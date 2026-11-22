"""Cash Flow master data — category / sub-category edit, delete and suspend.

The Cash Flow screen keeps three user-managed masters (``cash_flow_category``,
``cash_flow_subcategory`` and ``cash_flow_party``).  These tests drive the real
HTTP routes the *Master Data Management* panel posts to, plus the JSON quick-add
endpoints the Accounts expense form uses, and pin down the three rules the shop
needs:

* **Edit** a master in place.  Renaming must not touch history: every
  ``cash_flow_entry`` keeps pointing at the same row and simply shows the new
  name.  A rename can never steal another master's name (case-insensitive,
  including a disabled one), and a direction can only be set to in/out/both.
* **Delete** only a master that no history uses.  A category that is used —
  directly *or* only through one of its sub-categories, even by a voided row —
  must be refused with a message that names the way forward.  Deleting an
  unused category removes its unused sub-categories with it and leaves nothing
  behind that a later query can trip over.
* **Suspend** (disable) any master, used or not.  Suspension is reversible and
  lossless: the row, its direction and its notes survive, history keeps the
  name, and a suspended master is never silently brought back to life by a
  later entry or by the Add form.

The tests also cover what the UI can and cannot offer (the Delete button is
off when a category is used only via a sub-category) and that a user without
``can_view_cash_flow`` cannot reach any of it.
"""
from __future__ import annotations

from contextlib import contextmanager
import html as html_module
from pathlib import Path

from flask import template_rendered
from sqlalchemy import event
import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.services.cash_flow_svc import (
    CF_DIR_IN,
    CF_DIR_OUT,
    CF_SOURCE_LABELS,
    _cf_category_is_used,
    _cf_resolve_category,
    categories_for_direction,
    cf_used_category_ids,
    cf_used_subcategory_ids,
    delete_cf_category,
    delete_cf_subcategory,
    disable_cf_category,
    enable_cf_category,
    rename_cf_category,
    save_cf_category,
    save_cf_subcategory,
    save_manual_cash_flow_entry,
    subcategories_for_category,
)
from models import (
    Account,
    CashFlowCategory,
    CashFlowEntry,
    CashFlowEntryAudit,
    CashFlowParty,
    CashFlowSubcategory,
    User,
    db,
)

ADMIN = 'CashFlowAdmin'
ADMIN_PASSWORD = 'cash-flow-master-test-password'


# ---------------------------------------------------------------------------
# desk: isolated app + logged-in admin
# ---------------------------------------------------------------------------
@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'cash-flow-masters.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'isolated-cash-flow-masters-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', ADMIN)
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', ADMIN_PASSWORD)
    app = create_app({'TESTING': True, 'INSTANCE_PATH': tmp_path / 'instance',
                      'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
                      'SESSION_COOKIE_SECURE': False})
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    web = app.test_client()
    assert web.post('/login', data={'username': ADMIN,
                                    'password': ADMIN_PASSWORD}).status_code == 302
    return app, web


@contextmanager
def _views(app):
    views = {}

    def rendered(sender, template, context, **extra):
        views[template.name] = context

    with template_rendered.connected_to(rendered, app):
        yield views


def last_flash(web):
    """The last flash message, without the '— by <user>' suffix the hook adds."""
    with web.session_transaction() as sess:
        flashes = sess.get('_flashes') or []
    return (flashes[-1][1] if flashes else '').split(' — by ')[0]


def post_action(web, action, **data):
    data['action'] = action
    return web.post('/cash_flow', data=data)


# ---------------------------------------------------------------------------
# seeding helpers
# ---------------------------------------------------------------------------
def make_account(name='Main Cash', balance=500000.0):
    acc = Account(name=name, type='Cash', category='cash', account_type='company',
                  balance=balance, is_active=True)
    db.session.add(acc)
    db.session.flush()
    return acc


def make_category(name, direction='both', active=True, notes=None):
    cat = CashFlowCategory(name=name, direction=direction, is_active=active,
                           notes=notes)
    db.session.add(cat)
    db.session.flush()
    return cat


def make_subcategory(category, name, active=True):
    sub = CashFlowSubcategory(category_id=category.id, name=name, is_active=active)
    db.session.add(sub)
    db.session.flush()
    return sub


