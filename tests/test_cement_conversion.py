"""Cement Bag -> Open Cement (KG) conversion: acceptance tests A-J.

Every test runs against an isolated SQLite database created by ``create_app``
(``APP_DB_PATH`` pointed at ``tmp_path``), so the conversion tables, the
reserved category and the FIFO replay are exercised exactly as in production.
"""
from __future__ import annotations

import threading
from datetime import datetime

import pytest

from app import create_app
from models import (
    CementConversion,
    CementConversionLayer,
    Client,
    DeliveryPerson,
    DirectSale,
    DirectSaleItem,
    Entry,
    GRN,
    GRNItem,
    Material,
    MaterialCategory,
    User,
    db,
)
from app.services import cement_conversion as cc
from app.services.open_cement import (
    OPEN_CEMENT_CATEGORY_NAME,
    ensure_open_cement_category,
    is_open_cement_material,
    open_cement_category,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def world(tmp_path, monkeypatch):
    """Fresh app + database."""
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'cement.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health.json'))
    monkeypatch.setenv('SECRET_KEY', 'cement-conversion-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'CementAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'cement-test-password')
    app = create_app({
        'TESTING': True,
        'INSTANCE_PATH': tmp_path / 'instance',
        'ENABLE_BACKGROUND_WORKERS': False,
        'DAILY_BACKUP_SCHEDULER': False,
        'SESSION_COOKIE_SECURE': False,
    })
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    with app.app_context():
        yield app


