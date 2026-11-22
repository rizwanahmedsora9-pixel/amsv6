"""Domain service for Lost Ledger & Physical Stock Adjustments."""
from __future__ import annotations

import json
import logging
from datetime import datetime, date as _date

from sqlalchemy import case, func

from models import AuditLog, CementConversion, Entry, Material, StockAdjustment, db
from app.services import constants as C
from app.services.billing import get_next_bill_no
from app.services.lookups import get_material_by_input, is_service_name
from app.services.time_money import pk_now
from app.services.void_rebuild import _rebuild_material_totals, _set_entry_void_state

LOG = logging.getLogger(__name__)

EPS = 1e-6

LOSS_REASONS = (
    'Damage / Broken',
    'Leakage / Spillage',
    'Theft / Missing',
    'Moisture / Weight Loss',
    'Expired / Spoiled',
    'Counting Error',
    'Shortage',
    'Physical Count Shortage',
    'Other',
)

GAIN_REASONS = (
    'Found Extra / Surplus',
    'Counting Error',
    'Physical Count Surplus',
    'Return Adjustment',
    'Other',
)

ALL_REASONS = tuple(dict.fromkeys(LOSS_REASONS + GAIN_REASONS))


def _parse_date_str(value, *, default_today=True):
    raw = (value or '').strip()
    if not raw:
        if default_today:
            return pk_now().strftime('%Y-%m-%d')
        raise ValueError('Adjustment date is required.')
    try:
        parsed = datetime.strptime(raw[:10], '%Y-%m-%d')
        return parsed.strftime('%Y-%m-%d')
    except ValueError as exc:
        raise ValueError(f'Invalid date "{raw}". Expected YYYY-MM-DD.') from exc


def _parse_time_str(value):
    raw = (value or '').strip()
    if not raw:
        return pk_now().strftime('%H:%M:%S')
    for fmt in ('%H:%M:%S', '%H:%M'):
        try:
            return datetime.strptime(raw, fmt).strftime('%H:%M:%S')
        except ValueError:
            continue
    return pk_now().strftime('%H:%M:%S')


def _compose_datetime(date_str, time_str):
    try:
        return datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M:%S')
    except Exception:
        return pk_now()


def _is_open_cement(mat) -> bool:
    """True when *mat* belongs to the reserved Open Cement category."""
    try:
        from app.services.open_cement import is_open_cement_material
        return is_open_cement_material(mat)
    except Exception:
        return False


def _resolve_material(material_input):
    raw = (material_input or '').strip()
    if not raw:
        raise ValueError('Material is required.')
    mat = get_material_by_input(raw)
    if mat:
        return mat
    if is_service_name(raw):
        raise ValueError(f'"{raw}" is a service, not physical inventory, and cannot be adjusted.')
    raise ValueError(f'Material "{raw}" not found in inventory master.')


def system_stock_as_of(material_name, as_of_date=None, *, exclude_entry_id=None):
    """Authoritative system stock balance for ``material_name`` through ``as_of_date``."""
    date_str = _parse_date_str(as_of_date, default_today=True)
    q = db.session.query(
        func.sum(
            case(
                (Entry.type == 'IN', Entry.qty),
                (Entry.type == 'OUT', -Entry.qty),
                else_=0,
            )
        )
    ).filter(
        Entry.is_void == False,
        Entry.material == material_name,
        Entry.date <= date_str,
    )
    if exclude_entry_id:
        q = q.filter(Entry.id != int(exclude_entry_id))
    return round(float(q.scalar() or 0.0), 6)


def _get_projection():
    from app.services.profit_loss import _Projection
    return _Projection().build()


