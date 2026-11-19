"""Decision ledger must credit booked-material returns (Ring 6x6 regression).

After a Booked Return + booking cancel the booking line holds the net
quantity (87.1 delivered − 12.4 returned = 74.7 booked). The decision ledger
used to count only OUT rows, so it printed delivered 87.1 > booked 74.7 as a
negative remaining (−12.4 / −Rs 3,286.00) while every other module — the
cancel plan, the sale-allocation pool and the material ledger — had already
netted the return.
"""
from contextlib import contextmanager

from flask import template_rendered
import pytest

from app import create_app
from models import (
    Booking,
    BookingAllocation,
    BookingItem,
    Client,
    DirectSale,
    DirectSaleItem,
    Entry,
    db,
)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'decision-ledger.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'isolated-decision-ledger-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'DecisionLedgerAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'decision-ledger-test-password')
    app = create_app({'TESTING': True, 'INSTANCE_PATH': tmp_path / 'instance',
                      'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
                      'SESSION_COOKIE_SECURE': False})
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    web = app.test_client()
    assert web.post('/login', data={'username': 'DecisionLedgerAdmin',
                                    'password': 'decision-ledger-test-password'}).status_code == 302
    return app, web


@contextmanager
def _views(app):
    views = {}

    def rendered(sender, template, context, **extra):
        views[template.name] = context

    with template_rendered.connected_to(rendered, app):
        yield views


def _material_row(views, name):
    rows = views['decision_ledger.html']['overall_material_summary']
    return next(r for r in rows if r['name'] == name)


def test_returned_qty_is_credited_so_remaining_is_not_negative(desk):
    """Ring 6x6: 87.1 dispatched − 12.4 returned = 74.7 booked → 0 left."""
    app, web = desk
    with app.app_context():
        client = Client(code='RING01', name='Malik Amir Sahb Tibba', category='General')
        booking = Booking(client_name=client.name, amount=19795.50, paid_amount=0, is_void=False)
        item = BookingItem(material_name='Ring 6x6', qty=74.7, price_at_time=265.0)
        booking.items = [item]
        db.session.add_all([client, booking])
        db.session.flush()

        sale = DirectSale(client_name=client.name, client_code=client.code, category='Cash',
                          amount=23081.50, paid_amount=23081.50, is_void=False)
        sale.items = [DirectSaleItem(product_name='Ring 6x6', qty=87.1, price_at_time=265.0)]
        db.session.add(sale)
        db.session.flush()
        db.session.add(BookingAllocation(
            sale_id=sale.id, sale_item_id=sale.items[0].id,
            booking_item_id=item.id, qty=87.1, is_void=False))
        db.session.add_all([
            Entry(type='OUT', material='Ring 6x6', client=client.name, client_code=client.code,
                  client_category='Booking Delivery', nimbus_no='Direct Sale',
                  qty=87.1, date='2026-11-15', time='15:42:00', is_void=False),
            Entry(type='IN', material='Ring 6x6', client=client.name, client_code=client.code,
                  qty=12.4, date='2026-11-17', time='10:00:00', is_void=False,
                  nimbus_no='Material Return', transaction_category='Booked Return'),
        ])
        db.session.commit()

    with _views(app) as views:
        assert web.get('/decision_ledger').status_code == 200

        row = _material_row(views, 'Ring 6x6')
        assert row['booked'] == pytest.approx(74.7)
        assert row['dispatched'] == pytest.approx(74.7)  # 87.1 OUT − 12.4 returned
        assert row['remaining'] == pytest.approx(0.0)
        assert row['booked_cost'] == pytest.approx(19795.50, abs=0.01)
        assert row['dispatched_cost'] == pytest.approx(19795.50, abs=0.01)
        assert row['remaining_cost'] == pytest.approx(0.0, abs=0.01)
        assert row['unpriced_qty'] == pytest.approx(0.0)

        totals = views['decision_ledger.html']['overall_totals']
        assert totals['remaining_cost'] == pytest.approx(0.0, abs=0.01)

        client_row = views['decision_ledger.html']['data'][0]
        badge = client_row['materials'][0]
        assert badge['remaining'] == pytest.approx(0.0)
        assert badge['remaining_cost'] == pytest.approx(0.0, abs=0.01)


def test_over_return_keeps_full_booked_obligation(desk):
    """Return larger than the dispatches clamps delivered at 0, not negative."""
    app, web = desk
    with app.app_context():
        client = Client(code='SAND01', name='Sand Client', category='General')
        booking = Booking(client_name=client.name, amount=500.0, paid_amount=0, is_void=False)
        booking.items = [BookingItem(material_name='Loose Sand', qty=10, price_at_time=50.0)]
        db.session.add_all([client, booking])
        db.session.add_all([
            Entry(type='OUT', material='Loose Sand', client=client.name, client_code=client.code,
                  client_category='Booking Delivery', nimbus_no='Direct Sale',
                  qty=4, date='2026-11-15', time='09:00:00', is_void=False),
            Entry(type='IN', material='Loose Sand', client=client.name, client_code=client.code,
                  qty=6, date='2026-11-16', time='09:00:00', is_void=False,
                  nimbus_no='Material Return', transaction_category='Booked Return'),
        ])
        db.session.commit()

    with _views(app) as views:
        assert web.get('/decision_ledger').status_code == 200

        row = _material_row(views, 'Loose Sand')
        # 4 dispatched − 6 returned clamps to 0, so the full booking stands.
        assert row['dispatched'] == pytest.approx(0.0)
        assert row['remaining'] == pytest.approx(10.0)
        assert row['dispatched_cost'] == pytest.approx(0.0, abs=0.01)
        assert row['remaining_cost'] == pytest.approx(500.0, abs=0.01)
