"""Category-wise Decision Ledger: grouping, reconciliation and access.

The category view is presentation only — these tests pin down that

* every Material-Master category becomes a group (plus Uncategorised),
* group totals are exactly the sum of their material rows,
* All-Categories totals equal the flat summary (no accounting change),
* quantities are bucketed per unit and never mixed,
* inactive/no-master names and services are not lost,
* the page, permission map and login wall are unchanged.
"""
from contextlib import contextmanager
import re

from flask import template_rendered
import pytest

from app import create_app
from app.services.constants import ENDPOINT_PERMISSION_MAP
from models import (
    Booking,
    BookingAllocation,
    BookingItem,
    Client,
    DirectSale,
    DirectSaleItem,
    Entry,
    Material,
    MaterialCategory,
    Service,
    db,
)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'decision-categories.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'isolated-decision-categories-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'DecisionCategoryAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'decision-category-test-password')
    app = create_app({'TESTING': True, 'INSTANCE_PATH': tmp_path / 'instance',
                      'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
                      'SESSION_COOKIE_SECURE': False})
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    web = app.test_client()
    assert web.post('/login', data={'username': 'DecisionCategoryAdmin',
                                    'password': 'decision-category-test-password'}).status_code == 302
    return app, web


@contextmanager
def _views(app):
    views = {}

    def rendered(sender, template, context, **extra):
        views[template.name] = context

    with template_rendered.connected_to(rendered, app):
        yield views


def _seed(app):
    """Cement + Steel masters, one service, one nameless-to-master material.

    Expected ledger after seeding (returns already netted):

    | group   | materials            | booked        | delivered     | remaining     |
    | Cement  | DG, Pioneer          | 16 / 14,000   | 3 / 1,500     | 13 / 12,500   |
    | Steel   | 6mm Steel            | 100 / 26,000  | 30 / 7,800    | 70 / 18,200   |
    | Uncat.  | Mystery Mat, Rent C. | 8 / 230       | 3 / 90        | 5 / 140       |
    """
    with app.app_context():
        cement = MaterialCategory(name='Cement')
        steel = MaterialCategory(name='Steel')
        # Category with no activity at all — must still render as a group.
        sand = MaterialCategory(name='Sand')
        db.session.add_all([cement, steel, sand])
        db.session.flush()
        db.session.add_all([
            Material(code='C-DG', name='DG', category_id=cement.id, unit='Bags'),
            Material(code='C-PIO', name='Pioneer', category_id=cement.id, unit='Bags'),
            Material(code='S-6MM', name='6mm Steel', category_id=steel.id, unit='KG'),
            # Master row with no booking activity — must not materialise as a
            # ledger row, but its category must still exist.
            Material(code='S-12MM', name='12mm Steel', category_id=steel.id, unit='KG'),
            Service(code='SVC-RENTCEME', name='Rent Cement', unit='bags'),
        ])
        client = Client(code='CAT01', name='Category Client', category='General')
        db.session.add(client)
        db.session.flush()

        def booking(amount, line):
            b = Booking(client_name=client.name, amount=amount, paid_amount=0, is_void=False)
            b.items = [BookingItem(material_name=line[0], qty=line[1], price_at_time=line[2])]
            db.session.add(b)
            db.session.flush()
            return b

        dg = booking(5000, ('DG', 10, 500.0))
        booking(9000, ('Pioneer', 6, 1500.0))
        booking(26000, ('6mm Steel', 100, 260.0))
        booking(200, ('Rent Cement', 5, 40.0))
        booking(30, ('Mystery Mat', 3, 10.0))

        # DG: allocated delivery of 4 with a Booked Return of 1 → net 3.
        sale = DirectSale(client_name=client.name, client_code=client.code, category='Cash',
                          amount=2000, paid_amount=2000, is_void=False)
        sale.items = [DirectSaleItem(product_name='DG', qty=4, price_at_time=500.0)]
        db.session.add(sale)
        db.session.flush()
        db.session.add(BookingAllocation(
            sale_id=sale.id, sale_item_id=sale.items[0].id,
            booking_item_id=dg.items[0].id, qty=4, is_void=False))
        db.session.add_all([
            Entry(type='OUT', material='DG', client=client.name, client_code=client.code,
                  client_category='Booking Delivery', nimbus_no='Direct Sale',
                  qty=4, date='2026-11-15', time='10:00:00', is_void=False),
            Entry(type='IN', material='DG', client=client.name, client_code=client.code,
                  qty=1, date='2026-11-16', time='10:00:00', is_void=False,
                  nimbus_no='Material Return', transaction_category='Booked Return'),
            # Unallocated legacy-style dispatch → FIFO pricing.
            Entry(type='OUT', material='6mm Steel', client=client.name, client_code=client.code,
                  client_category='Booking Delivery', nimbus_no='Direct Sale',
                  qty=30, date='2026-11-15', time='11:00:00', is_void=False),
            Entry(type='OUT', material='Rent Cement', client=client.name, client_code=client.code,
                  client_category='Booking Delivery', nimbus_no='Direct Sale',
                  qty=2, date='2026-11-15', time='12:00:00', is_void=False),
            Entry(type='OUT', material='Mystery Mat', client=client.name, client_code=client.code,
                  client_category='Booking Delivery', nimbus_no='Direct Sale',
                  qty=1, date='2026-11-15', time='13:00:00', is_void=False),
        ])
        db.session.commit()


def _close(a, b, tol=1e-6):
    assert a == pytest.approx(b, abs=tol)