def estimate_adjustment_cost(
    material_name,
    as_of_date=None,
    qty=1.0,
    direction='LOSS',
    *,
    projection=None,
):
    """Estimate FIFO / fallback unit cost and total Rs. impact for an adjustment.

    Returns ``(unit_cost, total_value, cost_source)``.
    """
    qty_f = max(0.0, float(qty or 0.0))
    date_str = _parse_date_str(as_of_date, default_today=True)
    try:
        as_of_dt = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        as_of_dt = pk_now().date()

    if projection is None:
        projection = _get_projection()

    dir_norm = (direction or 'LOSS').strip().upper()
    if dir_norm == 'LOSS':
        lots = projection.available_fifo_lots(material_name, as_of_dt)
        remaining = qty_f
        total_cost = 0.0
        refs = []
        for lot in lots:
            if remaining <= EPS:
                break
            avail = max(0.0, float(lot.get('available') or 0.0))
            rate = max(0.0, float(lot.get('rate') or 0.0))
            if avail <= EPS or rate <= 0:
                continue
            take = min(remaining, avail)
            total_cost += take * rate
            remaining -= take
            ref = (lot.get('reference') or '').strip()
            if ref and ref not in refs:
                refs.append(ref)
        if remaining > EPS:
            fb_rate, fb_basis, _ = projection.fallback_cost(material_name, as_of_dt)
            if fb_rate > 0:
                total_cost += remaining * fb_rate
                if fb_basis:
                    refs.append(fb_basis)
                remaining = 0.0
        if qty_f > EPS and total_cost > 0:
            unit_cost = total_cost / qty_f
            total_value = round(total_cost, 2)
            source = f"FIFO GRN lots ({', '.join(refs[:3])})" if refs else 'Estimated stock cost'
            return round(unit_cost, 4), total_value, source

    # GAIN or no FIFO lot coverage on LOSS -> fallback cost (latest GRN / master price)
    fb_rate, fb_basis, _ = projection.fallback_cost(material_name, as_of_dt)
    if fb_rate > 0:
        unit_cost = round(float(fb_rate), 4)
        total_value = round(qty_f * unit_cost, 2)
        return unit_cost, total_value, fb_basis or 'Latest GRN / material rate'

    mat = Material.query.filter_by(name=material_name).first()
    master_price = float(getattr(mat, 'unit_price', 0.0) or 0.0) if mat else 0.0
    if master_price > 0:
        return round(master_price, 4), round(qty_f * master_price, 2), 'Material master unit price'
    return 0.0, 0.0, 'Cost unresolved (no GRN or unit price)'


def estimate_adjustment_conversion_reference(
    material_name,
    as_of_date=None,
    qty=1.0,
    direction='LOSS',
    *,
    projection=None,
):
    """Return the cement conversion reference this adjustment is costed from.

    For Open Cement the FIFO lots are dated synthetic lots created by a bag ->
    KG conversion, so the loss cost is inherited from that conversion.  This
    returns its ``SB-CC-####`` reference (or ``None`` for a GRN-derived or
    manual-cost adjustment) so the Lost Ledger row keeps a permanent link back
    to the transfer that produced the stock.
    """
    qty_f = max(0.0, float(qty or 0.0))
    date_str = _parse_date_str(as_of_date, default_today=True)
    try:
        as_of_dt = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        as_of_dt = pk_now().date()
    if projection is None:
        projection = _get_projection()
    if (direction or 'LOSS').strip().upper() != 'LOSS':
        return None
    remaining = qty_f
    for lot in projection.available_fifo_lots(material_name, as_of_dt):
        if remaining <= EPS:
            break
        avail = max(0.0, float(lot.get('available') or 0.0))
        if avail <= EPS:
            continue
        layer_id = int(lot.get('cement_conversion_layer_id') or 0)
        if layer_id:
            from models import CementConversionLayer
            layer = db.session.get(CementConversionLayer, layer_id)
            conv = db.session.get(CementConversion, layer.conversion_id) if layer else None
            if conv is not None:
                return conv.reference
        remaining -= min(remaining, avail)
    return None