def _login(web, username='CementAdmin', password='cement-test-password'):
    # A GET first so the session carries the CSRF token even when enforcement
    # is forced on (AMS_CSRF_ALWAYS).
    web.get('/login')
    response = web.post('/login', data={
        'username': username,
        'password': password,
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302


def _csrf(web):
    with web.session_transaction() as session:
        return session.get('_csrf_token')


def _flashes(web):
    with web.session_transaction() as session:
        return session.get('_flashes') or []


def _open_category_id(app):
    with app.app_context():
        return open_cement_category().id


def _make_world(app, *, bags=100, rate=1400.0, bag_name='DG Cement', open_name='Open DG Cement'):
    """Create a bagged item, a GRN receipt and its linked Open Cement item."""
    ensure_open_cement_category()
    with app.app_context():
        cat_id = open_cement_category().id
        bag = Material(name=bag_name, code=f'BG-{bag_name}', unit='Bags', total=0)
        db.session.add(bag)
        db.session.flush()
        open_mat = Material(
            name=open_name, code=f'OP-{bag_name}', category_id=cat_id, unit='KG',
            total=0, source_material_id=bag.id,
        )
        db.session.add(open_mat)
        db.session.flush()
        grn = GRN(
            supplier='Supplier A', auto_bill_no=f'SB-GRN-{bag.id}001',
            manual_bill_no=f'G-{bag.id}', costing_mode='LANDED',
            date_posted=datetime(2026, 11, 1, 9, 0), created_by='CementAdmin',
        )
        db.session.add(grn)
        db.session.flush()
        item = GRNItem(grn_id=grn.id, mat_name=bag_name, qty=bags, price_at_time=rate)
        db.session.add(item)
        db.session.flush()
        db.session.add(Entry(
            date='2026-11-01', time='09:00:00', type='IN', material=bag_name,
            client='Supplier A', qty=bags, bill_no=f'G-{bag.id}',
            auto_bill_no=grn.auto_bill_no, created_by='CementAdmin',
        ))
        bag.total = float(bags)
        db.session.commit()
        return {
            'bag_id': bag.id,
            'open_id': open_mat.id,
            'grn_id': grn.id,
            'grn_item_id': item.id,
            'bag_name': bag_name,
            'open_name': open_name,
            'cat_id': cat_id,
        }


def _convert(app, bag_id, open_id, *, bags, date='2026-11-05', weight=None, note='test', actor='CementAdmin'):
    with app.app_context():
        conv = cc.create_conversion(
            source_material=db.session.get(Material, int(bag_id)),
            dest_material=db.session.get(Material, int(open_id)),
            conversion_date=date, bags=bags, weight_per_bag=weight, note=note, actor=actor,
        )
        db.session.commit()
        return {'id': conv.id, 'reference': conv.reference}


def _preview(app, bag_id, bags, weight=None, date='2026-11-05'):
    with app.app_context():
        return cc.preview_conversion(db.session.get(Material, int(bag_id)), date, bags, weight)


def _pnl(app, start='2026-11-01', end='2026-11-30'):
    from app.services.profit_loss import build_profit_loss_report
    with app.app_context():
        return build_profit_loss_report(start_date=start, end_date=end)


def _get(app, model, oid):
    with app.app_context():
        return db.session.get(model, oid)


# --------------------------------------------------------------------------- #
# A. Category bootstrap is idempotent and reserved
# --------------------------------------------------------------------------- #
def test_a_open_cement_category_is_idempotent_and_reserved(world):
    with world.app_context():
        first = ensure_open_cement_category()
        second = ensure_open_cement_category()
        assert first.id == second.id
        assert MaterialCategory.query.filter_by(name=OPEN_CEMENT_CATEGORY_NAME).count() == 1
        assert first.is_active is True

        from app.services.open_cement import protect_open_cement_category
        message = protect_open_cement_category(first)
        assert message and 'system-managed' in message


def test_a_open_cement_category_created_on_boot(world):
    with world.app_context():
        cat = open_cement_category()
        assert cat is not None and cat.name == OPEN_CEMENT_CATEGORY_NAME


# --------------------------------------------------------------------------- #
# B. Open Cement never appears on a GRN
# --------------------------------------------------------------------------- #
def test_b_grn_material_list_excludes_open_cement(world):
    data = _make_world(world)
    web = world.test_client()
    _login(web)
    html = web.get('/grn').get_data(as_text=True)
    assert data['bag_name'] in html
    assert data['open_name'] not in html


def test_b_grn_post_rejects_open_cement(world):
    data = _make_world(world)
    web = world.test_client()
    _login(web)
    response = web.post('/grn', data={
        'action': 'add',
        'supplier': 'Supplier A',
        'manual_bill_no': 'G-OPEN-REJECT',
        'date': '2026-11-06',
        'time': '10:00',
        'mat_name[]': [data['open_name']],
        'qty[]': ['10'],
        'price[]': ['28'],
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    assert any('Open Cement' in message for _, message in _flashes(web))
    with world.app_context():
        assert not GRN.query.filter_by(manual_bill_no='G-OPEN-REJECT').first()
        assert GRNItem.query.filter_by(mat_name=data['open_name']).count() == 0


def test_b_grn_edit_sync_rejects_open_cement(world):
    data = _make_world(world)
    with world.app_context():
        from app.services.grn_edit import sync_items_from_form

        class FakeForm:
            def getlist(self, key):
                return [data['open_name']] if key == 'mat_name[]' else []

            def get(self, key, default=None):
                return default

        with pytest.raises(ValueError) as excinfo:
            sync_items_from_form(db.session.get(GRN, data['grn_id']), FakeForm(), actor='CementAdmin')
        assert 'Open Cement' in str(excinfo.value)


def test_b_historical_grn_rows_are_untouched(world):
    data = _make_world(world)
    with world.app_context():
        assert GRNItem.query.filter_by(grn_id=data['grn_id']).count() == 1
        grn2 = GRN(
            supplier='Supplier B', auto_bill_no='SB-GRN-777', manual_bill_no='G-2',
            costing_mode='LANDED', date_posted=datetime(2026, 11, 2, 9, 0),
            created_by='CementAdmin',
        )
        db.session.add(grn2)
        db.session.flush()
        db.session.add(GRNItem(grn_id=grn2.id, mat_name=data['bag_name'], qty=5, price_at_time=1400))
        db.session.commit()
        assert GRNItem.query.filter_by(grn_id=grn2.id).count() == 1


# --------------------------------------------------------------------------- #
# C. Conversion moves stock and preserves the GRN quantity
# --------------------------------------------------------------------------- #
def test_c_conversion_1_bag_leaves_99_bags_and_50_kg(world):
    data = _make_world(world, bags=100, rate=1400.0)
    conv = _convert(world, data['bag_id'], data['open_id'], bags='1')
    with world.app_context():
        bag = db.session.get(Material, data['bag_id'])
        open_mat = db.session.get(Material, data['open_id'])
        assert bag.total == pytest.approx(99.0)
        assert open_mat.total == pytest.approx(50.0)
        assert conv['reference']
        stored = db.session.get(CementConversion, conv['id'])
        assert stored.kg_produced == pytest.approx(50.0)
        assert stored.total_transferred_cost == pytest.approx(1400.0)
        assert stored.source_cost_per_kg == pytest.approx(28.0)
        # Original GRN quantity is never changed.
        assert db.session.get(GRNItem, data['grn_item_id']).qty == pytest.approx(100.0)
        # Two linked movements share the conversion id.
        entries = Entry.query.filter_by(source_table='cement_conversion', source_id=conv['id']).all()
        assert sorted((e.type, e.material, e.qty) for e in entries) == [
            ('IN', data['open_name'], 50.0),
            ('OUT', data['bag_name'], 1.0),
        ]
        assert all(e.source_bill_no == conv['reference'] for e in entries)
        # No sale side effect.
        assert DirectSale.query.count() == 0
        assert stored.cost_basis


def test_c_preview_reports_cost_and_stock(world):
    data = _make_world(world, bags=100, rate=1400.0)
    preview = _preview(world, data['bag_id'], 2)
    assert preview['kg_produced'] == pytest.approx(100.0)
    assert preview['cost_per_bag'] == pytest.approx(1400.0)
    assert preview['cost_per_kg'] == pytest.approx(28.0)
    assert preview['covered'] is True
    assert preview['valid'] is True


def test_c_rejects_over_stock(world):
    data = _make_world(world, bags=10, rate=1400.0)
    with pytest.raises(ValueError) as excinfo:
        _convert(world, data['bag_id'], data['open_id'], bags='11')
    assert 'Insufficient stock' in str(excinfo.value)
    with world.app_context():
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(10.0)
        assert CementConversion.query.count() == 0


def test_c_rejects_negative_and_zero(world):
    data = _make_world(world)
    for bad in ('0', '-3', 'abc'):
        with pytest.raises(ValueError):
            _convert(world, data['bag_id'], data['open_id'], bags=bad)


def test_c_rejects_cross_brand_and_unlinked_destination(world):
    data = _make_world(world)
    with world.app_context():
        other_bag = Material(name='Other Cement', code='OTH', unit='Bags', total=10)
        other_open = Material(
            name='Open Other', code='OPOTH', category_id=data['cat_id'],
            unit='KG', total=0, source_material_id=other_bag.id,
        )
        db.session.add_all([other_bag, other_open])
        db.session.commit()
        other_bag_id, other_open_id = other_bag.id, other_open.id

    # Destination not linked to the source.
    with pytest.raises(ValueError) as excinfo:
        _convert(world, data['bag_id'], other_open_id, bags='1')
    assert 'not linked' in str(excinfo.value)

    # Source that is itself Open Cement.
    with pytest.raises(ValueError) as excinfo:
        _convert(world, data['open_id'], data['open_id'], bags='1')
    assert 'cannot be used as a conversion source' in str(excinfo.value)


# --------------------------------------------------------------------------- #
# D. Selling open cement deducts stock in KG
# --------------------------------------------------------------------------- #
def _sell(world, web, material_name, qty, rate, *, date='2026-11-10 10:00', idem='sale-1',
          client='Walk-in Customer'):
    with world.app_context():
        if not Client.query.filter_by(name=client).first():
            db.session.add(Client(code='C-1', name=client, category='General'))
        if not DeliveryPerson.query.filter_by(name='Driver One').first():
            db.session.add(DeliveryPerson(name='Driver One', phone='0300'))
        db.session.commit()
        person_id = DeliveryPerson.query.filter_by(name='Driver One').first().id
    return web.post('/add_direct_sale', data={
        'client_name': client,
        'category': 'Cash',
        'sale_date': date,
        'product_name[]': [material_name],
        'qty[]': [str(qty)],
        'unit_rate[]': [str(rate)],
        'payment_method': 'Cash',
        'paid_amount': '0',
        'delivery_person_id[]': [str(person_id)],
        'create_invoice': '1',
        'idempotency_key': idem,
        '_csrf_token': _csrf(web),
    })


def test_d_sale_of_45kg_leaves_5kg(world):
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    response = _sell(world, web, data['open_name'], 45, 40)
    assert response.status_code == 302
    with world.app_context():
        assert db.session.get(Material, data['open_id']).total == pytest.approx(5.0)
        sale = DirectSale.query.one()
        item = DirectSaleItem.query.filter_by(sale_id=sale.id).one()
        assert item.product_name == data['open_name']
        assert item.qty == pytest.approx(45.0)
        # Cost inherited from the conversion layer (Rs. 28/kg).
        assert item.cost_rate_at_sale == pytest.approx(28.0)
        # Bagged cement stock is untouched by the open-cement sale.
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(99.0)


def test_d_open_cement_sale_does_not_touch_bagged_sales(world):
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    _sell(world, web, data['bag_name'], 2, 1500, idem='bag-sale')
    with world.app_context():
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(97.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# E. Lost Ledger records the open-cement loss once, with its conversion cost
# --------------------------------------------------------------------------- #
def test_e_loss_of_5kg_empties_stock_and_posts_pnl_once(world):
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    _sell(world, web, data['open_name'], 45, 40, idem='sale-e')

    with world.app_context():
        from app.services.stock_adjustments import create_stock_adjustment
        adj = create_stock_adjustment(
            material_name=data['open_name'], adjustment_date='2026-11-12',
            direction='LOSS', qty='5', reason='Damage / Broken', note='broken bag',
            source='lost_ledger', actor='CementAdmin',
        )
        db.session.commit()
        assert db.session.get(Material, data['open_id']).total == pytest.approx(0.0)
        assert adj.unit_cost == pytest.approx(28.0)
        assert adj.total_value == pytest.approx(140.0)
        assert adj.conversion_reference == CementConversion.query.one().reference
        assert adj.direction == 'LOSS'

        summary = _pnl(world)['summary']
        assert summary['total_material_revenue'] == pytest.approx(1800.0)
        assert summary['total_material_cost'] == pytest.approx(1260.0)
        assert summary['total_other_losses'] == pytest.approx(140.0)
        assert summary['total_profit'] == pytest.approx(400.0)
        assert summary['loss_rows'] == 1


def test_e_loss_cannot_exceed_available_open_cement(world):
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    with world.app_context():
        from app.services.stock_adjustments import create_stock_adjustment
        with pytest.raises(ValueError) as excinfo:
            create_stock_adjustment(
                material_name=data['open_name'], adjustment_date='2026-11-12',
                direction='LOSS', qty='51', reason='Damage / Broken',
                source='lost_ledger', actor='CementAdmin',
            )
        assert 'exceeds available' in str(excinfo.value)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# F. Financial reconciliation: sold + lost + remaining = original purchase cost
# --------------------------------------------------------------------------- #
def test_f_reconciliation_sold_plus_loss_plus_remaining(world):
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    _sell(world, web, data['open_name'], 45, 40, idem='sale-f')
    with world.app_context():
        from app.services.stock_adjustments import create_stock_adjustment
        create_stock_adjustment(
            material_name=data['open_name'], adjustment_date='2026-11-12',
            direction='LOSS', qty='5', reason='Damage / Broken',
            source='lost_ledger', actor='CementAdmin',
        )
        db.session.commit()

        summary = _pnl(world)['summary']
        sold_cost = summary['total_material_cost']
        loss_cost = summary['total_other_losses']
        remaining_kg = db.session.get(Material, data['open_id']).total
        remaining_cost = remaining_kg * 28.0
        assert sold_cost == pytest.approx(1260.0)
        assert loss_cost == pytest.approx(140.0)
        assert remaining_kg == pytest.approx(0.0)
        assert sold_cost + loss_cost + remaining_cost == pytest.approx(1400.0)


# --------------------------------------------------------------------------- #
# G. Different GRN rates produce per-layer traceability
# --------------------------------------------------------------------------- #
def test_g_multiple_grn_rates_keep_layer_traceability(world):
    data = _make_world(world, bags=50, rate=1400.0)
    with world.app_context():
        bag = db.session.get(Material, data['bag_id'])
        grn2 = GRN(
            supplier='Supplier B', auto_bill_no='SB-GRN-555', manual_bill_no='G-2',
            costing_mode='LANDED', date_posted=datetime(2026, 11, 3, 9, 0),
            created_by='CementAdmin',
        )
        db.session.add(grn2)
        db.session.flush()
        db.session.add(GRNItem(grn_id=grn2.id, mat_name=bag.name, qty=50, price_at_time=1600))
        db.session.add(Entry(
            date='2026-11-03', time='09:00:00', type='IN', material=bag.name,
            client='Supplier B', qty=50, bill_no='G-2', auto_bill_no='SB-GRN-555',
            created_by='CementAdmin',
        ))
        bag.total = 100.0
        db.session.commit()

    conv = _convert(world, data['bag_id'], data['open_id'], bags='60')
    with world.app_context():
        stored = db.session.get(CementConversion, conv['id'])
        layers = CementConversionLayer.query.filter_by(conversion_id=stored.id).order_by(
            CementConversionLayer.id.asc()
        ).all()
        assert len(layers) == 2
        assert layers[0].source_qty_bags == pytest.approx(50.0)
        assert layers[0].source_unit_cost == pytest.approx(1400.0)
        assert layers[0].kg_unit_cost == pytest.approx(28.0)
        assert layers[1].source_qty_bags == pytest.approx(10.0)
        assert layers[1].source_unit_cost == pytest.approx(1600.0)
        assert layers[1].kg_unit_cost == pytest.approx(32.0)
        assert stored.total_transferred_cost == pytest.approx(50 * 1400 + 10 * 1600)
        assert stored.source_cost_per_kg == pytest.approx((50 * 1400 + 10 * 1600) / (60 * 50))

        from app.services.profit_loss import _Projection
        lots = _Projection().build().available_fifo_lots(data['open_name'])
        conversion_lots = [lot for lot in lots if lot.get('cement_conversion_layer_id')]
        assert len(conversion_lots) == 2
        assert conversion_lots[0]['rate'] == pytest.approx(28.0)
        assert conversion_lots[1]['rate'] == pytest.approx(32.0)


# --------------------------------------------------------------------------- #
# H. Different brands are independent
# --------------------------------------------------------------------------- #
def test_h_brands_are_independent(world):
    data = _make_world(world, bags=10, rate=1400.0, bag_name='DG Cement', open_name='Open DG Cement')
    with world.app_context():
        bag2 = Material(name='Lucky Cement', code='LC', unit='Bags', total=0)
        db.session.add(bag2)
        db.session.flush()
        open2 = Material(
            name='Open Lucky Cement', code='OPLC', category_id=data['cat_id'],
            unit='KG', total=0, source_material_id=bag2.id,
        )
        db.session.add(open2)
        db.session.flush()
        grn2 = GRN(
            supplier='Supplier A', auto_bill_no='SB-GRN-LC', manual_bill_no='G-LC',
            costing_mode='LANDED', date_posted=datetime(2026, 11, 1, 10, 0),
            created_by='CementAdmin',
        )
        db.session.add(grn2)
        db.session.flush()
        db.session.add(GRNItem(grn_id=grn2.id, mat_name='Lucky Cement', qty=10, price_at_time=1400))
        db.session.add(Entry(
            date='2026-11-01', time='10:00:00', type='IN', material='Lucky Cement',
            client='Supplier A', qty=10, bill_no='G-LC', auto_bill_no='SB-GRN-LC',
            created_by='CementAdmin',
        ))
        bag2.total = 10.0
        db.session.commit()
        bag2_id, open2_id = bag2.id, open2.id

    _convert(world, data['bag_id'], data['open_id'], bags='2')
    _convert(world, bag2_id, open2_id, bags='3')
    with world.app_context():
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(8.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(100.0)
        assert db.session.get(Material, bag2_id).total == pytest.approx(7.0)
        assert db.session.get(Material, open2_id).total == pytest.approx(150.0)
        assert CementConversion.query.count() == 2


# --------------------------------------------------------------------------- #
# History / detail / reversal
# --------------------------------------------------------------------------- #
def test_history_and_detail_and_reversal(world):
    data = _make_world(world, bags=10, rate=1400.0)
    conv = _convert(world, data['bag_id'], data['open_id'], bags='2', note='first')
    web = world.test_client()
    _login(web)

    page = web.get('/inventory/cement_conversion')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert conv['reference'] in html
    assert 'first' in html

    detail = web.get(f"/inventory/cement_conversion/{conv['id']}")
    assert detail.status_code == 200
    assert conv['reference'] in detail.get_data(as_text=True)

    response = web.post(f"/inventory/cement_conversion/{conv['id']}/reverse", data={
        'reason': 'mistake', '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        stored = db.session.get(CementConversion, conv['id'])
        assert stored.status == 'REVERSED'
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(10.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(0.0)
        assert CementConversion.query.count() == 1
        assert CementConversionLayer.query.filter_by(conversion_id=stored.id).count() >= 1


def test_reversal_blocked_after_consumption(world):
    data = _make_world(world, bags=10, rate=1400.0)
    conv = _convert(world, data['bag_id'], data['open_id'], bags='2')
    web = world.test_client()
    _login(web)
    _sell(world, web, data['open_name'], 10, 40, idem='sale-rev')
    response = web.post(f"/inventory/cement_conversion/{conv['id']}/reverse", data={
        'reason': 'try', '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    assert any('cannot be reversed' in message for _, message in _flashes(web))
    with world.app_context():
        assert db.session.get(CementConversion, conv['id']).status == 'POSTED'


# --------------------------------------------------------------------------- #
# J. Security / concurrency / atomicity
# --------------------------------------------------------------------------- #
def test_j_duplicate_submit_is_idempotent(world):
    data = _make_world(world, bags=10, rate=1400.0)
    web = world.test_client()
    _login(web)
    payload = {
        'conversion_date': '2026-11-05',
        'source_material': str(data['bag_id']),
        'dest_material': str(data['open_id']),
        'bags': '2',
        'weight_per_bag': '50',
        'note': 'dup',
        'idempotency_key': 'same-key',
        '_csrf_token': _csrf(web),
    }
    first = web.post('/inventory/cement_conversion/save', data=payload)
    second = web.post('/inventory/cement_conversion/save', data=payload)
    assert first.status_code == 302 and second.status_code == 302
    with world.app_context():
        assert CementConversion.query.count() == 1
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(8.0)


def test_j_save_rejects_unlinked_destination(world):
    data = _make_world(world, bags=10, rate=1400.0)
    with world.app_context():
        other_open = Material(
            name='Open Other', code='OPOTH2', category_id=data['cat_id'],
            unit='KG', total=0, source_material_id=None,
        )
        db.session.add(other_open)
        db.session.commit()
        other_id = other_open.id
    web = world.test_client()
    _login(web)
    response = web.post('/inventory/cement_conversion/save', data={
        'conversion_date': '2026-11-05',
        'source_material': str(data['bag_id']),
        'dest_material': str(other_id),
        'bags': '1',
        'weight_per_bag': '50',
        'note': '',
        'idempotency_key': 'bad-dest',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    assert any('not linked' in message or 'Open Cement' in message
               for _, message in _flashes(web))
    with world.app_context():
        assert CementConversion.query.count() == 0


def test_j_insufficient_stock_is_refused_and_atomic(world):
    data = _make_world(world, bags=2, rate=1400.0)
    web = world.test_client()
    _login(web)
    response = web.post('/inventory/cement_conversion/save', data={
        'conversion_date': '2026-11-05',
        'source_material': str(data['bag_id']),
        'dest_material': str(data['open_id']),
        'bags': '5',
        'weight_per_bag': '50',
        'note': '',
        'idempotency_key': 'over',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        assert CementConversion.query.count() == 0
        assert Entry.query.filter_by(source_table='cement_conversion').count() == 0
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(2.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(0.0)


def test_j_rollback_on_invalid_destination_leaves_no_partial_conversion(world):
    data = _make_world(world, bags=10, rate=1400.0)
    with world.app_context():
        other_bag = Material(name='Plain Cement', code='PL', unit='Bags', total=10)
        db.session.add(other_bag)
        db.session.commit()
        other_bag_id = other_bag.id
    web = world.test_client()
    _login(web)
    response = web.post('/inventory/cement_conversion/save', data={
        'conversion_date': '2026-11-05',
        'source_material': str(data['bag_id']),
        'dest_material': str(other_bag_id),
        'bags': '3',
        'weight_per_bag': '50',
        'note': '',
        'idempotency_key': 'invalid-dest',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        assert CementConversion.query.count() == 0
        assert Entry.query.filter_by(source_table='cement_conversion').count() == 0
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(10.0)


def test_j_concurrent_conversions_are_serialised(world):
    """Two cashiers saving at the same moment must not both consume the stock."""
    data = _make_world(world, bags=10, rate=1400.0)
    results = []

    def worker(index):
        web = world.test_client()
        _login(web)
        payload = {
            'conversion_date': '2026-11-05',
            'source_material': str(data['bag_id']),
            'dest_material': str(data['open_id']),
            'bags': '6',
            'weight_per_bag': '50',
            'note': f'race-{index}',
            'idempotency_key': f'race-key-{index}',
            '_csrf_token': _csrf(web),
        }
        response = web.post('/inventory/cement_conversion/save', data=payload)
        results.append(response.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with world.app_context():
        # Only one 6-bag conversion can own the stock; the other is refused.
        assert CementConversion.query.count() == 1
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(4.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(300.0)


def test_j_permission_denied_for_unauthorized_role(world):
    data = _make_world(world, bags=10, rate=1400.0)
    with world.app_context():
        db.session.add(User(username='NoStock', password_hash='x', password_plain='x',
                            role='user', status='active', can_view_stock=False))
        db.session.commit()
    web = world.test_client()
    response = web.post('/login', data={'username': 'NoStock', 'password': 'x'})
    assert response.status_code == 302
    page = web.get('/inventory/cement_conversion')
    assert page.status_code in (302, 403)


def test_j_csrf_token_required(tmp_path, monkeypatch):
    """With CSRF enforcement forced on, a token-less POST is refused."""
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'cement_csrf.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health.json'))
    monkeypatch.setenv('SECRET_KEY', 'cement-conversion-csrf')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'CementAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'cement-test-password')
    app = create_app({
        'TESTING': True,
        'AMS_CSRF_ALWAYS': True,
        'INSTANCE_PATH': tmp_path / 'instance',
        'ENABLE_BACKGROUND_WORKERS': False,
        'DAILY_BACKUP_SCHEDULER': False,
        'SESSION_COOKIE_SECURE': False,
    })
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    with app.app_context():
        data = _make_world(app, bags=10, rate=1400.0)
    web = app.test_client()
    _login(web)
    response = web.post('/inventory/cement_conversion/save', data={
        'conversion_date': '2026-11-05',
        'source_material': str(data['bag_id']),
        'dest_material': str(data['open_id']),
        'bags': '1',
        'weight_per_bag': '50',
        'idempotency_key': 'no-csrf',
    })
    assert response.status_code in (400, 403)
    with app.app_context():
        assert CementConversion.query.count() == 0

    # The same payload with the session token is accepted.
    response = web.post('/inventory/cement_conversion/save', data={
        'conversion_date': '2026-11-05',
        'source_material': str(data['bag_id']),
        'dest_material': str(data['open_id']),
        'bags': '1',
        'weight_per_bag': '50',
        'idempotency_key': 'with-csrf',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with app.app_context():
        assert CementConversion.query.count() == 1


# --------------------------------------------------------------------------- #
# Migration 0018: additive, idempotent, safe on an existing database copy
# --------------------------------------------------------------------------- #
def test_migration_0018_upgrades_an_existing_database_copy(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / 'upgrade.db'
    monkeypatch.setenv('APP_DB_PATH', str(db_path))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health.json'))
    monkeypatch.setenv('SECRET_KEY', 'cement-migration-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'CementAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'cement-test-password')
    app = create_app({
        'TESTING': True, 'INSTANCE_PATH': tmp_path / 'instance',
        'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
        'SESSION_COOKIE_SECURE': False,
    })
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')

    # Seed one row first, then rewind the schema to "before 0018" (drop the new
    # tables/columns and the migration-history row), keeping every row intact.
    with app.app_context():
        from models import Material
        db.session.add(Material(name='DG Cement', code='DG', unit='Bags', total=5))
        db.session.commit()

    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM migration_history WHERE filename LIKE '0018_%'")
    for table in ('cement_conversion_layer', 'cement_conversion'):
        con.execute(f'DROP TABLE IF EXISTS {table}')
    for table, column in (('material', 'source_material_id'), ('material', 'bag_weight_kg'),
                          ('stock_adjustment', 'conversion_reference')):
        try:
            con.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
        except sqlite3.OperationalError:
            pass
    con.commit()
    before = con.execute('SELECT COUNT(*) FROM material').fetchone()[0]
    assert before == 1

    # Run the migration framework exactly as a boot would.
    from app.services.auto_migrate import run_sql_migrations
    report = run_sql_migrations(db_path=db_path)
    assert report['changed'] is True

    # Idempotent: a second run changes nothing.
    second = run_sql_migrations(db_path=db_path)
    assert second['changed'] is False

    con = sqlite3.connect(db_path)
    material_cols = {r[1] for r in con.execute("PRAGMA table_info('material')")}
    assert {'source_material_id', 'bag_weight_kg'} <= material_cols
    adj_cols = {r[1] for r in con.execute("PRAGMA table_info('stock_adjustment')")}
    assert 'conversion_reference' in adj_cols
    assert {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'cement_conversion%'"
    )} == {'cement_conversion', 'cement_conversion_layer'}
    # Existing data survived the upgrade.
    con = sqlite3.connect(db_path)
    assert con.execute('SELECT COUNT(*) FROM material').fetchone()[0] == before


# --------------------------------------------------------------------------- #
# Search / filters / category visibility
# --------------------------------------------------------------------------- #
def test_open_cement_category_is_visible_and_filterable(world):
    data = _make_world(world)
    web = world.test_client()
    _login(web)
    html = web.get('/materials').get_data(as_text=True)
    assert OPEN_CEMENT_CATEGORY_NAME in html
    assert data['open_name'] in html
    assert 'source_material_id' in html


def test_open_cement_item_shows_kg_and_source_link(world):
    data = _make_world(world)
    with world.app_context():
        open_mat = db.session.get(Material, data['open_id'])
        assert open_mat.unit == 'KG'
        assert open_mat.source_material_id == data['bag_id']
        assert is_open_cement_material(open_mat)
        assert not is_open_cement_material(db.session.get(Material, data['bag_id']))


# --------------------------------------------------------------------------- #
# D2. Keg entry on the sales sheet: kegs x kg/keg -> KG (server-side)
# --------------------------------------------------------------------------- #
def _driver_id(world):
    with world.app_context():
        if not DeliveryPerson.query.filter_by(name='Driver One').first():
            db.session.add(DeliveryPerson(name='Driver One', phone='0300'))
            db.session.commit()
        return DeliveryPerson.query.filter_by(name='Driver One').first().id


def test_d_keg_helper_derives_kg_from_kegs_and_kg_per_keg(world):
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    # 2 kegs x 20 kg/keg = 40 KG, even though the KG field says 999: the keg
    # helper is authoritative for Open Cement and the deduction is in KG.
    response = web.post('/add_direct_sale', data={
        'client_name': 'Walk-in Customer',
        'category': 'Cash',
        'sale_date': '2026-11-10 10:00',
        'product_name[]': [data['open_name']],
        'qty[]': ['999'],
        'keg_qty[]': ['2'],
        'keg_kg[]': ['20'],
        'unit_rate[]': ['40'],
        'payment_method': 'Cash',
        'paid_amount': '0',
        'delivery_person_id[]': [_driver_id(world)],
        'create_invoice': '1',
        'idempotency_key': 'keg-sale-1',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        assert db.session.get(Material, data['open_id']).total == pytest.approx(10.0)
        sale = DirectSale.query.one()
        item = DirectSaleItem.query.filter_by(sale_id=sale.id).one()
        assert item.qty == pytest.approx(40.0)
        assert item.cost_rate_at_sale == pytest.approx(28.0)
        # The bagged stock is untouched by an Open Cement keg sale.
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(99.0)


def test_d_keg_helper_defaults_to_50kg_per_keg(world):
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    response = web.post('/add_direct_sale', data={
        'client_name': 'Walk-in Customer',
        'category': 'Cash',
        'sale_date': '2026-11-10 10:00',
        'product_name[]': [data['open_name']],
        'qty[]': ['1'],
        'keg_qty[]': ['1'],
        'keg_kg[]': [''],
        'unit_rate[]': ['40'],
        'payment_method': 'Cash',
        'paid_amount': '0',
        'delivery_person_id[]': [_driver_id(world)],
        'create_invoice': '1',
        'idempotency_key': 'keg-sale-50',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        assert db.session.get(Material, data['open_id']).total == pytest.approx(0.0)
        item = DirectSaleItem.query.order_by(DirectSaleItem.id.desc()).first()
        assert item.qty == pytest.approx(50.0)


def test_d_keg_helper_ignores_bagged_rows(world):
    data = _make_world(world, bags=100, rate=1400.0)
    web = world.test_client()
    _login(web)
    # A bagged sale with keg fields posted must stay in bags: only Open Cement
    # is stocked in KG and only its rows use the keg helper.
    response = web.post('/add_direct_sale', data={
        'client_name': 'Walk-in Customer',
        'category': 'Cash',
        'sale_date': '2026-11-10 10:00',
        'product_name[]': [data['bag_name']],
        'material_id[]': [str(data['bag_id'])],
        'qty[]': ['2'],
        'keg_qty[]': ['10'],
        'keg_kg[]': ['50'],
        'unit_rate[]': ['1500'],
        'payment_method': 'Cash',
        'paid_amount': '0',
        'delivery_person_id[]': [_driver_id(world)],
        'create_invoice': '1',
        'idempotency_key': 'keg-sale-bag',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(98.0)
        item = DirectSaleItem.query.order_by(DirectSaleItem.id.desc()).first()
        assert item.qty == pytest.approx(2.0)
    # A keg entry that exceeds the open stock is refused, not partially sold.
    data2 = _make_world(world, bags=100, rate=1400.0, bag_name='DG Cement', open_name='Open DG Cement')
    with world.app_context():
        from app.services.cement_conversion import create_conversion
        create_conversion(
            source_material=db.session.get(Material, data2['bag_id']),
            dest_material=db.session.get(Material, data2['open_id']),
            conversion_date='2026-11-05', bags='1', actor='CementAdmin',
        )
        db.session.commit()
    web2 = world.test_client()
    _login(web2)
    response = web2.post('/add_direct_sale', data={
        'client_name': 'Walk-in Customer',
        'category': 'Cash',
        'sale_date': '2026-11-10 10:00',
        'product_name[]': [data2['open_name']],
        'material_id[]': [str(data2['open_id'])],
        'qty[]': ['1'],
        'keg_qty[]': ['5'],
        'keg_kg[]': ['50'],
        'unit_rate[]': ['40'],
        'payment_method': 'Cash',
        'paid_amount': '0',
        'delivery_person_id[]': [_driver_id(world)],
        'create_invoice': '1',
        'idempotency_key': 'keg-sale-over',
        '_csrf_token': _csrf(web2),
    })
    assert response.status_code == 302
    assert any('Insufficient stock' in m for _, m in _flashes(web2))
    with world.app_context():
        assert db.session.get(Material, data2['open_id']).total == pytest.approx(50.0)
        assert DirectSale.query.count() == 1  # only the bagged sale above


def test_j_backdate_restricted_user_cannot_convert_old_date(world):
    """A back-date-limited account may not create an old conversion."""
    data = _make_world(world, bags=10, rate=1400.0)
    with world.app_context():
        db.session.add(User(
            username='BackdateClerk', password_hash='x', password_plain='x',
            role='user', status='active', can_view_stock=True,
            restrict_backdated_edit=True,
        ))
        db.session.commit()
    web = world.test_client()
    _login(web, username='BackdateClerk', password='x')
    response = web.post('/inventory/cement_conversion/save', data={
        'conversion_date': '2026-01-01',   # older than the 90-day back-date window
        'source_material': str(data['bag_id']),
        'dest_material': str(data['open_id']),
        'bags': '1',
        'weight_per_bag': '50',
        'idempotency_key': 'backdate-old',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    assert any('blocked' in m.lower() for _, m in _flashes(web))
    with world.app_context():
        assert CementConversion.query.count() == 0
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(10.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(0.0)

    # The same account can still convert a current-dated transaction.
    from app.services.time_money import pk_now
    today = pk_now().strftime('%Y-%m-%d')
    response = web.post('/inventory/cement_conversion/save', data={
        'conversion_date': today,
        'source_material': str(data['bag_id']),
        'dest_material': str(data['open_id']),
        'bags': '1',
        'weight_per_bag': '50',
        'idempotency_key': 'backdate-today',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        assert CementConversion.query.count() == 1
        assert db.session.get(Material, data['bag_id']).total == pytest.approx(9.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(50.0)


def test_d_edit_sale_keg_helper_derives_kg(world):
    """Editing a saved Open Cement bill also derives KG from the keg helper."""
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    _sell(world, web, data['open_name'], 10, 40, idem='edit-keg-base')
    with world.app_context():
        sale = DirectSale.query.one()
        sale_id = sale.id
        person_id = DeliveryPerson.query.filter_by(name='Driver One').first().id
    response = web.post(f'/edit_bill/DirectSale/{sale_id}', data={
        'category': 'Cash',
        'client_name': 'Walk-in Customer',
        'sale_date': '2026-11-10 10:00',
        'product_name[]': [data['open_name']],
        'material_id[]': [str(data['open_id'])],
        'qty[]': ['1'],
        'keg_qty[]': ['1'],
        'keg_kg[]': ['30'],
        'unit_rate[]': ['40'],
        'payment_method': 'Cash',
        'paid_amount': '0',
        'delivery_person_id[]': [str(person_id)],
        'create_invoice': '1',
        'idempotency_key': 'edit-keg-save',
        '_csrf_token': _csrf(web),
    })
    assert response.status_code == 302
    with world.app_context():
        sale = db.session.get(DirectSale, sale_id)
        item = DirectSaleItem.query.filter_by(sale_id=sale.id).one()
        assert item.qty == pytest.approx(30.0)
        assert db.session.get(Material, data['open_id']).total == pytest.approx(20.0)


def test_d_edit_modal_offers_keg_helper_for_saved_open_cement_sale(world):
    """Re-opening a saved Open Cement bill shows the keg helper on its row."""
    data = _make_world(world, bags=100, rate=1400.0)
    _convert(world, data['bag_id'], data['open_id'], bags='1')
    web = world.test_client()
    _login(web)
    _sell(world, web, data['open_name'], 10, 40, idem='modal-keg')
    with world.app_context():
        sale_id = DirectSale.query.one().id
    html = web.get(f'/direct_sales/{sale_id}/edit-modal').get_data(as_text=True)
    assert 'keg-helper' in html
    assert 'keg_qty[]' in html and 'keg_kg[]' in html
    assert f'data-open-cement="1"' in html
    page = web.get('/direct_sales').get_data(as_text=True)
    assert f'OPEN_CEMENT_MATERIAL_NAMES = ["{data["open_name"]}"]' in page