def make_entry(direction, amount, account, category=None, subcategory=None,
               note=None, is_void=False):
    entry = CashFlowEntry(
        direction=direction, amount=amount, account_id=account.id,
        category_id=category.id if category else None,
        subcategory_id=subcategory.id if subcategory else None,
        description=note or 'test row', date_posted=None, is_void=is_void,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


# ---------------------------------------------------------------------------
# ADD
# ---------------------------------------------------------------------------
def test_add_category_then_add_subcategory_under_it(desk):
    app, web = desk
    post_action(web, 'add_category', new_category_name='Shop Rent',
                new_category_direction='out', new_category_notes='monthly')
    assert 'Category created' in last_flash(web)

    with app.app_context():
        cat = CashFlowCategory.query.filter_by(name='Shop Rent').one()
        assert cat.direction == 'out'
        assert cat.is_active is True
        assert cat.notes == 'monthly'
        cat_id = cat.id

    post_action(web, 'add_subcategory', new_sub_category_id=str(cat_id),
                new_subcategory_name='November')
    assert 'Sub-category created' in last_flash(web)
    with app.app_context():
        sub = CashFlowSubcategory.query.filter_by(category_id=cat_id, name='November').one()
        assert sub.is_active is True


def test_subcategory_needs_an_existing_parent(desk):
    app, web = desk
    post_action(web, 'add_subcategory', new_subcategory_name='Orphan')
    assert 'Pick a parent category' in last_flash(web)
    with app.app_context():
        assert CashFlowSubcategory.query.count() == 0


def test_add_category_is_case_insensitive_and_trims(desk):
    """'  Shop Rent  ' must not become a second 'Shop Rent'."""
    app, web = desk
    post_action(web, 'add_category', new_category_name='Shop Rent', new_category_direction='out')
    post_action(web, 'add_category', new_category_name='  shop rent  ', new_category_direction='out')
    with app.app_context():
        assert CashFlowCategory.query.count() == 1
        assert CashFlowCategory.query.one().name == 'Shop Rent'


def test_blank_category_name_is_refused(desk):
    app, web = desk
    post_action(web, 'add_category', new_category_name='   ')
    assert 'Category name is required' in last_flash(web)
    with app.app_context():
        assert CashFlowCategory.query.count() == 0


def test_invalid_category_direction_is_refused(desk):
    app, web = desk
    post_action(web, 'add_category', new_category_name='Odd', new_category_direction='sideways')
    assert 'direction must be' in last_flash(web)
    with app.app_context():
        assert CashFlowCategory.query.count() == 0


# ---------------------------------------------------------------------------
# EDIT / RENAME
# ---------------------------------------------------------------------------
def test_rename_updates_the_row_and_keeps_every_history_entry_linked(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Shop Rent', direction='out')
        entry = make_entry('out', 1500, acc, category=cat, note='nov rent')
        acc_id, cat_id, entry_id = acc.id, cat.id, entry.id
        db.session.commit()

    post_action(web, 'rename_category', category_id=str(cat_id),
                category_name='Shop Rent (Nov)', category_direction='out',
                category_notes='renamed')
    assert 'Category updated' in last_flash(web)

    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        assert (cat.name, cat.direction, cat.notes) == ('Shop Rent (Nov)', 'out', 'renamed')
        entry = db.session.get(CashFlowEntry, entry_id)
        assert entry.category_id == cat_id          # same row, not folded into another
        assert entry.category.name == 'Shop Rent (Nov)'

    page = web.get('/cash_flow')
    assert page.status_code == 200
    assert b'Shop Rent (Nov)' in page.data


def test_rename_can_change_the_direction_and_both_forms_are_accepted(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Consulting', direction='in')
        cat_id = cat.id
        db.session.commit()

    for direction in ('out', 'both', CF_DIR_IN):
        post_action(web, 'rename_category', category_id=str(cat_id),
                    category_name='Consulting', category_direction=direction)
        with app.app_context():
            assert db.session.get(CashFlowCategory, cat_id).direction == direction


def test_rename_refuses_a_name_another_category_already_uses(desk):
    app, web = desk
    with app.app_context():
        make_category('Rent')
        utilities = make_category('Utilities')
        utilities_id = utilities.id
        db.session.commit()

    post_action(web, 'rename_category', category_id=str(utilities_id),
                category_name='RENT')
    assert 'Another category already uses that name' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, utilities_id).name == 'Utilities'


def test_rename_refuses_a_name_held_by_a_disabled_category(desk):
    """A suspended category still owns its name — reviving two rows would lie."""
    app, web = desk
    with app.app_context():
        make_category('Old Rent', active=False)
        live = make_category('Utilities')
        live_id = live.id
        db.session.commit()

    post_action(web, 'rename_category', category_id=str(live_id), category_name='Old Rent')
    assert 'Another category already uses that name' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, live_id).name == 'Utilities'


def test_rename_to_its_own_name_or_a_case_variant_is_allowed(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Shop Rent')
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'rename_category', category_id=str(cat_id), category_name='Shop Rent')
    assert 'Category updated' in last_flash(web)
    post_action(web, 'rename_category', category_id=str(cat_id), category_name='SHOP RENT')
    assert 'Category updated' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id).name == 'SHOP RENT'


def test_rename_with_a_blank_name_is_refused_and_changes_nothing(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Shop Rent', direction='out', notes='keep me')
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'rename_category', category_id=str(cat_id), category_name='   ',
                category_direction='in', category_notes='wiped')
    assert 'Category name is required' in last_flash(web)
    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        assert (cat.name, cat.direction, cat.notes) == ('Shop Rent', 'out', 'keep me')


def test_rename_with_an_unknown_direction_keeps_the_stored_one(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Shop Rent', direction='out')
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'rename_category', category_id=str(cat_id),
                category_name='Shop Rent', category_direction='sideways')
    assert 'Category updated' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id).direction == 'out'


def test_rename_unknown_category_is_refused_not_a_500(desk):
    app, web = desk
    resp = post_action(web, 'rename_category', category_id='99999', category_name='Ghost')
    assert resp.status_code == 302
    assert 'Category not found' in last_flash(web)


def test_rename_subcategory_updates_in_place_and_keeps_history(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        entry = make_entry('out', 700, acc, category=cat, subcategory=sub)
        sub_id, entry_id = sub.id, entry.id
        db.session.commit()

    post_action(web, 'rename_subcategory', subcategory_id=str(sub_id),
                subcategory_name='Diesel (PSO)', subcategory_notes='pso only')
    assert 'Sub-category updated' in last_flash(web)
    with app.app_context():
        sub = db.session.get(CashFlowSubcategory, sub_id)
        assert (sub.name, sub.notes) == ('Diesel (PSO)', 'pso only')
        entry = db.session.get(CashFlowEntry, entry_id)
        assert entry.subcategory_id == sub_id
        assert entry.subcategory.name == 'Diesel (PSO)'


def test_subcategory_names_are_unique_per_category_not_globally(desk):
    app, web = desk
    with app.app_context():
        fuel = make_category('Fuel')
        rent = make_category('Rent')
        make_subcategory(fuel, 'Maintenance')          # Fuel / Maintenance
        rent_sub = make_subcategory(rent, 'Maintenance')  # Rent / Maintenance — allowed
        fuel_id, rent_id, rent_sub_id = fuel.id, rent.id, rent_sub.id
        db.session.commit()

    # The same name under a *different* parent is fine ...
    post_action(web, 'add_subcategory', new_sub_category_id=str(rent_id),
                new_subcategory_name='Tyres')
    assert 'Sub-category created' in last_flash(web)

    # ... but not under the same parent.
    post_action(web, 'add_subcategory', new_sub_category_id=str(fuel_id),
                new_subcategory_name='Maintenance')
    assert 'already exists' in last_flash(web)
    with app.app_context():
        assert CashFlowSubcategory.query.filter_by(category_id=fuel_id).count() == 1

    # Renaming onto a sibling under the same parent is refused; the other
    # parent's identically named sub-category is untouched.
    with app.app_context():
        fuel_sub = CashFlowSubcategory.query.filter_by(category_id=fuel_id).one()
        make_subcategory(db.session.get(CashFlowCategory, fuel_id), 'Tyres')
        db.session.commit()
        fuel_sub_id = fuel_sub.id
    post_action(web, 'rename_subcategory', subcategory_id=str(fuel_sub_id),
                subcategory_name='Tyres')
    assert 'Another sub-category already uses that name' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowSubcategory, fuel_sub_id).name == 'Maintenance'
        assert db.session.get(CashFlowSubcategory, rent_sub_id).name == 'Maintenance'
        assert rent_id != fuel_id

    # ... while renaming to the *other* parent's name is fine.
    post_action(web, 'rename_subcategory', subcategory_id=str(rent_sub_id),
                subcategory_name='Maintenance')
    assert 'Sub-category updated' in last_flash(web)


# ---------------------------------------------------------------------------
# SUSPEND / RESTORE
# ---------------------------------------------------------------------------
def test_suspend_and_restore_a_category(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Shop Rent', direction='out', notes='monthly')
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'disable_category', category_id=str(cat_id))
    assert 'Category disabled' in last_flash(web)
    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        assert cat.is_active is False
        assert (cat.direction, cat.notes) == ('out', 'monthly')   # suspension is lossless

    post_action(web, 'enable_category', category_id=str(cat_id))
    assert 'Category enabled' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id).is_active is True