def test_groups_reconcile_with_material_rows_and_overall_totals(desk):
    app, web = desk
    _seed(app)
    with _views(app) as views:
        assert web.get('/decision_ledger').status_code == 200
        ctx = views['decision_ledger.html']
        groups = ctx['category_summary']
        overall = ctx['overall_totals']

        # Every master category + Uncategorised, alphabetical with uncat last.
        # "Open Cement" is the reserved system category created at bootstrap.
        assert [g['name'] for g in groups] == [
            'Cement', 'Open Cement', 'Sand', 'Steel', 'Uncategorised'
        ]
        # The activity-free category renders with zero totals and no rows.
        sand_group = next(g for g in groups if g['name'] == 'Sand')
        assert sand_group['materials'] == []
        _close(sand_group['booked_cost'], 0)
        _close(sand_group['remaining'], 0)

        # Group totals equal the sum of their own material rows, field by field.
        for g in groups:
            for field in ('booked', 'dispatched', 'remaining', 'booked_cost',
                          'dispatched_cost', 'remaining_cost', 'unpriced_qty'):
                _close(g[field], sum(m[field] for m in g['materials']))

        # All-Categories totals = sum of category groups = flat summary.
        for field in ('booked_cost', 'dispatched_cost', 'remaining_cost', 'unpriced_qty'):
            _close(overall[field], sum(g[field] for g in groups))
            _close(overall[field],
                   sum(m[field] for m in ctx['overall_material_summary']))

        by_name = {g['name']: g for g in groups}
        assert [m['name'] for m in by_name['Cement']['materials']] == ['DG', 'Pioneer']
        assert [m['name'] for m in by_name['Steel']['materials']] == ['6mm Steel']
        assert [m['name'] for m in by_name['Uncategorised']['materials']] == [
            'Mystery Mat', 'Rent Cement']

        cement, steel, uncat = by_name['Cement'], by_name['Steel'], by_name['Uncategorised']
        _close(cement['booked'], 16); _close(cement['booked_cost'], 14000)
        _close(cement['dispatched'], 3); _close(cement['dispatched_cost'], 1500)
        _close(cement['remaining'], 13); _close(cement['remaining_cost'], 12500)
        _close(steel['booked'], 100); _close(steel['booked_cost'], 26000)
        _close(steel['dispatched'], 30); _close(steel['dispatched_cost'], 7800)
        _close(steel['remaining'], 70); _close(steel['remaining_cost'], 18200)
        _close(uncat['booked'], 8); _close(uncat['booked_cost'], 230)
        _close(uncat['dispatched'], 3); _close(uncat['dispatched_cost'], 90)
        _close(uncat['remaining'], 5); _close(uncat['remaining_cost'], 140)

        _close(overall['booked_cost'], 40230)
        _close(overall['dispatched_cost'], 9390)
        _close(overall['remaining_cost'], 30840)


def test_quantities_are_bucketed_per_unit_and_never_mixed(desk):
    app, web = desk
    _seed(app)
    with _views(app) as views:
        assert web.get('/decision_ledger').status_code == 200
        groups = {g['name']: g for g in views['decision_ledger.html']['category_summary']}

        cement_buckets = groups['Cement']['qty_buckets']
        assert len(cement_buckets) == 1
        assert cement_buckets[0]['label'] == 'Bags'
        _close(cement_buckets[0]['booked'], 16)

        assert [b['label'] for b in groups['Steel']['qty_buckets']] == ['KG']

        # Uncategorised mixes service 'bags' with an unknown unit — two
        # buckets, so no single total is derivable and none is shown.
        uncat_labels = [b['label'] for b in groups['Uncategorised']['qty_buckets']]
        assert uncat_labels == ['', 'bags']
        assert sum(b['booked'] for b in groups['Uncategorised']['qty_buckets']) == 8


def test_dropdown_markup_search_and_cards_contract(desk):
    app, web = desk
    _seed(app)
    resp = web.get('/decision_ledger')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Dropdown carries every category from the DB plus All Categories.
    assert '<option value="">All Categories</option>' in html
    for g_name in ('Cement', 'Open Cement', 'Sand', 'Steel', 'Uncategorised'):
        assert re.search(rf'<option value="[^"]+">{g_name}</option>', html)
    # Client-side only: the category select must not submit/reload the page.
    sel_tag = re.search(r'<select id="dl-cat-filter"[^>]*>', html).group(0)
    assert 'name=' not in sel_tag

    # Expandable rows, indented children, search box and card hooks exist.
    # Five groups: Cement, the reserved Open Cement, Sand, Steel, Uncategorised.
    assert html.count('class="dl-cat-row"') == 5
    assert 'data-name="dg"' in html and 'data-name="mystery mat"' in html
    assert 'id="dl-mat-search"' in html
    # The empty Sand group shows a placeholder child, not a broken arrow.
    assert 'No material activity in this category.' in html
    for card_id in ('dl-card-booked', 'dl-card-delivered', 'dl-card-remaining'):
        assert f'id="{card_id}"' in html
    # Server-rendered category values (used by the card swap) are present.
    assert '14,000.00' in html and '26,000.00' in html and '230.00' in html


def test_empty_database_renders_cleanly(desk):
    app, web = desk
    resp = web.get('/decision_ledger')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The reserved Open Cement category exists from bootstrap, so the page
    # renders its (empty) group with the standard placeholder instead of the
    # whole-page "no activity" message.
    assert 'Open Cement' in html
    assert 'No material activity in this category.' in html
    assert 'All Categories' in html


def test_access_rules_are_unchanged(desk):
    app, _web = desk
    # Anonymous request is still bounced by login_required.
    anon = app.test_client()
    resp = anon.get('/decision_ledger')
    assert resp.status_code in (302, 401)
    if resp.status_code == 302:
        assert '/login' in resp.headers.get('Location', '')
    # And the endpoint keeps its permission (enforced app-wide by hooks.py).
    assert ENDPOINT_PERMISSION_MAP['decision_ledger'] == 'can_view_decision_ledger'
