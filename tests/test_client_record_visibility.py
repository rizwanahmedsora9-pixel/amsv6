"""Verify client history stays with the right owner and is still accessible."""
from contextlib import contextmanager

from flask import template_rendered
import pytest

from app import create_app
from models import Booking, Client, DirectSale, DirectSaleItem, Entry, Payment, PendingBill, WaiveOff, db


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'client-test.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'isolated-client-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'ClientTestAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'client-test-only-password')
    app = create_app({'TESTING': True, 'INSTANCE_PATH': tmp_path / 'instance',
                      'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
                      'SESSION_COOKIE_SECURE': False})
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    web = app.test_client()
    assert web.post('/login', data={'username': 'ClientTestAdmin', 'password': 'client-test-only-password'}).status_code == 302
    return app, web


@contextmanager
def _views(app):
    views = {}

    def rendered(sender, template, context, **extra):
        views[template.name] = context

    with template_rendered.connected_to(rendered, app):
        yield views


def _clients():
    owner = Client(code='OWNER', name='Current Client Name', category='General')
    other = Client(code='OTHER', name='Old Client Name', category='General')
    db.session.add_all([owner, other])
    db.session.flush()
    return owner, other


def test_ledgers_show_coded_rows_with_an_old_name_only_for_the_owner(desk):
    app, web = desk
    with app.app_context():
        owner, other = _clients()
        sale = DirectSale(client_code=' owner ', client_name=other.name, amount=500,
                          auto_bill_no='STALE-NAME-SALE')
        sale.items = [DirectSaleItem(product_name='Cement', qty=2, price_at_time=250)]
        entry = Entry(client_code='OWNER', client=other.name, type='OUT', material='Cement', qty=2,
                      bill_no='STALE-NAME-ENTRY')
        pending = PendingBill(client_code='OWNER', client_name=other.name, bill_no='STALE-NAME-PENDING')
        no_code = PendingBill(client_name=other.name, bill_no='LEGACY-NAME-ONLY-PENDING')
        db.session.add_all([sale, entry, pending, no_code])
        ids = owner.id, other.id
        db.session.flush()
        sale_id, pending_id, legacy_id = sale.id, pending.id, no_code.id
        db.session.commit()

    for client_id, owns_sale in [(ids[0], True), (ids[1], False)]:
        with _views(app) as views:
            assert web.get(f'/ledger/{client_id}?tab=analysis').status_code == 200
            ledger = views['client_ledger.html']
            assert (f'DirectSale{sale_id}' in ledger['transactions_map']) == owns_sale
            assert ledger['material_grand']['lines'] == int(owns_sale)
            assert {p.id for p in ledger['pending_bills']} == ({pending_id} if owns_sale else {legacy_id})
            assert web.get(f'/client_ledger/{client_id}').status_code == 200
            assert {e.bill_no for e in views['ledger.html']['entries']} == (
                {'STALE-NAME-ENTRY'} if owns_sale else set()
            )


