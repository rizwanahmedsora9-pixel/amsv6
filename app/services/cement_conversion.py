"""Cement bag -> open cement (KG) conversion domain service.

The conversion is an *internal stock transfer*:

* it consumes the source bagged item's oldest FIFO purchase layers,
* it carries their cost across to the linked Open Cement item,
* it writes two linked ``Entry`` rows (source OUT, destination IN) that share
  this conversion's id,
* it writes one ``CementConversionLayer`` row per consumed purchase layer.

It never creates a purchase, payable, receivable, cash movement, sale or
expense.  ``Profit & Loss`` is untouched by the transfer itself; the
destination's cost is only recognised later, when the open cement is sold or
lost, through the same FIFO replay that costs everything else.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date as _date
from typing import Optional

from sqlalchemy import func

from models import (
    AccountingAuditLog,
    CementConversion,
    CementConversionLayer,
    Entry,
    GRN,
    GRNItem,
    Material,
    db,
)
from app.services.billing import get_next_bill_no
from app.services.grn_consumption import build_fifo_snapshot
from app.services.lookups import get_material_by_input
from app.services.open_cement import (
    DEFAULT_BAG_WEIGHT_KG,
    ensure_open_cement_category,
    is_open_cement_material,
    source_bag_weight_kg,
)
from app.services.time_money import pk_now
from app.services.void_rebuild import _rebuild_material_totals

LOG = logging.getLogger(__name__)

EPS = 1e-6


# --------------------------------------------------------------------------- #
# Small parsing helpers
# --------------------------------------------------------------------------- #
def _parse_date(value, *, default_today=True) -> str:
    raw = (value or '').strip()
    if not raw:
        if default_today:
            return pk_now().strftime('%Y-%m-%d')
        raise ValueError('Conversion date is required.')
    try:
        return datetime.strptime(raw[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError as exc:
        raise ValueError(f'Invalid date "{raw}". Expected YYYY-MM-DD.') from exc


def _parse_positive(value, label) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a valid number.') from exc
    if number <= EPS:
        raise ValueError(f'{label} must be greater than zero.')
    return number


def _day(value: str) -> Optional[_date]:
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Cost preview / allocation
# --------------------------------------------------------------------------- #
def allocate_source_lots(source_material, as_of_date, bags, weight_per_bag):
    """Allocate *bags* over the source's open FIFO purchase layers.

    Returns a dict with the layer rows, totals and a coverage flag.  Only real
    GRN lots are used; the existing costing method (FIFO over landed GRN cost)
    is never replaced by a second valuation.
    """
    bags = max(0.0, float(bags or 0.0))
    weight = max(0.0, float(weight_per_bag or 0.0))
    if bags <= EPS or weight <= EPS:
        return {
            'layers': [], 'bags_covered': 0.0, 'bags_requested': bags,
            'kg_produced': 0.0, 'total_cost': 0.0, 'covered': False,
            'cost_per_bag': 0.0, 'cost_per_kg': 0.0, 'basis': '',
        }

    as_of_dt = _day(as_of_date) if isinstance(as_of_date, str) else as_of_date
    snapshot = build_fifo_snapshot(
        as_of_dt=as_of_dt,
        fifo_only=True,
        material_scope=[source_material.name],
    )
    lots = [
        lot for lot in snapshot.available_fifo_lots(source_material.name, as_of_dt=as_of_dt)
        if lot.get('grn_item_id') and float(lot.get('available') or 0) > EPS
    ]

    remaining = bags
    layers = []
    total_cost = 0.0
    refs = []
    for lot in lots:
        if remaining <= EPS:
            break
        available = float(lot.get('available') or 0.0)
        rate = float(lot.get('rate') or 0.0)
        if available <= EPS or rate <= EPS:
            continue
        take = min(remaining, available)
        kg = round(take * weight, 6)
        amount = round(take * rate, 2)
        grn_item = db.session.get(GRNItem, int(lot['grn_item_id']))
        grn = db.session.get(GRN, grn_item.grn_id) if grn_item else None
        layers.append({
            'grn_item_id': int(lot['grn_item_id']),
            'grn_id': (grn.id if grn else lot.get('grn_id')),
            'reference': (lot.get('reference') or '').strip(),
            'supplier': (lot.get('supplier') or '').strip(),
            'lot_date': (lot.get('date') or '').strftime('%Y-%m-%d') if hasattr(lot.get('date'), 'strftime') else str(lot.get('date') or ''),
            'source_qty_bags': round(take, 6),
            'source_unit_cost': round(rate, 6),
            'kg_qty': kg,
            'kg_unit_cost': round(rate / weight, 6),
            'cost_amount': amount,
        })
        total_cost += amount
        remaining -= take
        ref = (lot.get('reference') or '').strip()
        if ref and ref not in refs:
            refs.append(ref)

    covered = remaining <= EPS
    kg_produced = round(bags * weight, 6)
    cost_per_bag = round(total_cost / bags, 6) if bags > EPS else 0.0
    cost_per_kg = round(total_cost / kg_produced, 6) if kg_produced > EPS else 0.0
    if covered:
        basis = 'FIFO GRN lots: ' + ', '.join(refs[:4]) if refs else 'FIFO GRN lots'
    else:
        basis = (
            f'Only {bags - remaining:,.4f} of {bags:,.4f} bags have a traceable '
            'FIFO purchase layer on/before this date.'
        )
    return {
        'layers': layers,
        'bags_covered': round(bags - remaining, 6),
        'bags_requested': bags,
        'kg_produced': kg_produced,
        'total_cost': round(total_cost, 2),
        'covered': covered,
        'cost_per_bag': cost_per_bag,
        'cost_per_kg': cost_per_kg,
        'basis': basis,
    }


def preview_conversion(source_material, as_of_date, bags, weight_per_bag=None):
    """Public preview used by the conversion form (no writes)."""
    if source_material is None:
        raise ValueError('Source cement item is required.')
    if is_open_cement_material(source_material):
        raise ValueError('Open Cement items cannot be used as a conversion source.')
    if weight_per_bag is None:
        weight_per_bag = source_bag_weight_kg(source_material)
    weight = _parse_positive(weight_per_bag, 'Weight per bag')
    bags_f = _parse_positive(bags, 'Conversion quantity')
    date_str = _parse_date(as_of_date)
    available_stock = round(float(source_material.total or 0.0), 6)
    alloc = allocate_source_lots(source_material, date_str, bags_f, weight)
    return {
        'source_material': source_material,
        'date': date_str,
        'bags': bags_f,
        'weight_per_bag': weight,
        'available_stock': available_stock,
        'kg_produced': alloc['kg_produced'],
        'total_cost': alloc['total_cost'],
        'cost_per_bag': alloc['cost_per_bag'],
        'cost_per_kg': alloc['cost_per_kg'],
        'basis': alloc['basis'],
        'layers': alloc['layers'],
        'covered': alloc['covered'],
        'sufficient_stock': available_stock + EPS >= bags_f,
        'valid': alloc['covered'] and available_stock + EPS >= bags_f,
    }


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
def _make_reference() -> str:
    from app.services.constants import AUTO_BILL_NAMESPACES
    return get_next_bill_no(AUTO_BILL_NAMESPACES['CEMENT_CONVERSION'])


def create_conversion(
    *,
    source_material: Material,
    dest_material: Material,
    conversion_date=None,
    bags=None,
    weight_per_bag=None,
    note='',
    actor='System',
    actor_user=None,
    idempotency_key=None,
) -> CementConversion:
    """Create the conversion header, both ledger movements and cost layers.

    The caller owns the transaction (and, on SQLite, should already hold the
    write lock).  Raises ``ValueError`` with a user-facing message; nothing is
    committed here.
    """
    if source_material is None or dest_material is None:
        raise ValueError('Both a source and a destination item are required.')
    # Duplicate submit (double click / retried POST): the same idempotency key
    # already produced a conversion, so return it instead of converting twice.
    key = (idempotency_key or '').strip()[:64]
    if key:
        existing = CementConversion.query.filter_by(idempotency_key=key).first()
        if existing is not None:
            return existing
    if is_open_cement_material(source_material):
        raise ValueError('Open Cement items cannot be used as a conversion source.')
    if not is_open_cement_material(dest_material):
        raise ValueError('The destination must be an item in the Open Cement category.')
    if int(getattr(dest_material, 'source_material_id', 0) or 0) != int(source_material.id):
        raise ValueError(
            f'"{dest_material.name}" is not linked to "{source_material.name}". '
            'Link the Open Cement item to its bagged cement item first.'
        )

    date_str = _parse_date(conversion_date)
    bags_f = _parse_positive(bags, 'Conversion quantity')
    weight = (
        source_bag_weight_kg(source_material)
        if weight_per_bag is None or str(weight_per_bag).strip() == ''
        else _parse_positive(weight_per_bag, 'Weight per bag')
    )

    available_stock = round(float(source_material.total or 0.0), 6)
    if available_stock + EPS < bags_f:
        raise ValueError(
            f'Insufficient stock for {source_material.name}. '
            f'Available: {available_stock:g} {source_material.unit or "Bags"}, '
            f'requested: {bags_f:g}.'
        )

    alloc = allocate_source_lots(source_material, date_str, bags_f, weight)
    if not alloc['covered']:
        raise ValueError(
            f'Cannot convert {bags_f:g} bags of {source_material.name}: '
            f'{alloc["basis"]} Convert a smaller quantity or receive more stock first.'
        )

    now = pk_now()
    time_str = now.strftime('%H:%M:%S')
    reference = _make_reference()

    conv = CementConversion(
        reference=reference,
        conversion_date=date_str,
        conversion_time=time_str,
        date_posted=now,
        source_material_id=source_material.id,
        source_material=source_material.name,
        source_unit=source_material.unit or 'Bags',
        bags_converted=round(bags_f, 6),
        weight_per_bag_kg=round(weight, 6),
        kg_produced=alloc['kg_produced'],
        dest_material_id=dest_material.id,
        dest_material=dest_material.name,
        dest_unit=dest_material.unit or 'KG',
        source_cost_per_bag=alloc['cost_per_bag'],
        source_cost_per_kg=alloc['cost_per_kg'],
        total_transferred_cost=alloc['total_cost'],
        cost_basis=alloc['basis'][:400],
        status='POSTED',
        note=(note or '').strip()[:500],
        idempotency_key=(idempotency_key or '').strip()[:64] or None,
        created_by=actor,
        updated_by=actor,
    )
    db.session.add(conv)
    db.session.flush()

    common = dict(
        date=date_str,
        time=time_str,
        client='',
        client_code=None,
        client_category='Cement Conversion',
        created_by=actor,
        source_module='inventory',
        source_table='cement_conversion',
        source_id=conv.id,
        source_bill_no=reference,
        is_void=False,
    )
    source_entry = Entry(
        type='OUT',
        material=source_material.name,
        qty=round(bags_f, 6),
        bill_no=reference,
        auto_bill_no=reference,
        nimbus_no='Cement Conversion',
        transaction_category='Cement Conversion',
        transaction_type='Conversion Out',
        note=(note or '').strip()[:500],
        **common,
    )
    dest_entry = Entry(
        type='IN',
        material=dest_material.name,
        qty=alloc['kg_produced'],
        bill_no=reference,
        auto_bill_no=reference,
        nimbus_no='Cement Conversion',
        transaction_category='Cement Conversion',
        transaction_type='Conversion In',
        note=(note or '').strip()[:500],
        **common,
    )
    db.session.add(source_entry)
    db.session.add(dest_entry)
    db.session.flush()
    conv.source_entry_id = source_entry.id
    conv.dest_entry_id = dest_entry.id

    for layer in alloc['layers']:
        db.session.add(CementConversionLayer(
            conversion_id=conv.id,
            grn_item_id=layer['grn_item_id'],
            grn_id=layer['grn_id'],
            reference=layer['reference'],
            supplier=layer['supplier'],
            lot_date=layer['lot_date'],
            source_qty_bags=layer['source_qty_bags'],
            source_unit_cost=layer['source_unit_cost'],
            kg_qty=layer['kg_qty'],
            kg_unit_cost=layer['kg_unit_cost'],
            cost_amount=layer['cost_amount'],
            is_void=False,
        ))

    _rebuild_material_totals()
    db.session.flush()

    # Snapshot the post-conversion stock for the history screen.
    source_material = db.session.get(Material, int(source_material.id))
    dest_material = db.session.get(Material, int(dest_material.id))
    conv.source_stock_after = round(float(source_material.total or 0.0), 6) if source_material else None
    conv.dest_stock_after = round(float(dest_material.total or 0.0), 6) if dest_material else None
    db.session.flush()

    db.session.add(AccountingAuditLog(
        module='inventory',
        action='cement_conversion.create',
        entity_type='CementConversion',
        entity_id=conv.id,
        user_id=getattr(actor_user, 'id', None),
        username=actor,
        after_json=json.dumps({
            'id': conv.id,
            'reference': conv.reference,
            'date': conv.conversion_date,
            'source_material': conv.source_material,
            'dest_material': conv.dest_material,
            'bags': conv.bags_converted,
            'weight_per_bag_kg': conv.weight_per_bag_kg,
            'kg_produced': conv.kg_produced,
            'source_cost_per_bag': conv.source_cost_per_bag,
            'total_transferred_cost': conv.total_transferred_cost,
            'source_entry_id': conv.source_entry_id,
            'dest_entry_id': conv.dest_entry_id,
            'actor': actor,
        }, default=str)[:1500],
    ))
    return conv


# --------------------------------------------------------------------------- #
# Reversal
# --------------------------------------------------------------------------- #
def conversion_layer_remaining(conversion: CementConversion, *, as_of_date=None) -> float:
    """Kilograms of this conversion's cost layers still on hand.

    Uses the same authoritative FIFO replay as costing, so a later sale or
    loss of the open cement is reflected here.
    """
    as_of_dt = _day(as_of_date) if isinstance(as_of_date, str) else as_of_date
    snapshot = build_fifo_snapshot(
        as_of_dt=as_of_dt,
        fifo_only=True,
        material_scope=[conversion.dest_material],
    )
    layer_ids = {
        int(layer.id)
        for layer in CementConversionLayer.query.filter_by(conversion_id=conversion.id).all()
    }
    if not layer_ids:
        return 0.0
    total = 0.0
    for lot in snapshot.available_fifo_lots(conversion.dest_material, as_of_dt=as_of_dt):
        if int(lot.get('cement_conversion_layer_id') or 0) in layer_ids:
            total += float(lot.get('available') or 0.0)
    return round(total, 6)


def reverse_conversion(
    conversion: CementConversion,
    *,
    actor='System',
    actor_user=None,
    reason='',
) -> CementConversion:
    """Reverse a conversion whose open-cement stock is still untouched.

    The source OUT entry is voided (restoring the bag to its GRN lot) and a
    compensating destination OUT entry consumes the conversion's cost layers.
    The header keeps a permanent REVERSED audit state; nothing is hard-deleted.
    """
    if conversion is None:
        raise ValueError('Conversion not found.')
    if (conversion.status or 'POSTED').upper() == 'REVERSED':
        raise ValueError(f'{conversion.reference} is already reversed.')

    dest = db.session.get(Material, conversion.dest_material_id) if conversion.dest_material_id else None
    if dest is None:
        dest = Material.query.filter(
            func.lower(func.trim(Material.name)) == (conversion.dest_material or '').strip().lower()
        ).first()
    if dest is None:
        raise ValueError(f'Destination item "{conversion.dest_material}" no longer exists.')

    remaining = conversion_layer_remaining(conversion)
    kg = float(conversion.kg_produced or 0.0)
    if remaining + EPS < kg:
        raise ValueError(
            f'{conversion.reference} cannot be reversed: only {remaining:g} of '
            f'{kg:g} kg produced are still on hand. The rest has been sold, lost '
            'or consumed. Record compensating transactions instead.'
        )

    now = pk_now()
    conversion.status = 'REVERSED'
    conversion.reversed_at = now
    conversion.reversed_by = actor
    conversion.reverse_reason = (reason or '').strip()[:300]
    conversion.updated_by = actor

    # Void the original source OUT movement.  The destination IN stays: the
    # reversal below is the compensating OUT that consumes the cost layers, so
    # the ledger reads "converted in 50 kg, reversed out 50 kg".
    source_entry = db.session.get(Entry, conversion.source_entry_id) if conversion.source_entry_id else None
    if source_entry is not None and not source_entry.is_void:
        source_entry.is_void = True
        suffix = f'Reversed by {conversion.reference} on {now.strftime("%Y-%m-%d")}'
        source_entry.note = (f'{source_entry.note or ""} | {suffix}').strip(' |')[:500]

    reversal_entry = Entry(
        date=now.strftime('%Y-%m-%d'),
        time=now.strftime('%H:%M:%S'),
        type='OUT',
        material=dest.name,
        client='',
        client_code=None,
        client_category='Cement Conversion',
        qty=kg,
        bill_no=conversion.reference,
        auto_bill_no=conversion.reference,
        nimbus_no='Cement Conversion',
        transaction_category='Cement Conversion',
        transaction_type='Conversion Reversal',
        note=(f'Reversal of {conversion.reference}' + (f' — {reason}' if reason else ''))[:500],
        created_by=actor,
        source_module='inventory',
        source_table='cement_conversion',
        source_id=conversion.id,
        source_bill_no=conversion.reference,
        is_void=False,
    )
    db.session.add(reversal_entry)
    db.session.flush()
    conversion.reversal_entry_id = reversal_entry.id

    _rebuild_material_totals()
    db.session.flush()

    db.session.add(AccountingAuditLog(
        module='inventory',
        action='cement_conversion.reverse',
        entity_type='CementConversion',
        entity_id=conversion.id,
        user_id=getattr(actor_user, 'id', None),
        username=actor,
        before_json=json.dumps({'status': 'POSTED'}, default=str),
        after_json=json.dumps({
            'id': conversion.id,
            'reference': conversion.reference,
            'kg_reversed': kg,
            'reversal_entry_id': reversal_entry.id,
            'reason': reason,
            'actor': actor,
        }, default=str)[:1500],
        reason=(reason or '')[:500],
    ))
    return conversion


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def conversion_history(
    *,
    date_from=None,
    date_to=None,
    source_material='',
    dest_material='',
    q='',
    page=1,
    per_page=25,
):
    query = CementConversion.query
    if date_from:
        query = query.filter(CementConversion.conversion_date >= date_from)
    if date_to:
        query = query.filter(CementConversion.conversion_date <= date_to)
    if source_material:
        query = query.filter(
            func.lower(func.trim(CementConversion.source_material)) == source_material.strip().lower()
        )
    if dest_material:
        query = query.filter(
            func.lower(func.trim(CementConversion.dest_material)) == dest_material.strip().lower()
        )
    if q:
        term = f'%{q.strip()}%'
        query = query.filter(
            CementConversion.reference.ilike(term)
            | CementConversion.source_material.ilike(term)
            | CementConversion.dest_material.ilike(term)
            | CementConversion.note.ilike(term)
            | CementConversion.created_by.ilike(term)
        )
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = min(100, max(5, int(per_page)))
    except (TypeError, ValueError):
        per_page = 25
    total = query.count()
    rows = query.order_by(
        CementConversion.conversion_date.desc(),
        CementConversion.id.desc(),
    ).offset((page - 1) * per_page).limit(per_page).all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        'rows': rows,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': total_pages,
    }


def conversion_detail(conversion: CementConversion) -> dict:
    """Complete transaction detail for an authorised viewer."""
    source = None
    dest = None
    if conversion.source_material_id:
        source = db.session.get(Material, int(conversion.source_material_id))
    if conversion.dest_material_id:
        dest = db.session.get(Material, int(conversion.dest_material_id))
    layers = CementConversionLayer.query.filter_by(conversion_id=conversion.id).order_by(
        CementConversionLayer.id.asc()
    ).all()
    source_entry = db.session.get(Entry, conversion.source_entry_id) if conversion.source_entry_id else None
    dest_entry = db.session.get(Entry, conversion.dest_entry_id) if conversion.dest_entry_id else None
    reversal_entry = db.session.get(Entry, conversion.reversal_entry_id) if conversion.reversal_entry_id else None
    audit_rows = []
    try:
        audit_rows = (
            AccountingAuditLog.query
            .filter(
                (AccountingAuditLog.entity_type == 'CementConversion')
                & (AccountingAuditLog.entity_id == conversion.id)
            )
            .order_by(AccountingAuditLog.created_at.asc(), AccountingAuditLog.id.asc())
            .all()
        )
    except Exception:
        audit_rows = []
    return {
        'conv': conversion,
        'conversion': conversion,
        'source': source,
        'dest': dest,
        'layers': layers,
        'source_entry': source_entry,
        'dest_entry': dest_entry,
        'reversal_entry': reversal_entry,
        'layer_remaining_kg': conversion_layer_remaining(conversion),
        'audit': audit_rows,
    }