def build_material_cost_map(as_of_date=None, *, projection=None):
    """Return precomputed cost info per material name for UI previews (one projection pass)."""
    date_str = _parse_date_str(as_of_date, default_today=True)
    try:
        as_of_dt = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        as_of_dt = pk_now().date()
    if projection is None:
        projection = _get_projection()

    out = {}
    for mat in Material.query.order_by(Material.name.asc()).all():
        lots = projection.available_fifo_lots(mat.name, as_of_dt)
        if lots:
            front = lots[0]
            rate = round(float(front.get('rate') or 0.0), 4)
            source = f"FIFO lot {front.get('reference') or ''}".strip()
        else:
            fb_rate, fb_basis, _ = projection.fallback_cost(mat.name, as_of_dt)
            rate = round(float(fb_rate or 0.0), 4)
            source = fb_basis or ('Material master unit price' if rate > 0 else 'No GRN / unit price')
        out[mat.name] = {
            'unit_cost': rate,
            'source': source,
            'unit': mat.unit or 'Bags',
            'current_total': float(mat.total or 0.0),
        }
    return out


def _entry_note_text(reason, note):
    r = (reason or '').strip()
    n = (note or '').strip()
    if r and n:
        return f"[{r}] {n}"[:500]
    return (n or r or '')[:500]


def _sync_linked_entry(adj, mat, *, actor='System'):
    entry_type = 'OUT' if adj.direction == 'LOSS' else 'IN'
    tx_label = 'Stock Loss' if adj.direction == 'LOSS' else 'Stock Surplus'
    entry = db.session.get(Entry, adj.entry_id) if adj.entry_id else None
    if entry is None:
        entry = Entry(
            date=adj.adjustment_date,
            time=adj.adjustment_time,
            type=entry_type,
            material=mat.name,
            client='',
            client_code=None,
            client_category='Stock Adjustment',
            qty=float(adj.qty),
            bill_no=adj.reference,
            auto_bill_no=None,
            nimbus_no=tx_label,
            created_by=actor,
            transaction_category='Stock Adjustment',
            transaction_type=tx_label,
            note=_entry_note_text(adj.reason, adj.note),
            source_module='inventory',
            source_table='stock_adjustment',
            source_id=adj.id,
            source_bill_no=adj.reference,
            is_void=bool(adj.is_void),
        )
        db.session.add(entry)
        db.session.flush()
        adj.entry_id = entry.id
    else:
        entry.date = adj.adjustment_date
        entry.time = adj.adjustment_time
        entry.type = entry_type
        entry.material = mat.name
        entry.client = ''
        entry.client_code = None
        entry.client_category = 'Stock Adjustment'
        entry.qty = float(adj.qty)
        entry.bill_no = adj.reference
        entry.auto_bill_no = None
        entry.nimbus_no = tx_label
        entry.transaction_category = 'Stock Adjustment'
        entry.transaction_type = tx_label
        entry.note = _entry_note_text(adj.reason, adj.note)
        entry.source_module = 'inventory'
        entry.source_table = 'stock_adjustment'
        if adj.id:
            entry.source_id = adj.id
        entry.source_bill_no = adj.reference
        entry.is_void = bool(adj.is_void)
    return entry