def test_suspending_a_used_category_keeps_the_history_visible(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Shop Rent', direction='out')
        entry = make_entry('out', 1500, acc, category=cat)
        cat_id, entry_id = cat.id, entry.id
        db.session.commit()

    post_action(web, 'disable_category', category_id=str(cat_id))
    assert 'Category disabled' in last_flash(web)

    with app.app_context():
        entry = db.session.get(CashFlowEntry, entry_id)
        assert entry.category_id == cat_id
        assert entry.category.name == 'Shop Rent'
        assert entry.category.is_active is False

    page = web.get('/cash_flow')
    assert page.status_code == 200
    assert b'Shop Rent' in page.data          # history keeps the name
    assert b'Disabled' in page.data


def test_suspended_category_is_not_offered_for_new_entries(desk):
    app, web = desk
    with app.app_context():
        make_category('Old Rent', direction='out', active=False)
        make_category('Shop Rent', direction='out')
        db.session.commit()

    meta = web.get('/cash_flow/meta/categories?direction=out').get_json()
    assert {c['name'] for c in meta['categories']} == {'Shop Rent'}
    with app.app_context():
        assert [c.name for c in categories_for_direction('out')] == ['Shop Rent']
        assert 'Old Rent' in {c.name for c in categories_for_direction('out', include_inactive=True)}
        assert [c.name for c in categories_for_direction('in')] == []


def test_a_suspended_category_is_never_silently_revived_by_an_entry(desk):
    """Typing a suspended category's name must not un-suspend it.

    The suspension is the shop's decision; a data-entry box may not reverse it
    (and must not quietly attach the row to the suspended master either).
    """
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out', active=False, notes='do not use')
        cat_id, acc_id = cat.id, acc.id
        db.session.commit()

    post_action(web, 'record_movement', direction='out', amount='700',
                cash_account_id=str(acc_id), category_name='Fuel',
                description='diesel refill')

    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        assert cat.is_active is False, 'the entry silently un-suspended the category'
        assert cat.notes == 'do not use'
        assert CashFlowEntry.query.count() == 0, 'the entry was posted on a suspended category'


def test_a_suspended_subcategory_is_not_silently_revived_by_an_entry(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel', active=False)
        cat_id, sub_id, acc_id = cat.id, sub.id, acc.id
        db.session.commit()

    post_action(web, 'record_movement', direction='out', amount='700',
                cash_account_id=str(acc_id), category_id=str(cat_id),
                subcategory_name='Diesel', description='diesel refill')

    with app.app_context():
        assert db.session.get(CashFlowSubcategory, sub_id).is_active is False
        assert CashFlowEntry.query.count() == 0


def test_suspending_a_subcategory_keeps_its_category_untouched(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        cat_id, sub_id = cat.id, sub.id
        db.session.commit()

    post_action(web, 'disable_subcategory', subcategory_id=str(sub_id))
    assert 'Sub-category disabled' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowSubcategory, sub_id).is_active is False
        assert db.session.get(CashFlowCategory, cat_id).is_active is True
        assert subcategories_for_category(cat_id) == []
        assert [s.name for s in subcategories_for_category(cat_id, include_inactive=True)] == ['Diesel']

    post_action(web, 'enable_subcategory', subcategory_id=str(sub_id))
    assert 'Sub-category enabled' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowSubcategory, sub_id).is_active is True


def test_suspend_and_restore_unknown_rows_report_instead_of_crashing(desk):
    app, web = desk
    for action, field in (('disable_category', 'category_id'),
                          ('enable_category', 'category_id'),
                          ('disable_subcategory', 'subcategory_id'),
                          ('enable_subcategory', 'subcategory_id')):
        resp = post_action(web, action, **{field: '99999'})
        assert resp.status_code == 302
        assert 'not found' in last_flash(web)


def test_missing_ids_report_instead_of_raising_a_server_error(desk):
    """A POST without its id must not reach db.session.get(None) (SAWarning)."""
    app, web = desk
    for action, field in (('disable_category', 'category_id'),
                          ('enable_category', 'category_id'),
                          ('rename_category', 'category_id'),
                          ('delete_category', 'category_id'),
                          ('disable_subcategory', 'subcategory_id'),
                          ('enable_subcategory', 'subcategory_id'),
                          ('rename_subcategory', 'subcategory_id'),
                          ('delete_subcategory', 'subcategory_id')):
        data = {'category_name': 'X', 'subcategory_name': 'Y'}
        resp = post_action(web, action, **data)
        assert resp.status_code == 302
        assert 'not found' in last_flash(web), action


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------
def test_delete_an_unused_category_and_its_unused_subcategories(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Temp')
        make_subcategory(cat, 'one')
        make_subcategory(cat, 'two')
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'delete_category', category_id=str(cat_id))
    assert 'Unused category deleted' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id) is None
        assert CashFlowSubcategory.query.filter_by(category_id=cat_id).count() == 0


def test_deleting_a_category_whose_subcategories_are_already_loaded_works(desk):
    """The old delete issued a bulk DELETE while the relationship was loaded.

    After the delete the two halves disagreed: Postgres would refuse the FK and
    the in-session objects still believed their parent existed.
    """
    app, web = desk
    with app.app_context():
        cat = make_category('Loaded Parent')
        make_subcategory(cat, 'a')
        make_subcategory(cat, 'b')
        cat_id = cat.id
        db.session.commit()
        loaded = db.session.get(CashFlowCategory, cat_id)
        assert [s.name for s in loaded.subcategories] == ['a', 'b']

    post_action(web, 'delete_category', category_id=str(cat_id))
    assert 'Unused category deleted' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id) is None
        assert CashFlowSubcategory.query.filter_by(category_id=cat_id).count() == 0


def test_delete_a_category_used_directly_is_refused(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Shop Rent')
        make_entry('out', 1500, acc, category=cat)
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'delete_category', category_id=str(cat_id))
    message = last_flash(web)
    assert 'Cannot delete' in message
    assert 'disable' in message.lower()
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id) is not None
        assert CashFlowEntry.query.count() == 1


def test_delete_a_category_used_only_through_a_subcategory_is_refused(desk):
    """The FK on the entry points at the sub-category, but the category is used."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel')
        sub = make_subcategory(cat, 'Diesel')
        make_entry('out', 700, acc, category=None, subcategory=sub)
        cat_id, sub_id = cat.id, sub.id
        db.session.commit()
        assert _cf_category_is_used(cat_id) is True

    post_action(web, 'delete_category', category_id=str(cat_id))
    message = last_flash(web)
    assert 'still used by 1 cash-flow transaction(s)' in message, message
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id) is not None
        assert db.session.get(CashFlowSubcategory, sub_id) is not None


def test_delete_a_category_used_only_by_a_voided_row_is_refused(desk):
    """A voided document is still a document; its classification stays readable."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Shop Rent')
        make_entry('out', 1500, acc, category=cat, is_void=True)
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'delete_category', category_id=str(cat_id))
    assert 'Cannot delete' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id) is not None
        assert db.session.get(CashFlowEntry, 1).category.name == 'Shop Rent'


