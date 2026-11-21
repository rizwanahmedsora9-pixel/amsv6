from contextlib import contextmanager
from datetime import datetime

import pytest
from flask import template_rendered

from app import create_app
from models import Booking, BookingItem, Client, Entry, PendingBill, db


@pytest.fixture
def ledger_desk(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'ledger-booking-test.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'ledger-booking-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'LedgerTestAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'ledger-test-only-password')
    app = create_app({
        'TESTING': True,
        'INSTANCE_PATH': tmp_path / 'instance',
        'ENABLE_BACKGROUND_WORKERS': False,
        'DAILY_BACKUP_SCHEDULER': False,
        'SESSION_COOKIE_SECURE': False,
    })
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    web = app.test_client()
    assert web.post('/login', data={
        'username': 'LedgerTestAdmin',
        'password': 'ledger-test-only-password',
    }).status_code == 302
    return app, web


@contextmanager
def _rendered_context(app):
    captured = {}

    def record(sender, template, context, **extra):
        captured[template.name] = context

    with template_rendered.connected_to(record, app):
        yield captured


def test_full_booking_and_pending_amount_include_opening_credit(ledger_desk):
    app, web = ledger_desk
    with app.app_context():
        client = Client(
            code='FBMCL-00306',
            name='Haji Iqbal Sahb JPS',
            category='General',
            opening_balance=-750245,
            opening_balance_date=datetime(2026, 11, 15, 20, 8),
        )
        booking = Booking(
            client_name=client.name,
            amount=801200,
            paid_amount=0,
            auto_bill_no='SB-BK-1017',
            date_posted=datetime(2026, 11, 15, 20, 8),
            is_void=False,
        )
        booking.items = [
            BookingItem(material_name='Kohat', qty=40, price_at_time=1430),
            BookingItem(material_name='Rent Cement', qty=40, price_at_time=25),
            BookingItem(material_name='Kohat', qty=500, price_at_time=1460),
            BookingItem(material_name='Rent Cement', qty=500, price_at_time=26),
        ]
        db.session.add_all([client, booking])
        db.session.flush()
        db.session.add_all([
            Entry(client_code=client.code, client=client.name, type='OUT', material='Kohat',
                  qty=20, bill_no='SB-SL-1028', nimbus_no='Direct Sale',
                  client_category='Booking Delivery', is_void=False),
            Entry(client_code=client.code, client=client.name, type='OUT', material='Rent Cement',
                  qty=20, bill_no='SB-SL-1028', nimbus_no='Direct Sale',
                  client_category='Booking Delivery', is_void=False),
            PendingBill(client_code=client.code, client_name=client.name,
                        bill_no='SB-BK-1017', amount=801200, is_paid=False,
                        is_void=False, source_table='booking', source_id=booking.id),
        ])
        client_id = client.id
        db.session.commit()

    with _rendered_context(app) as views:
        response = web.get(f'/ledger/{client_id}')
    assert response.status_code == 200
    ledger = views['client_ledger.html']
    assert ledger['total_debit'] == 801200
    assert ledger['total_credit'] == 750245
    assert ledger['total_balance'] == 50955
    assert ledger['undelivered_total'] == 772100
    pending = ledger['pending_bills'][0]
    assert pending.display_original_amount == 801200
    assert pending.display_amount == 50955
    assert pending.display_status == 'Pending'
    assert b'50,955' in response.data