def create_stock_adjustment(
    *,
    material_name,
    adjustment_date=None,
    adjustment_time=None,
    direction=None,
    qty=None,
    physical_qty=None,
    reason='',
    note='',
    unit_cost=None,
    source='lost_ledger',
    actor='System',
    actor_user=None,
    projection=None,
    conversion_reference=None,
):
    """Create a StockAdjustment + linked Entry + rebuild Material.total.

    Either pass ``direction`` ('LOSS' / 'GAIN') + positive ``qty``, OR pass
    ``physical_qty`` (>= 0) to compute the difference from system stock on
    ``adjustment_date``. Returns ``None`` when ``physical_qty`` matches system
    stock within ``EPS``.
    """
    mat = _resolve_material(material_name)
    date_str = _parse_date_str(adjustment_date, default_today=True)
    time_str = _parse_time_str(adjustment_time)
    sys_qty = system_stock_as_of(mat.name, date_str)

    if physical_qty is not None and str(physical_qty).strip() != '':
        try:
            phys_f = round(float(physical_qty), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError('Physical quantity must be a valid number.') from exc
        if phys_f < 0:
            raise ValueError('Physical stock count cannot be negative.')
        diff = round(phys_f - sys_qty, 6)
        if abs(diff) <= EPS:
            return None
        resolved_dir = 'LOSS' if diff < 0 else 'GAIN'
        resolved_qty = round(abs(diff), 6)
    else:
        resolved_dir = (direction or 'LOSS').strip().upper()
        if resolved_dir not in ('LOSS', 'GAIN'):
            raise ValueError('Adjustment direction must be LOSS or GAIN.')
        try:
            resolved_qty = round(float(qty or 0.0), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError('Quantity must be a valid positive number.') from exc
        if resolved_qty <= EPS:
            raise ValueError('Adjustment quantity must be greater than zero.')
        diff = -resolved_qty if resolved_dir == 'LOSS' else resolved_qty
        phys_f = round(sys_qty + diff, 6)

    # Open Cement stock exists only because a conversion put it there, and its
    # cost is inherited from the original GRN.  Losing more than is on hand
    # would create negative stock and a bogus loss cost, so it is refused here
    # (server-side, not just in the form).
    if resolved_dir == 'LOSS' and _is_open_cement(mat) and resolved_qty > sys_qty + EPS:
        raise ValueError(
            f'Loss quantity {resolved_qty:g} exceeds available Open Cement stock '
            f'{sys_qty:g} {mat.unit or "KG"} for {mat.name}.'
        )

    clean_reason = (reason or '').strip()
    if not clean_reason:
        clean_reason = 'Damage / Broken' if resolved_dir == 'LOSS' else 'Found Extra / Surplus'

    manual_rate = None
    if unit_cost is not None and str(unit_cost).strip() != '':
        try:
            manual_rate = float(unit_cost)
        except (TypeError, ValueError) as exc:
            raise ValueError('Unit cost must be a valid number.') from exc
        if manual_rate < 0:
            raise ValueError('Unit cost cannot be negative.')

    if manual_rate is not None and manual_rate > 0:
        est_rate = round(manual_rate, 4)
        total_val = round(resolved_qty * est_rate, 2)
        cost_src = 'Manual unit cost override'
    else:
        est_rate, total_val, cost_src = estimate_adjustment_cost(
            mat.name, date_str, resolved_qty, resolved_dir, projection=projection
        )

    # Open Cement: record which conversion the loss cost was inherited from.
    # An explicit caller-supplied reference wins (the form preview supplies it);
    # otherwise it is derived from the same FIFO lot the cost came from.
    resolved_conversion_reference = (conversion_reference or '').strip()[:50] or None
    if (
        resolved_conversion_reference is None
        and resolved_dir == 'LOSS'
        and manual_rate is None
        and _is_open_cement(mat)
    ):
        try:
            resolved_conversion_reference = estimate_adjustment_conversion_reference(
                mat.name, date_str, resolved_qty, resolved_dir, projection=projection
            )
        except Exception:
            resolved_conversion_reference = None

    ns = C.AUTO_BILL_NAMESPACES['STOCK_ADJUSTMENT']
    reference = get_next_bill_no(ns)

    adj = StockAdjustment(
        reference=reference,
        adjustment_date=date_str,
        adjustment_time=time_str,
        date_posted=_compose_datetime(date_str, time_str),
        material=mat.name,
        direction=resolved_dir,
        qty=resolved_qty,
        system_qty=sys_qty,
        physical_qty=phys_f,
        diff_qty=diff,
        reason=clean_reason[:80],
        note=(note or '').strip()[:500],
        unit_cost=est_rate,
        total_value=total_val,
        cost_source=cost_src[:200],
        source=(source or 'lost_ledger')[:40],
        conversion_reference=resolved_conversion_reference,
        created_by=actor,
        updated_by=actor,
        is_void=False,
    )
    db.session.add(adj)
    db.session.flush()

    entry = _sync_linked_entry(adj, mat, actor=actor)
    adj.entry_id = entry.id
    entry.source_id = adj.id

    _rebuild_material_totals()
    db.session.flush()

    db.session.add(AuditLog(
        user_id=getattr(actor_user, 'id', None),
        action='stock_adjustment.create',
        details=json.dumps({
            'id': adj.id,
            'reference': adj.reference,
            'material': adj.material,
            'direction': adj.direction,
            'qty': adj.qty,
            'system_qty': adj.system_qty,
            'physical_qty': adj.physical_qty,
            'reason': adj.reason,
            'unit_cost': adj.unit_cost,
            'total_value': adj.total_value,
            'source': adj.source,
            'conversion_reference': adj.conversion_reference,
            'actor': actor,
        }, default=str)[:1000],
    ))
    return adj


def update_stock_adjustment(
    adj,
    *,
    material_name,
    adjustment_date,
    direction=None,
    qty=None,
    physical_qty=None,
    reason='',
    note='',
    unit_cost=None,
    actor='System',
    actor_user=None,
):
    """Edit an existing StockAdjustment and resync its Entry & Material.total."""
    if not adj:
        raise ValueError('Adjustment record not found.')

    mat = _resolve_material(material_name)
    date_str = _parse_date_str(adjustment_date, default_today=True)
    time_str = adj.adjustment_time or pk_now().strftime('%H:%M:%S')
    sys_qty = system_stock_as_of(mat.name, date_str, exclude_entry_id=adj.entry_id)

    if physical_qty is not None and str(physical_qty).strip() != '':
        try:
            phys_f = round(float(physical_qty), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError('Physical quantity must be a valid number.') from exc
        if phys_f < 0:
            raise ValueError('Physical stock count cannot be negative.')
        diff = round(phys_f - sys_qty, 6)
        if abs(diff) <= EPS:
            raise ValueError('Physical quantity matches system stock (0 difference). Void/delete the adjustment instead.')
        resolved_dir = 'LOSS' if diff < 0 else 'GAIN'
        resolved_qty = round(abs(diff), 6)
    else:
        resolved_dir = (direction or adj.direction or 'LOSS').strip().upper()
        if resolved_dir not in ('LOSS', 'GAIN'):
            raise ValueError('Adjustment direction must be LOSS or GAIN.')
        try:
            resolved_qty = round(float(qty or 0.0), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError('Quantity must be a valid positive number.') from exc
        if resolved_qty <= EPS:
            raise ValueError('Adjustment quantity must be greater than zero.')
        diff = -resolved_qty if resolved_dir == 'LOSS' else resolved_qty
        phys_f = round(sys_qty + diff, 6)

    clean_reason = (reason or '').strip() or adj.reason or ('Damage / Broken' if resolved_dir == 'LOSS' else 'Found Extra / Surplus')

    manual_rate = None
    if unit_cost is not None and str(unit_cost).strip() != '':
        try:
            manual_rate = float(unit_cost)
        except (TypeError, ValueError) as exc:
            raise ValueError('Unit cost must be a valid number.') from exc
        if manual_rate < 0:
            raise ValueError('Unit cost cannot be negative.')

    if manual_rate is not None and manual_rate > 0:
        est_rate = round(manual_rate, 4)
        total_val = round(resolved_qty * est_rate, 2)
        cost_src = 'Manual unit cost override'
    else:
        est_rate, total_val, cost_src = estimate_adjustment_cost(
            mat.name, date_str, resolved_qty, resolved_dir
        )

    before_snap = {
        'material': adj.material,
        'date': adj.adjustment_date,
        'direction': adj.direction,
        'qty': adj.qty,
        'reason': adj.reason,
        'unit_cost': adj.unit_cost,
        'total_value': adj.total_value,
    }

    adj.adjustment_date = date_str
    adj.adjustment_time = time_str
    adj.date_posted = _compose_datetime(date_str, time_str)
    adj.material = mat.name
    adj.direction = resolved_dir
    adj.qty = resolved_qty
    adj.system_qty = sys_qty
    adj.physical_qty = phys_f
    adj.diff_qty = diff
    adj.reason = clean_reason[:80]
    adj.note = (note or '').strip()[:500]
    adj.unit_cost = est_rate
    adj.total_value = total_val
    adj.cost_source = cost_src[:200]
    adj.updated_by = actor

    entry = _sync_linked_entry(adj, mat, actor=actor)
    adj.entry_id = entry.id
    entry.source_id = adj.id

    _rebuild_material_totals()
    db.session.flush()

    db.session.add(AuditLog(
        user_id=getattr(actor_user, 'id', None),
        action='stock_adjustment.edit',
        details=json.dumps({
            'id': adj.id,
            'reference': adj.reference,
            'before': before_snap,
            'after': {
                'material': adj.material,
                'date': adj.adjustment_date,
                'direction': adj.direction,
                'qty': adj.qty,
                'reason': adj.reason,
                'unit_cost': adj.unit_cost,
                'total_value': adj.total_value,
            },
            'actor': actor,
        }, default=str)[:1000],
    ))
    return adj


def hard_delete_stock_adjustment(adj, *, actor='System', actor_user=None):
    """Permanently delete a StockAdjustment and its linked Entry."""
    if not adj:
        return False
    snap = {
        'id': adj.id,
        'reference': adj.reference,
        'material': adj.material,
        'direction': adj.direction,
        'qty': adj.qty,
        'total_value': adj.total_value,
        'actor': actor,
    }
    entry_id = adj.entry_id
    adj.entry_id = None
    from app.services.health import note_db_shrink
    note_db_shrink(1)
    db.session.flush()
    db.session.delete(adj)
    if entry_id:
        entry = db.session.get(Entry, entry_id)
        if entry:
            if not entry.is_void:
                _set_entry_void_state(entry, True)
            db.session.delete(entry)
    _rebuild_material_totals()
    db.session.flush()

    db.session.add(AuditLog(
        user_id=getattr(actor_user, 'id', None),
        action='stock_adjustment.hard_delete',
        details=json.dumps(snap, default=str)[:1000],
    ))
    return True


def save_physical_counts_batch(
    counts,
    *,
    adjustment_date=None,
    reason='',
    note='',
    actor='System',
    actor_user=None,
):
    """Save multiple physical counts from Stock Summary in one transaction.

    ``counts`` is an iterable of ``(material_name, physical_qty_raw)`` pairs.
    Blank values are skipped; matching counts (diff == 0) are counted in
    ``matched`` without creating redundant zero-difference entries.
    """
    date_str = _parse_date_str(adjustment_date, default_today=True)
    projection = _get_projection()

    # Deduplicate by resolved material name (preserving order, last value wins)
    deduped = {}
    for material_name, phys_raw in counts:
        if phys_raw is None or str(phys_raw).strip() == '':
            continue
        mat = _resolve_material(material_name)
        deduped[mat.name] = (mat, phys_raw)

    created = []
    matched = 0
    for mat_name, (mat, phys_raw) in deduped.items():
        sys_qty = system_stock_as_of(mat.name, date_str)
        try:
            phys_f = round(float(phys_raw), 6)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Invalid physical count for {mat.name}: "{phys_raw}".') from exc
        if phys_f < 0:
            raise ValueError(f'Physical count for {mat.name} cannot be negative.')
        diff = round(phys_f - sys_qty, 6)
        if abs(diff) <= EPS:
            matched += 1
            continue
        eff_reason = (reason or '').strip()
        if not eff_reason:
            eff_reason = 'Physical Count Shortage' if diff < 0 else 'Physical Count Surplus'
        adj = create_stock_adjustment(
            material_name=mat.name,
            adjustment_date=date_str,
            physical_qty=phys_f,
            reason=eff_reason,
            note=note,
            source='stock_summary',
            actor=actor,
            actor_user=actor_user,
            projection=projection,
        )
        if adj:
            created.append(adj)
    return {'created': created, 'matched': matched}