def test_the_page_offers_delete_only_for_unused_categories(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        used = make_category('Used Direct')
        used_sub = make_category('Used Via Sub')
        sub = make_subcategory(used_sub, 'child')
        free = make_category('Free')
        make_entry('out', 10, acc, category=used)
        make_entry('out', 10, acc, subcategory=sub)
        used_id, via_sub_id, free_id = used.id, used_sub.id, free.id
        db.session.commit()

    page = web.get('/cash_flow')
    assert page.status_code == 200
    html = page.data.decode()
    for cat_id in (used_id, via_sub_id):
        row = html.split(f'name="category_id" value="{cat_id}"', 1)[1].split('</tr>', 1)[0]
        assert 'value="delete_category"' not in row
        assert 'Disable instead of deleting' in row
        assert 'In use — disable instead' in row
    row = html.split(f'name="category_id" value="{free_id}"', 1)[1].split('</tr>', 1)[0]
    assert 'value="delete_category"' in row


def test_delete_an_unused_subcategory(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel')
        sub = make_subcategory(cat, 'Diesel')
        cat_id, sub_id = cat.id, sub.id
        db.session.commit()

    post_action(web, 'delete_subcategory', subcategory_id=str(sub_id))
    assert 'Unused sub-category deleted' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowSubcategory, sub_id) is None
        assert db.session.get(CashFlowCategory, cat_id) is not None


def test_delete_a_used_subcategory_is_refused(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel')
        sub = make_subcategory(cat, 'Diesel')
        make_entry('out', 700, acc, category=cat, subcategory=sub)
        sub_id = sub.id
        db.session.commit()

    post_action(web, 'delete_subcategory', subcategory_id=str(sub_id))
    assert 'Cannot delete' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowSubcategory, sub_id) is not None


def test_delete_unknown_rows_report_instead_of_crashing(desk):
    app, web = desk
    post_action(web, 'delete_category', category_id='99999')
    assert 'Category not found' in last_flash(web)
    post_action(web, 'delete_subcategory', subcategory_id='99999')
    assert 'Sub-category not found' in last_flash(web)


# ---------------------------------------------------------------------------
# Creation paths that must respect suspension
# ---------------------------------------------------------------------------
def test_add_category_reports_instead_of_silently_overwriting_an_existing_one(desk):
    """Re-adding 'Fuel' must not flip its direction or replace its notes."""
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel', direction='out', notes='admin notes')
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'add_category', new_category_name='Fuel',
                new_category_direction='in', new_category_notes='oops')
    message = last_flash(web)
    assert 'already exists' in message.lower()
    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        assert cat.direction == 'out'
        assert cat.notes == 'admin notes'


def test_add_category_with_a_suspended_name_asks_before_reviving_it(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel', direction='out', active=False)
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'add_category', new_category_name='Fuel', new_category_direction='in')
    message = last_flash(web)
    assert 'disabled' in message.lower() or 'suspended' in message.lower()
    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        assert cat.is_active is False
        assert cat.direction == 'out'