def test_edit_preserves_linked_history_and_does_not_claim_other_clients_rows(desk):
    app, web = desk
    with app.app_context():
        owner, other = _clients()
        rows = [
            Entry(client_code=other.code, client=owner.name, bill_no='OTHER-ENTRY'),
            DirectSale(client_code=other.code, client_name=owner.name, auto_bill_no='OTHER-SALE'),
            DirectSale(client_code=owner.code, client_name='Earlier Name', auto_bill_no='OWN-SALE'),
            PendingBill(client_name=f' {owner.name.upper()} ', bill_no='OWN-NAME-ONLY'),
            Booking(client_name=owner.name, auto_bill_no='OWN-BOOKING'),
            Payment(client_name=owner.name, auto_bill_no='OWN-LEGACY-PAYMENT'),
            Payment(client_name=owner.name, client_id=other.id, auto_bill_no='OTHER-PAYMENT'),
        ]
        db.session.add_all(rows)
        db.session.flush()
        _, other_sale_id, own_sale_id, pending_id, booking_id, pay_id, other_pay_id = [row.id for row in rows]
        owner_id, other_id = owner.id, other.id
        db.session.commit()

    response = web.post(f'/edit_client/{owner_id}', data={
        'name': 'Renamed Client', 'code': 'RENAMED', 'category': 'General',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Client updated' in response.data
    with app.app_context():
        assert Entry.query.filter_by(bill_no='OTHER-ENTRY').one().client_code == 'OTHER'
        assert db.session.get(DirectSale, other_sale_id).client_code == 'OTHER'
        assert db.session.get(DirectSale, own_sale_id).client_code == 'RENAMED'
        assert db.session.get(Booking, booking_id).client_name == 'Renamed Client'
        assert db.session.get(PendingBill, pending_id).client_code == 'RENAMED'
        payment = db.session.get(Payment, pay_id)
        assert payment.client_id == owner_id
        assert payment.client_name == 'Current Client Name'
        assert db.session.get(Payment, other_pay_id).client_id == other_id

    # A legacy name-only record cannot safely be moved to either of two
    # clients with the same name. Refuse the rename before changing any rows.
    with app.app_context():
        db.session.add(Client(name=' renamed client ', code='DUPLICATE-NAME'))
        db.session.commit()
    response = web.post(f'/edit_client/{owner_id}', data={
        'name': 'Another Name', 'code': 'ANOTHER', 'category': 'General',
    }, follow_redirects=True)
    assert b'Client identity is ambiguous' in response.data
    with app.app_context():
        assert db.session.get(Client, owner_id).code == 'RENAMED'
        assert db.session.get(Booking, booking_id).client_name == 'Renamed Client'


def test_suspend_keeps_history_and_old_transfer_links_cannot_move_it(desk):
    app, web = desk
    with app.app_context():
        owner, other = _clients()
        other_bill = PendingBill(client_code=other.code, client_name=other.name)
        db.session.add_all([
            Entry(client_code=owner.code, client=owner.name, bill_no='OWNER-ENTRY'),
            Entry(client_code=other.code, client=other.name, bill_no='OTHER-ENTRY'),
            other_bill,
            WaiveOff(client_code=other.code, client_name=other.name, bill_no='OTHER-WAIVE'),
        ])
        ids = owner.id, other.id
        db.session.flush()
        other_bill_id = other_bill.id
        db.session.commit()

    response = web.post(f'/transfer_client/{ids[0]}', data={'target_client_id': ids[1]}, follow_redirects=True)
    assert b'Client data transfer is paused' in response.data
    with app.app_context():
        assert db.session.get(Client, ids[0]).is_active
        assert Entry.query.filter_by(bill_no='OWNER-ENTRY').one().client_code == 'OWNER'

    assert web.post(f'/delete_client/{ids[0]}').status_code == 302
    with _views(app) as views:
        response = web.get('/clients?search=OWNER')
        assert response.status_code == 200
        assert b'Inactive Clients' in response.data
        assert f'/ledger/{ids[0]}'.encode() in response.data
        assert views['clients.html']['active_pagination'].total == 0
        assert views['clients.html']['inactive_pagination'].total == 1
        assert web.get(f'/client_ledger/{ids[0]}').status_code == 200
        assert [row.bill_no for row in views['ledger.html']['entries']] == ['OWNER-ENTRY']

    # Simulate a pre-existing transfer flag with mixed history at the target.
    with app.app_context():
        db.session.get(Client, ids[0]).transferred_to_id = ids[1]
        db.session.commit()
    response = web.post(f'/reclaim_client/{ids[0]}', follow_redirects=True)
    assert b'cannot be reclaimed automatically' in response.data
    with app.app_context():
        assert not db.session.get(Client, ids[0]).is_active
        assert Entry.query.filter_by(bill_no='OTHER-ENTRY').one().client_code == 'OTHER'
        assert db.session.get(PendingBill, other_bill_id).client_code == 'OTHER'
        assert WaiveOff.query.filter_by(bill_no='OTHER-WAIVE').one().client_code == 'OTHER'