def test_add_subcategory_under_a_suspended_parent_is_refused(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel', active=False)
        cat_id = cat.id
        db.session.commit()

    post_action(web, 'add_subcategory', new_sub_category_id=str(cat_id),
                new_subcategory_name='Diesel')
    assert 'disabled' in last_flash(web).lower() or 'suspended' in last_flash(web).lower()
    with app.app_context():
        assert CashFlowSubcategory.query.count() == 0


# ---------------------------------------------------------------------------
# JSON quick-add endpoints (Accounts expense form)
# ---------------------------------------------------------------------------
def test_meta_categories_quick_add_never_revives_a_suspended_category(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel', direction='out', active=False)
        cat_id = cat.id
        db.session.commit()

    resp = web.post('/cash_flow/meta/categories', json={'name': 'Fuel', 'direction': 'out'})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['ok'] is False
    assert 'disabled' in body['error'].lower() or 'suspended' in body['error'].lower()
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id).is_active is False

    # ... and an existing active category is never silently re-pointed.  The
    # quick-add is also the expense form's dropdown loader, so an existing row
    # is returned (created=False) — reuse is the point there; silently
    # rewriting the master is not.
    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        cat.is_active = True
        cat.direction = 'out'
        db.session.commit()
    resp = web.post('/cash_flow/meta/categories',
                    json={'name': 'Fuel', 'direction': 'in', 'notes': 'oops'})
    assert resp.status_code == 200
    assert resp.get_json()['created'] is False
    assert resp.get_json()['category']['direction'] == 'out'
    with app.app_context():
        cat = db.session.get(CashFlowCategory, cat_id)
        assert cat.direction == 'out'
        assert cat.notes is None


def test_meta_subcategories_quick_add_refuses_a_suspended_parent(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel', active=False)
        cat_id = cat.id
        db.session.commit()

    resp = web.post('/cash_flow/meta/subcategories',
                    json={'category_id': cat_id, 'name': 'Diesel'})
    assert resp.status_code == 400
    with app.app_context():
        assert CashFlowSubcategory.query.count() == 0


def test_meta_categories_lists_only_active_rows_and_validates_input(desk):
    app, web = desk
    with app.app_context():
        make_category('Active In', direction='in')
        make_category('Suspended In', direction='in', active=False)
        make_category('Out Only', direction='out')
        db.session.commit()

    body = web.get('/cash_flow/meta/categories?direction=in').get_json()
    assert {c['name'] for c in body['categories']} == {'Active In'}

    resp = web.post('/cash_flow/meta/categories', json={'name': ''})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


# ---------------------------------------------------------------------------
# Service-level rules (used by Accounts, not only by this page)
# ---------------------------------------------------------------------------
def test_resolver_rejects_a_suspended_category_instead_of_reviving_it(desk):
    app, web = desk
    with app.app_context():
        make_category('Fuel', direction='out', active=False)
        db.session.commit()
        with pytest.raises(ValueError):
            _cf_resolve_category(CF_DIR_OUT, None, 'Fuel', required=True,
                                 create_if_missing=True)
        assert CashFlowCategory.query.one().is_active is False


def test_service_helpers_cover_the_same_rules(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        make_entry('out', 700, acc, category=cat, subcategory=sub)
        cat_id, sub_id = cat.id, sub.id
        db.session.commit()

        assert cat_id in cf_used_category_ids()
        assert sub_id in cf_used_subcategory_ids()
        with pytest.raises(ValueError):
            delete_cf_category(cat_id)
        with pytest.raises(ValueError):
            delete_cf_subcategory(sub_id)

        disable_cf_category(cat_id)
        assert db.session.get(CashFlowCategory, cat_id).is_active is False
        enable_cf_category(cat_id)
        assert db.session.get(CashFlowCategory, cat_id).is_active is True

        rename_cf_category(cat_id, 'Fuel & Oil', direction='both')
        assert db.session.get(CashFlowCategory, cat_id).name == 'Fuel & Oil'
        assert db.session.get(CashFlowCategory, cat_id).direction == 'both'


def test_accounts_expense_path_refuses_a_suspended_category(desk):
    """Accounts posts expenses with create_missing=False; suspension must hold."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out', active=False)
        acc_id, cat_id = acc.id, cat.id
        db.session.commit()

        with pytest.raises(ValueError):
            save_manual_cash_flow_entry(
                direction='out', amount=500, account_id=acc_id, category_id=cat_id,
                description='diesel', create_missing=False,
            )
        assert CashFlowEntry.query.count() == 0
        assert db.session.get(CashFlowCategory, cat_id).is_active is False


# ---------------------------------------------------------------------------
# Parties share the same "used ⇒ disable, unused ⇒ delete" rule
# ---------------------------------------------------------------------------
def test_party_edit_suspend_and_delete_follow_the_same_rules(desk):
    app, web = desk
    with app.app_context():
        party = CashFlowParty(name='Landlord', party_type='person', is_active=True)
        db.session.add(party)
        db.session.flush()
        acc = make_account()
        cat = make_category('Rent', direction='out')
        entry = make_entry('out', 3000, acc, category=cat)
        entry.party_id = party.id
        party_id, entry_id = party.id, entry.id
        db.session.commit()

    post_action(web, 'update_party', party_id=str(party_id), party_name='Landlord (Haji)',
                party_type='person', party_note='rent only')
    assert 'Party updated' in last_flash(web)
    with app.app_context():
        party = db.session.get(CashFlowParty, party_id)
        assert (party.name, party.note) == ('Landlord (Haji)', 'rent only')
        assert db.session.get(CashFlowEntry, entry_id).party_id == party_id

    post_action(web, 'disable_party', party_id=str(party_id))
    assert 'Party disabled' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowParty, party_id).is_active is False

    post_action(web, 'delete_party', party_id=str(party_id))
    assert 'Cannot delete' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowParty, party_id) is not None


def test_unused_party_can_be_deleted(desk):
    app, web = desk
    with app.app_context():
        party = CashFlowParty(name='Temp', party_type='person', is_active=True)
        db.session.add(party)
        db.session.commit()
        party_id = party.id

    post_action(web, 'delete_party', party_id=str(party_id))
    assert 'Unused party deleted' in last_flash(web)
    with app.app_context():
        assert db.session.get(CashFlowParty, party_id) is None


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def test_every_master_data_route_needs_can_view_cash_flow(desk):
    app, web = desk
    with app.app_context():
        cat = make_category('Fuel')
        cat_id = cat.id
        staff = User(username='CfStaff', password_hash=generate_password_hash('staff-pass-1'),
                     role='staff', status='active', can_view_cash_flow=False)
        db.session.add(staff)
        db.session.commit()

    other = app.test_client()
    assert other.post('/login', data={'username': 'CfStaff',
                                      'password': 'staff-pass-1'}).status_code == 302

    assert other.get('/cash_flow').status_code in (302, 403)
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id).name == 'Fuel'

    for action, field in (('add_category', 'new_category_name'),
                          ('rename_category', 'category_id'),
                          ('disable_category', 'category_id'),
                          ('delete_category', 'category_id')):
        data = {'action': action, field: str(cat_id) if field == 'category_id' else 'Hijack'}
        resp = other.post('/cash_flow', data=data)
        assert resp.status_code in (302, 403)
    with app.app_context():
        assert CashFlowCategory.query.count() == 1
        assert CashFlowCategory.query.one().name == 'Fuel'
        assert CashFlowCategory.query.one().is_active is True


def test_page_renders_master_panel_with_expected_controls(desk):
    app, web = desk
    with app.app_context():
        make_category('Shop Rent', direction='out')
        db.session.commit()

    page = web.get('/cash_flow')
    assert page.status_code == 200
    html = page.data.decode()
    for needle in ('Master Data Management', 'Add Category', 'Add Sub-category',
                   'value="rename_category"', 'value="disable_category"',
                   'value="add_category"'):
        assert needle in html


def test_shared_submit_lock_keeps_clicked_action_button_enabled():
    """Disabling the clicked button drops its name/value from a browser POST."""
    layout = (Path(__file__).resolve().parents[1] / 'templates' / 'layout.html').read_text()
    assert 'function lockForm(form, lock, submitter)' in layout
    assert 'const submitter = e.submitter || document.activeElement;' in layout
    assert 'btn.disabled = !!lock && btn !== submitter;' in layout
    assert layout.count('lockForm(form, true, submitter)') >= 2


def test_master_data_actions_redirect_back_to_the_master_panel(desk):
    app, web = desk
    with app.app_context():
        category = make_category('Shop Rent', direction='out')
        category_id = category.id
        db.session.commit()

    response = web.post('/cash_flow', data={
        'action': 'rename_category',
        'category_id': str(category_id),
        'category_name': 'Office Rent',
        'category_direction': 'out',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert response.history
    assert response.history[0].headers['Location'].endswith('#tab-master')
    assert 'Category updated. Historical rows stay linked to the same category.' in response.get_data(as_text=True)


def test_names_are_html_escaped_on_the_page(desk):
    app, web = desk
    post_action(web, 'add_category', new_category_name='<script>alert(1)</script>')
    page = web.get('/cash_flow')
    assert b'<script>alert(1)</script>' not in page.data
    assert b'&lt;script&gt;alert(1)&lt;/script&gt;' in page.data


def test_source_labels_still_cover_the_manual_bucket(desk):
    """Guard the label table the master panel's direction select relies on."""
    assert CF_SOURCE_LABELS['MANUAL_CASH_FLOW'] == 'Manual'
    assert CF_DIR_IN in ('in',) and CF_DIR_OUT == 'out'


# ---------------------------------------------------------------------------
# Real-time propagation to the Accounts expense form
# ---------------------------------------------------------------------------
# Accounts does not keep its own copy of the masters: the expense form reads
# cash_flow_category / cash_flow_subcategory / cash_flow_party on every render.
# These tests pin that the Cash Flow panel's Edit / Disable / Restore / Delete
# is visible on the very next Accounts page load, and that a disabled id that is
# submitted anyway (a stale browser tab) is refused by the server — the form's
# dropdown is a convenience, never the gate.
def _expense_option_values(app, html, select_id):
    """Option (value, label) pairs inside one <select id="..."> block."""
    block = html.split(f'id="{select_id}"', 1)[1].split('</select>', 1)[0]
    pairs = []
    for chunk in block.split('<option ')[1:]:
        value = chunk.split('value="', 1)[1].split('"', 1)[0]
        label = chunk.split('>', 1)[1].split('</option>', 1)[0].strip()
        pairs.append((value, html_module.unescape(label)))
    return pairs


def _accounts_form(app, web):
    resp = web.get('/accounts/')
    assert resp.status_code == 200
    return resp.data.decode()


def test_accounts_expense_form_reflects_category_edits_suspends_and_deletes_in_real_time(desk):
    app, web = desk
    with app.app_context():
        fuel = make_category('Fuel', direction='out')
        misc = make_category('Misc', direction='out')
        fuel_sub = make_subcategory(fuel, 'Diesel')
        misc_sub = make_subcategory(misc, 'Other')
        db.session.add(CashFlowParty(name='Landlord', party_type='person', is_active=True))
        fuel_id, misc_id = fuel.id, misc.id
        fuel_sub_id, misc_sub_id = fuel_sub.id, misc_sub.id
        db.session.commit()

    # Baseline: both categories and both sub-categories are offered.
    html = _accounts_form(app, web)
    cats = dict(_expense_option_values(app, html, 'expense_category_id'))
    subs = dict(_expense_option_values(app, html, 'expense_subcategory_id'))
    assert cats[str(fuel_id)] == 'Fuel' and cats[str(misc_id)] == 'Misc'
    assert subs[str(fuel_sub_id)] == 'Diesel' and subs[str(misc_sub_id)] == 'Other'

    # 1. EDIT — the next page load shows the new name, same id.
    post_action(web, 'rename_category', category_id=str(fuel_id),
                category_name='Fuel & Oil', category_direction='out')
    html = _accounts_form(app, web)
    assert dict(_expense_option_values(app, html, 'expense_category_id'))[str(fuel_id)] == 'Fuel & Oil'

    post_action(web, 'rename_subcategory', subcategory_id=str(fuel_sub_id),
                subcategory_name='Diesel (PSO)')
    html = _accounts_form(app, web)
    assert dict(_expense_option_values(app, html, 'expense_subcategory_id'))[str(fuel_sub_id)] == 'Diesel (PSO)'

    # 2. SUSPEND a category — it and its sub-category disappear immediately.
    post_action(web, 'disable_category', category_id=str(fuel_id))
    html = _accounts_form(app, web)
    cats = dict(_expense_option_values(app, html, 'expense_category_id'))
    subs = dict(_expense_option_values(app, html, 'expense_subcategory_id'))
    assert str(fuel_id) not in cats
    assert str(fuel_sub_id) not in subs, 'sub-category of a suspended category was still offered'
    assert str(misc_id) in cats and str(misc_sub_id) in subs

    # 3. SUSPEND only the sub-category — its parent stays selectable.
    post_action(web, 'disable_subcategory', subcategory_id=str(misc_sub_id))
    html = _accounts_form(app, web)
    subs = dict(_expense_option_values(app, html, 'expense_subcategory_id'))
    assert str(misc_sub_id) not in subs
    assert str(misc_id) in dict(_expense_option_values(app, html, 'expense_category_id'))

    # 4. RESTORE — both come back on the next load.
    post_action(web, 'restore_category', category_id=str(fuel_id))
    post_action(web, 'restore_subcategory', subcategory_id=str(fuel_sub_id))
    post_action(web, 'restore_subcategory', subcategory_id=str(misc_sub_id))
    html = _accounts_form(app, web)
    assert str(fuel_id) in dict(_expense_option_values(app, html, 'expense_category_id'))
    assert str(fuel_sub_id) in dict(_expense_option_values(app, html, 'expense_subcategory_id'))
    assert str(misc_sub_id) in dict(_expense_option_values(app, html, 'expense_subcategory_id'))

    # 5. SUSPEND a party — it leaves the recipient dropdown.
    with app.app_context():
        party_id = CashFlowParty.query.one().id
    post_action(web, 'disable_party', party_id=str(party_id))
    html = _accounts_form(app, web)
    assert str(party_id) not in dict(_expense_option_values(app, html, 'expense_party_id'))
    post_action(web, 'restore_party', party_id=str(party_id))
    html = _accounts_form(app, web)
    assert dict(_expense_option_values(app, html, 'expense_party_id'))[str(party_id)].startswith('Landlord')

    # 6. DELETE an unused category — gone from the form on the next load.
    post_action(web, 'delete_category', category_id=str(misc_id))
    html = _accounts_form(app, web)
    assert str(misc_id) not in dict(_expense_option_values(app, html, 'expense_category_id'))
    assert str(misc_sub_id) not in dict(_expense_option_values(app, html, 'expense_subcategory_id'))


def test_accounts_refuses_a_suspended_category_submitted_by_a_stale_tab(desk):
    """The dropdown is convenience; the server must refuse on its own.

    A tab left open while the category was suspended still posts the old id.
    The expense must not be posted, and the master must stay suspended.
    """
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        party = CashFlowParty(name='Landlord', party_type='person', is_active=True)
        db.session.add(party)
        db.session.commit()
        acc_id, cat_id, sub_id, party_id = acc.id, cat.id, sub.id, party.id

    post_action(web, 'disable_category', category_id=str(cat_id))

    resp = web.post('/accounts/transactions/new', data={
        'tx_mode': 'pay', 'pay_target': 'personal_expense',
        'target_label': 'diesel refill', 'method': 'Cash',
        'amount': '750', 'pay_from_account_id': str(acc_id),
        'expense_category_id': str(cat_id), 'expense_party_id': str(party_id),
    })
    assert resp.status_code in (200, 302)          # reported, not a 500
    assert 'missing or inactive' in last_flash(web), last_flash(web)
    with app.app_context():
        assert CashFlowEntry.query.count() == 0, 'an expense was posted on a suspended category'
        assert db.session.get(CashFlowCategory, cat_id).is_active is False
        assert db.session.get(CashFlowSubcategory, sub_id).is_active is True
        assert db.session.get(Account, acc_id).balance == 500000.0


def test_accounts_refuses_a_suspended_subcategory_submitted_by_a_stale_tab(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        party = CashFlowParty(name='Landlord', party_type='person', is_active=True)
        db.session.add(party)
        db.session.commit()
        acc_id, cat_id, sub_id, party_id = acc.id, cat.id, sub.id, party.id

    post_action(web, 'disable_subcategory', subcategory_id=str(sub_id))

    web.post('/accounts/transactions/new', data={
        'tx_mode': 'pay', 'pay_target': 'personal_expense',
        'target_label': 'diesel refill', 'method': 'Cash',
        'amount': '750', 'pay_from_account_id': str(acc_id),
        'expense_category_id': str(cat_id), 'expense_subcategory_id': str(sub_id),
        'expense_party_id': str(party_id),
    })
    flash_text = last_flash(web)
    assert 'sub-category' in flash_text.lower() and 'disabled' in flash_text.lower(), flash_text
    with app.app_context():
        assert CashFlowEntry.query.count() == 0, 'an expense was posted on a suspended sub-category'
        assert db.session.get(CashFlowSubcategory, sub_id).is_active is False
        assert db.session.get(Account, acc_id).balance == 500000.0


def test_accounts_refuses_a_suspended_party_submitted_by_a_stale_tab(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        party = CashFlowParty(name='Landlord', party_type='person', is_active=True)
        db.session.add(party)
        db.session.commit()
        acc_id, cat_id, party_id = acc.id, cat.id, party.id

    post_action(web, 'disable_party', party_id=str(party_id))

    web.post('/accounts/transactions/new', data={
        'tx_mode': 'pay', 'pay_target': 'personal_expense',
        'target_label': 'rent', 'method': 'Cash',
        'amount': '750', 'pay_from_account_id': str(acc_id),
        'expense_category_id': str(cat_id), 'expense_party_id': str(party_id),
    })
    flash_text = last_flash(web)
    assert 'party' in flash_text.lower(), flash_text
    with app.app_context():
        assert CashFlowEntry.query.count() == 0, 'an expense was posted on a suspended party'
        assert db.session.get(CashFlowParty, party_id).is_active is False
        assert db.session.get(Account, acc_id).balance == 500000.0


def test_accounts_expense_with_active_masters_still_posts_normally(desk):
    """The guard must not break the happy path after a suspend/restore cycle."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        party = CashFlowParty(name='Landlord', party_type='person', is_active=True)
        db.session.add(party)
        db.session.commit()
        acc_id, cat_id, sub_id, party_id = acc.id, cat.id, sub.id, party.id

    # Suspend then restore: the rows must be usable again.
    post_action(web, 'disable_category', category_id=str(cat_id))
    post_action(web, 'restore_category', category_id=str(cat_id))

    resp = web.post('/accounts/transactions/new', data={
        'tx_mode': 'pay', 'pay_target': 'personal_expense',
        'target_label': 'diesel refill', 'method': 'Cash',
        'amount': '750', 'pay_from_account_id': str(acc_id),
        'expense_category_id': str(cat_id), 'expense_subcategory_id': str(sub_id),
        'expense_party_id': str(party_id),
    })
    assert resp.status_code in (200, 302)
    with app.app_context():
        entry = CashFlowEntry.query.one()
        assert (entry.direction, entry.amount) == ('out', 750.0)
        assert entry.category_id == cat_id
        assert entry.subcategory_id == sub_id
        assert entry.party_id == party_id
        assert db.session.get(Account, acc_id).balance == 500000.0 - 750.0


# ---------------------------------------------------------------------------
# Suspension must not freeze history
# ---------------------------------------------------------------------------
def test_an_old_expense_stays_editable_after_its_category_is_suspended(desk):
    """Suspending a master retires it for *new* entries only.

    Accounts edits the ledger row behind an expense and re-resolves its
    classification with ``create_missing=False``.  If a suspended master made
    that resolution fail, correcting a typo in an old expense would be refused
    — suspension would silently freeze history.  The entry's *own* category and
    sub-category stay accepted; anything else suspended does not.
    """
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        party = CashFlowParty(name='Landlord', party_type='person', is_active=True)
        db.session.add(party)
        db.session.commit()
        acc_id, cat_id, sub_id, party_id = acc.id, cat.id, sub.id, party.id

    # Book a normal expense, then retire the category and its sub-category.
    resp = web.post('/accounts/transactions/new', data={
        'tx_mode': 'pay', 'pay_target': 'personal_expense',
        'target_label': 'diesel refil', 'method': 'Cash',
        'amount': '750', 'pay_from_account_id': str(acc_id),
        'expense_category_id': str(cat_id), 'expense_subcategory_id': str(sub_id),
        'expense_party_id': str(party_id),
    })
    assert resp.status_code in (200, 302)
    with app.app_context():
        entry_id = CashFlowEntry.query.one().id
        tx_id = db.session.get(CashFlowEntry, entry_id).account_tx_id

    post_action(web, 'disable_subcategory', subcategory_id=str(sub_id))
    post_action(web, 'disable_category', category_id=str(cat_id))

    # Fix the typo in the description — the edit must go through.
    resp = web.post(f'/accounts/transactions/{tx_id}/edit', data={
        'description': 'diesel refill', 'amount': '750',
        'from_account_id': str(acc_id),
    })
    assert resp.status_code in (200, 302)
    with app.app_context():
        entry = db.session.get(CashFlowEntry, entry_id)
        assert entry.description == 'diesel refill', last_flash(web)
        assert entry.category_id == cat_id and entry.subcategory_id == sub_id
        assert db.session.get(Account, acc_id).balance == 500000.0 - 750.0
        # The masters themselves stay suspended — an edit is not a restore.
        assert db.session.get(CashFlowCategory, cat_id).is_active is False
        assert db.session.get(CashFlowSubcategory, sub_id).is_active is False


def test_an_old_expense_cannot_be_moved_to_a_different_suspended_category(desk):
    """Only the entry's own suspended master is tolerated — not any suspended one."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        old_cat = make_category('Fuel', direction='out')
        retired = make_category('Retired', direction='out', active=False)
        db.session.commit()
        acc_id, old_cat_id, retired_id = acc.id, old_cat.id, retired.id
        with pytest.raises(ValueError):
            save_manual_cash_flow_entry(
                direction='out', amount=100, account_id=acc_id,
                category_id=retired_id, description='x', create_missing=False,
            )
        assert CashFlowEntry.query.count() == 0
        # ... and naming it (rather than its id) gets the actionable message.
        with pytest.raises(ValueError, match='disabled'):
            save_manual_cash_flow_entry(
                direction='out', amount=100, account_id=acc_id,
                category_name='Retired', description='x', create_missing=True,
            )
        assert CashFlowEntry.query.count() == 0
        assert db.session.get(CashFlowCategory, retired_id).is_active is False
        assert db.session.get(CashFlowCategory, old_cat_id).is_active is True


# ---------------------------------------------------------------------------
# "Remove all its entries, then delete it" — the lifecycle a shop actually runs
# ---------------------------------------------------------------------------
# Reporter's sequence: create a category, post transactions in it, suspend it,
# edit it, empty it, then delete it.  Emptying it by *voiding* every row never
# made the delete possible, because a void keeps the row (and its category_id)
# by design — and the operator only had a generic "data error" to go on.
def test_voiding_a_categories_transactions_does_not_make_it_deletable(desk):
    """A void is not a delete: the refusal must be readable, not the data-error flash."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Transport', direction='out')
        sub = make_subcategory(cat, 'Loader')
        entry = make_entry('out', 1500, acc, category=cat, subcategory=sub)
        cat_id, entry_id = cat.id, entry.id
        db.session.commit()

    post_action(web, 'disable_category', category_id=str(cat_id))
    post_action(web, 'void_entry', entry_id=str(entry_id), void_reason='cleanup')
    assert 'voided' in last_flash(web)

    post_action(web, 'delete_category', category_id=str(cat_id))
    message = last_flash(web)
    assert 'Cannot delete' in message
    assert 'voided rows still count as history' in message.lower()
    assert 'data error' not in message.lower(), 'the readable refusal was sanitised away'
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id) is not None


def test_remove_entries_and_delete_empties_the_category_in_one_step(desk):
    """The full reporter lifecycle, ending with the category really gone."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Transport', direction='out')
        sub = make_subcategory(cat, 'Loader')
        cat_id, sub_id, acc_id = cat.id, sub.id, acc.id
        db.session.commit()
        # Real entries (with their account movement), so the assertions below
        # can see the purge reverse exactly the money it removes.
        first, _ = save_manual_cash_flow_entry(
            direction='out', amount=1500, account_id=acc_id,
            category_id=cat_id, subcategory_id=sub_id, description='loader')
        second, _ = save_manual_cash_flow_entry(
            direction='out', amount=2500, account_id=acc_id,
            category_id=cat_id, description='toll')
        live_id, void_id = first.id, second.id
        db.session.commit()

    # suspend + edit, exactly like the report
    post_action(web, 'disable_category', category_id=str(cat_id))
    post_action(web, 'rename_category', category_id=str(cat_id),
                category_name='Transport (edited)', category_direction='out',
                category_notes='renamed while suspended')
    assert 'Category updated' in last_flash(web)

    # one of the two rows is voided first (the "remove the entries" attempt)
    post_action(web, 'void_entry', entry_id=str(void_id), void_reason='cleanup')
    with app.app_context():
        balance_after_spend = db.session.get(Account, acc_id).balance
        assert balance_after_spend == 500000.0 - 4000.0 + 2500.0, 'the void reversed one row'

    # the offered button empties the category and deletes it
    post_action(web, 'purge_category_entries', category_id=str(cat_id))
    message = last_flash(web)
    assert 'Category deleted' in message, message
    assert 'data error' not in message.lower()
    with app.app_context():
        assert db.session.get(CashFlowCategory, cat_id) is None
        assert db.session.get(CashFlowSubcategory, sub_id) is None
        assert CashFlowEntry.query.filter(
            CashFlowEntry.id.in_([live_id, void_id])).count() == 0
        assert CashFlowEntryAudit.query.filter(
            CashFlowEntryAudit.entry_id.in_([live_id, void_id])).count() == 0
        # The ledger rows those entries owned are gone too: no orphan money
        # movement may survive the document it described.
        from models import AccountTransaction
        assert AccountTransaction.query.filter(AccountTransaction.source_type == 'CashFlowEntry',
                                              AccountTransaction.source_id.in_([live_id, void_id])
                                              ).count() == 0
        # The live row's money effect was reversed on the way out.
        assert db.session.get(Account, acc_id).balance == 500000.0


def test_remove_entries_and_delete_is_not_the_data_error_flash_on_a_grn_party(desk):
    """A party used only by a GRN charge/payment: readable refusal, never an FK crash.

    ``grn_charge.recipient_party_id`` / ``supplier_payment.recipient_party_id``
    can point at a ``cash_flow_party`` that no cash-flow entry uses.  Delete used
    to send the DELETE anyway and the operator got
    "The operation could not be completed due to a data error."
    """
    from models import GRN, GRNCharge, GRNItem, Material, Supplier

    app, web = desk
    with app.app_context():
        party = CashFlowParty(name='Pioneer Labour', party_type='labour', is_active=True)
        supplier = Supplier(name='Cement Co')
        material = Material(code='CEM', name='Cement', unit='bag')
        db.session.add_all([party, supplier, material])
        db.session.flush()
        grn = GRN(supplier=supplier.name, supplier_id=supplier.id,
                  auto_bill_no='GRN-9001')
        db.session.add(grn)
        db.session.flush()
        db.session.add(GRNItem(grn_id=grn.id, mat_name=material.name,
                               qty=10, price_at_time=100))
        db.session.add(GRNCharge(grn_id=grn.id, label='Loading', amount=500,
                                 amount_minor=50000, recipient_party_id=party.id,
                                 recipient_name=party.name))
        db.session.commit()
        party_id = party.id

    post_action(web, 'delete_party', party_id=str(party_id))
    message = last_flash(web)
    assert 'Cannot delete' in message
    assert 'GRN charges' in message
    assert 'data error' not in message.lower()
    with app.app_context():
        assert db.session.get(CashFlowParty, party_id) is not None


def test_remove_entries_and_delete_empties_a_subcategory(desk):
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Fuel', direction='out')
        sub = make_subcategory(cat, 'Diesel')
        make_entry('out', 700, acc, category=cat, subcategory=sub)
        cat_id, sub_id, acc_id = cat.id, sub.id, acc.id
        db.session.commit()

    post_action(web, 'delete_subcategory', subcategory_id=str(sub_id))
    assert 'Cannot delete' in last_flash(web)

    post_action(web, 'purge_subcategory_entries', subcategory_id=str(sub_id))
    message = last_flash(web)
    assert 'Sub-category deleted' in message, message
    with app.app_context():
        assert db.session.get(CashFlowSubcategory, sub_id) is None
        assert db.session.get(CashFlowCategory, cat_id) is not None
        assert CashFlowEntry.query.count() == 0
        assert db.session.get(Account, acc_id).balance == 500000.0


def test_the_used_category_row_offers_the_emptying_action(desk):
    """The panel must offer the way forward instead of a dead-end disabled button."""
    app, web = desk
    with app.app_context():
        acc = make_account()
        cat = make_category('Transport', direction='out')
        sub = make_subcategory(cat, 'Loader')
        make_entry('out', 1500, acc, category=cat, subcategory=sub)
        cat_id = cat.id
        db.session.commit()

    html = web.get('/cash_flow').get_data(as_text=True)
    assert 'value="purge_category_entries"' in html
    assert 'Remove entries &amp; delete' in html
    assert 'value="purge_subcategory_entries"' in html
    # One row carries BOTH ids: the panel must report one transaction, not two.
    from app.services.cash_flow_svc import cf_category_entry_counts
    with app.app_context():
        assert cf_category_entry_counts(cat_id) == {'total': 1, 'direct': 1}
    assert 'remove its 1 transaction(s)' in html
