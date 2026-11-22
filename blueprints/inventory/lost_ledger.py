"""Lost Ledger (Stock Adjustments: Losses, Damage, Shrinkage & Physical Counts)."""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ._common import *  # noqa
from app.services.edit_policy import (
    EditBlocked, authorise_edit, audit_override, form_reason,
    is_backdate_restricted, record_date_of,
)
from app.services.lookups import get_material_by_input
from app.services.stock_adjustments import (
    ALL_REASONS,
    GAIN_REASONS,
    LOSS_REASONS,
    build_material_cost_map,
    create_stock_adjustment,
    estimate_adjustment_cost,
    estimate_adjustment_conversion_reference,
    hard_delete_stock_adjustment,
    system_stock_as_of,
    update_stock_adjustment,
)
from app.services.time_money import pk_now


def _lost_ledger_filters():
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    if date_from and date_to and date_to < date_from:
        date_to = date_from
    material = (request.args.get('material') or '').strip()
    material_category = (request.args.get('material_category') or '').strip()
    direction = (request.args.get('direction') or '').strip().upper()
    if direction not in ('LOSS', 'GAIN'):
        direction = ''
    reason = (request.args.get('reason') or '').strip()
    q = (request.args.get('q') or '').strip()
    show = (request.args.get('show') or 'active').strip().lower()
    if show not in ('active', 'voided', 'all'):
        show = 'active'
    per_page = request.args.get('per_page', 25, type=int) or 25
    if per_page not in (15, 25, 50, 100):
        per_page = 25
    page = max(1, request.args.get('page', 1, type=int) or 1)
    return {
        'date_from': date_from,
        'date_to': date_to,
        'material': material,
        'material_category': material_category,
        'direction': direction,
        'reason': reason,
        'q': q,
        'show': show,
        'per_page': per_page,
        'page': page,
    }


def _apply_lost_ledger_filters(query, f):
    if f['show'] == 'active':
        query = query.filter(StockAdjustment.is_void == False)
    elif f['show'] == 'voided':
        query = query.filter(StockAdjustment.is_void == True)

    if f['date_from']:
        query = query.filter(StockAdjustment.adjustment_date >= f['date_from'])
    if f['date_to']:
        query = query.filter(StockAdjustment.adjustment_date <= f['date_to'])
    if f['material']:
        query = query.filter(StockAdjustment.material == f['material'])
    if f['material_category']:
        try:
            cat_id = int(f['material_category'])
            cat_mats = [m.name for m in Material.query.filter(Material.category_id == cat_id).all()]
            if cat_mats:
                query = query.filter(StockAdjustment.material.in_(cat_mats))
            else:
                query = query.filter(StockAdjustment.id == -1)
        except ValueError:
            pass
    if f['direction']:
        query = query.filter(StockAdjustment.direction == f['direction'])
    if f['reason']:
        query = query.filter(StockAdjustment.reason == f['reason'])
    if f['q']:
        term = f"%{f['q']}%"
        query = query.filter(
            or_(
                StockAdjustment.reference.ilike(term),
                StockAdjustment.material.ilike(term),
                StockAdjustment.reason.ilike(term),
                StockAdjustment.note.ilike(term),
                StockAdjustment.created_by.ilike(term),
            )
        )
    return query


def _page_window(current_page, total_pages):
    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    pages = [1]
    left = max(2, current_page - 1)
    right = min(total_pages - 1, current_page + 1)
    if left > 2:
        pages.append(None)
    pages.extend(range(left, right + 1))
    if right < total_pages - 1:
        pages.append(None)
    pages.append(total_pages)
    return pages


def _filter_redirect_args(source):
    return {
        k: (source.get(k) or '').strip()
        for k in ('date_from', 'date_to', 'material', 'material_category', 'direction', 'reason', 'q', 'show', 'per_page', 'page')
        if (source.get(k) or '').strip()
    }


def _lost_ledger_redirect_url():
    next_url = (request.form.get('next') or '').strip()
    if next_url.startswith('/inventory/lost_ledger') or next_url.startswith('/lost_ledger'):
        return next_url
    return url_for('inventory.lost_ledger', **_filter_redirect_args(request.form))


@inventory_bp.route('/lost_ledger')
@login_required
def lost_ledger():
    f = _lost_ledger_filters()
    base_query = _apply_lost_ledger_filters(StockAdjustment.query, f)

    # Full-filtered rows for KPIs & material/reason breakdowns (active rows only
    # contribute to live financial/stock totals, matching Profit & Loss).
    filtered_rows = base_query.order_by(
        StockAdjustment.adjustment_date.desc(),
        StockAdjustment.id.desc(),
    ).all()

    total_rows = len(filtered_rows)
    per_page = f['per_page']
    total_pages = max(1, (total_rows + per_page - 1) // per_page)
    page = min(f['page'], total_pages)
    f['page'] = page
    start_idx = (page - 1) * per_page
    end_idx = min(total_rows, start_idx + per_page)
    page_rows = filtered_rows[start_idx:end_idx]

    # Single authoritative P&L projection pass for FIFO cost map + live P&L row values
    from app.services.profit_loss import _Projection
    projection = _Projection().build()
    pl_by_adj_id = {}
    for prow in projection.rows:
        if prow.get('document_type') == 'StockAdjustment' and prow.get('document_id'):
            pl_by_adj_id[int(prow['document_id'])] = prow

    all_materials = [m for m in Material.query.order_by(Material.name.asc()).all() if not is_service_name(m.name)]
    mat_unit_map = {m.name: (m.unit or 'Bags') for m in all_materials}
    mat_cat_map = {m.name: (m.category.name if m.category else 'General') for m in all_materials}
    mat_stock_map = {m.name: round(float(m.total or 0.0), 4) for m in all_materials}
    today_str = pk_now().strftime('%Y-%m-%d')
    cost_map = build_material_cost_map(today_str, projection=projection)

    loss_qty = 0.0
    loss_value = 0.0
    loss_count = 0
    gain_qty = 0.0
    gain_value = 0.0
    gain_count = 0
    by_material_map = {}
    by_reason_map = {}

    def _row_pl_value(adj_obj):
        prow = pl_by_adj_id.get(adj_obj.id)
        if prow:
            if prow.get('include_in_net'):
                amt = float(prow.get('other_loss') or prow.get('other_gain') or 0.0)
                return amt, prow.get('cost_basis') or adj_obj.cost_source or '', True
            return float(adj_obj.total_value or 0.0), prow.get('cost_basis') or adj_obj.cost_source or '', False
        return float(adj_obj.total_value or 0.0), adj_obj.cost_source or '', True

    for adj in filtered_rows:
        if adj.is_void:
            continue
        qty_f = float(adj.qty or 0.0)
        val_f, _basis, _included = _row_pl_value(adj)
        is_loss = (adj.direction or 'LOSS').upper() != 'GAIN'
        if is_loss:
            loss_qty += qty_f
            loss_value += val_f
            loss_count += 1
        else:
            gain_qty += qty_f
            gain_value += val_f
            gain_count += 1

        mb = by_material_map.setdefault(adj.material, {
            'material': adj.material,
            'unit': mat_unit_map.get(adj.material, 'Bags'),
            'loss_qty': 0.0,
            'gain_qty': 0.0,
            'loss_value': 0.0,
            'gain_value': 0.0,
            'count': 0,
        })
        mb['count'] += 1
        if is_loss:
            mb['loss_qty'] += qty_f
            mb['loss_value'] += val_f
        else:
            mb['gain_qty'] += qty_f
            mb['gain_value'] += val_f

        r_key = adj.reason or 'Other'
        rb = by_reason_map.setdefault(r_key, {
            'reason': r_key,
            'loss_qty': 0.0,
            'gain_qty': 0.0,
            'net_value': 0.0,
            'count': 0,
        })
        rb['count'] += 1
        if is_loss:
            rb['loss_qty'] += qty_f
            rb['net_value'] -= val_f
        else:
            rb['gain_qty'] += qty_f
            rb['net_value'] += val_f

    by_material = sorted(
        by_material_map.values(),
        key=lambda r: (r['loss_value'] + r['gain_value'], r['loss_qty'] + r['gain_qty']),
        reverse=True,
    )
    by_reason = sorted(
        by_reason_map.values(),
        key=lambda r: (abs(r['net_value']), r['count']),
        reverse=True,
    )

    display_rows = []
    for adj in page_rows:
        val_f, basis, included = _row_pl_value(adj)
        qty_f = float(adj.qty or 0.0)
        unit_cost_eff = (val_f / qty_f) if (qty_f > 1e-6 and val_f > 0) else float(adj.unit_cost or 0.0)
        display_rows.append({
            'obj': adj,
            'unit': mat_unit_map.get(adj.material, 'Bags'),
            'category': mat_cat_map.get(adj.material, 'General'),
            'effective_value': round(val_f, 2),
            'effective_unit_cost': round(unit_cost_eff, 2),
            'effective_basis': basis,
            'pl_included': included,
        })

    categories = MaterialCategory.query.order_by(MaterialCategory.name.asc()).all()
    existing_reasons = [
        r[0] for r in db.session.query(StockAdjustment.reason).distinct().order_by(StockAdjustment.reason.asc()).all()
        if r[0]
    ]
    reason_choices = list(dict.fromkeys(list(ALL_REASONS) + existing_reasons))

    now_pk = pk_now()
    month_start_str = now_pk.replace(day=1).strftime('%Y-%m-%d')
    last30_str = (now_pk - timedelta(days=29)).strftime('%Y-%m-%d')

    return render_template(
        'lost_ledger.html',
        rows=display_rows,
        filters=f,
        total_rows=total_rows,
        total_pages=total_pages,
        page_window=_page_window(page, total_pages),
        start_idx=(start_idx + 1 if total_rows else 0),
        end_idx=end_idx,
        kpis={
            'loss_qty': round(loss_qty, 4),
            'loss_value': round(loss_value, 2),
            'loss_count': loss_count,
            'gain_qty': round(gain_qty, 4),
            'gain_value': round(gain_value, 2),
            'gain_count': gain_count,
            'net_value': round(gain_value - loss_value, 2),
            'active_count': loss_count + gain_count,
        },
        by_material=by_material,
        by_reason=by_reason,
        materials=all_materials,
        categories=categories,
        mat_unit_map=mat_unit_map,
        mat_stock_map=mat_stock_map,
        cost_map=cost_map,
        loss_reasons=LOSS_REASONS,
        gain_reasons=GAIN_REASONS,
        all_reasons=reason_choices,
        today_str=today_str,
        month_start_str=month_start_str,
        last30_str=last30_str,
    )


@inventory_bp.route('/lost_ledger/material_info')
@login_required
def lost_ledger_material_info():
    material_input = (request.args.get('material') or '').strip()
    as_of_date = (request.args.get('date') or '').strip() or pk_now().strftime('%Y-%m-%d')
    exclude_entry_id = request.args.get('exclude_entry_id', type=int)
    direction = (request.args.get('direction') or 'LOSS').strip().upper()
    qty = request.args.get('qty', 1.0, type=float) or 1.0

    mat = get_material_by_input(material_input)
    if not mat:
        return jsonify({'ok': False, 'error': 'Material not found'}), 404

    sys_qty = system_stock_as_of(mat.name, as_of_date, exclude_entry_id=exclude_entry_id)
    unit_cost, total_val, cost_src = estimate_adjustment_cost(
        mat.name, as_of_date, qty=max(qty, 1.0), direction=direction
    )
    is_open = False
    conv_ref = None
    try:
        from app.services.open_cement import is_open_cement_material
        is_open = is_open_cement_material(mat)
    except Exception:
        is_open = False
    if is_open and direction == 'LOSS':
        try:
            conv_ref = estimate_adjustment_conversion_reference(
                mat.name, as_of_date, qty=max(qty, 1.0), direction=direction
            )
        except Exception:
            conv_ref = None
    return jsonify({
        'ok': True,
        'material': mat.name,
        'unit': mat.unit or 'Bags',
        'system_qty': sys_qty,
        'unit_cost': unit_cost,
        'total_value': total_val,
        'cost_source': cost_src,
        'is_open_cement': is_open,
        'conversion_reference': conv_ref,
    })


@inventory_bp.route('/lost_ledger/add', methods=['POST'])
@login_required
def lost_ledger_add():
    adj_date = (request.form.get('adjustment_date') or pk_now().strftime('%Y-%m-%d')).strip()
    material = (request.form.get('material') or '').strip()
    entry_mode = (request.form.get('entry_mode') or 'qty').strip().lower()
    direction = (request.form.get('direction') or 'LOSS').strip().upper()
    qty_raw = request.form.get('qty')
    phys_raw = request.form.get('physical_qty')
    reason = (request.form.get('reason') or '').strip()
    note = (request.form.get('note') or '').strip()
    unit_cost_raw = (request.form.get('unit_cost') or '').strip()
    conversion_reference = (request.form.get('conversion_reference') or '').strip()

    redirect_url = _lost_ledger_redirect_url()

    try:
        authorise_edit(
            current_user,
            adj_date,
            action_label='Add stock adjustment',
            reason=note or reason or 'Lost ledger entry',
        )
    except EditBlocked as blocked:
        flash(blocked.message, 'danger')
        return redirect(redirect_url)

    actor = getattr(current_user, 'username', None) or 'System'
    try:
        if entry_mode == 'physical':
            adj = create_stock_adjustment(
                material_name=material,
                adjustment_date=adj_date,
                physical_qty=phys_raw,
                reason=reason,
                note=note,
                unit_cost=(unit_cost_raw if unit_cost_raw else None),
                source='lost_ledger',
                actor=actor,
                actor_user=current_user,
                conversion_reference=conversion_reference,
            )
            if adj is None:
                flash('Entered physical count matches system stock (0 difference). No adjustment needed.', 'info')
                return redirect(redirect_url)
        else:
            adj = create_stock_adjustment(
                material_name=material,
                adjustment_date=adj_date,
                direction=direction,
                qty=qty_raw,
                reason=reason,
                note=note,
                unit_cost=(unit_cost_raw if unit_cost_raw else None),
                source='lost_ledger',
                actor=actor,
                actor_user=current_user,
                conversion_reference=conversion_reference,
            )
        db.session.commit()
        if is_backdate_restricted(current_user):
            audit_override(
                current_user,
                action='create',
                entity='StockAdjustment',
                entity_id=adj.id,
                reason=adj.note or adj.reason,
            )
            db.session.commit()
        sign = '-' if adj.direction == 'LOSS' else '+'
        flash(
            f'Saved {adj.reference}: {adj.material} ({sign}{float(adj.qty):g}) · '
            f'Est. P&L impact Rs. {float(adj.total_value or 0):,.2f}. Stock & Profit/Loss updated.',
            'success',
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception:
        db.session.rollback()
        flash('Could not save stock adjustment. Please check the values and try again.', 'danger')

    return redirect(redirect_url)


@inventory_bp.route('/lost_ledger/edit/<int:adj_id>', methods=['POST'])
@login_required
def lost_ledger_edit(adj_id):
    adj = db.session.get(StockAdjustment, adj_id)
    redirect_url = _lost_ledger_redirect_url()
    if not adj:
        flash('Stock adjustment record not found.', 'danger')
        return redirect(redirect_url)

    new_date = (request.form.get('adjustment_date') or adj.adjustment_date).strip()
    reason = (request.form.get('reason') or adj.reason or '').strip()
    note = (request.form.get('note') or '').strip()
    edit_reason = form_reason(request.form) or note or reason

    try:
        authorise_edit(
            current_user,
            record_date_of(adj, 'adjustment_date'),
            action_label='Edit stock adjustment',
            reason=edit_reason,
        )
        if new_date != adj.adjustment_date:
            authorise_edit(
                current_user,
                new_date,
                action_label='Move stock adjustment date',
                reason=edit_reason,
            )
    except EditBlocked as blocked:
        flash(blocked.message, 'danger')
        return redirect(redirect_url)

    entry_mode = (request.form.get('entry_mode') or 'qty').strip().lower()
    direction = (request.form.get('direction') or adj.direction).strip().upper()
    material = (request.form.get('material') or adj.material).strip()
    qty_raw = request.form.get('qty')
    phys_raw = request.form.get('physical_qty')
    unit_cost_raw = (request.form.get('unit_cost') or '').strip()

    actor = getattr(current_user, 'username', None) or 'System'
    try:
        update_stock_adjustment(
            adj,
            material_name=material,
            adjustment_date=new_date,
            direction=direction,
            qty=(None if entry_mode == 'physical' else qty_raw),
            physical_qty=(phys_raw if entry_mode == 'physical' else None),
            reason=reason,
            note=note,
            unit_cost=(unit_cost_raw if unit_cost_raw else None),
            actor=actor,
            actor_user=current_user,
        )
        db.session.commit()
        if is_backdate_restricted(current_user):
            audit_override(
                current_user,
                action='edit',
                entity='StockAdjustment',
                entity_id=adj.id,
                reason=edit_reason,
            )
            db.session.commit()
        flash(f'Updated {adj.reference}. Stock, FIFO lots & Profit/Loss recalculated.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception:
        db.session.rollback()
        flash('Could not update stock adjustment.', 'danger')

    return redirect(redirect_url)


@inventory_bp.route('/lost_ledger/delete/<int:adj_id>', methods=['POST'])
@login_required
def lost_ledger_delete(adj_id):
    adj = db.session.get(StockAdjustment, adj_id)
    redirect_url = _lost_ledger_redirect_url()
    if not adj:
        flash('Stock adjustment record not found.', 'danger')
        return redirect(redirect_url)

    void_reason = form_reason(request.form) or 'Deleted from Lost Ledger'
    try:
        authorise_edit(
            current_user,
            record_date_of(adj, 'adjustment_date'),
            action_label='Delete stock adjustment',
            reason=void_reason,
        )
    except EditBlocked as blocked:
        flash(blocked.message, 'danger')
        return redirect(redirect_url)

    actor = getattr(current_user, 'username', None) or 'System'
    # Immediate delete: reverse the stock effect, drop the adjustment and its
    # ledger entry for real — nothing lingers until a restart.
    hard_delete_stock_adjustment(adj, actor=actor, actor_user=current_user)
    db.session.commit()
    if is_backdate_restricted(current_user):
        audit_override(
            current_user,
            action='delete',
            entity='StockAdjustment',
            entity_id=adj.id,
            reason=void_reason,
        )
        db.session.commit()
    flash(f'Deleted {adj.reference}. Stock quantity and Profit & Loss impact reversed immediately.', 'success')
    return redirect(redirect_url)




@inventory_bp.route('/lost_ledger/export')
@login_required
def lost_ledger_export():
    f = _lost_ledger_filters()
    rows = _apply_lost_ledger_filters(StockAdjustment.query, f).order_by(
        StockAdjustment.adjustment_date.desc(),
        StockAdjustment.id.desc(),
    ).all()

    from app.services.profit_loss import _Projection
    projection = _Projection().build()
    pl_by_adj_id = {
        int(prow['document_id']): prow
        for prow in projection.rows
        if prow.get('document_type') == 'StockAdjustment' and prow.get('document_id')
    }

    wb = Workbook()
    ws = wb.active
    ws.title = 'Lost Ledger'
    headers = [
        'Date', 'Time', 'Reference', 'Type', 'Material', 'Qty',
        'System Before', 'Physical Count', 'Diff', 'Reason',
        'Unit Cost (Rs)', 'P&L Value Impact (Rs)', 'Status', 'Source', 'Note', 'By',
    ]
    ws.append(headers)
    header_fill = PatternFill(start_color='1E293B', end_color='1E293B', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF')
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font

    for adj in rows:
        prow = pl_by_adj_id.get(adj.id)
        if prow and prow.get('include_in_net'):
            pl_val = float(prow.get('other_loss') or prow.get('other_gain') or 0.0)
        else:
            pl_val = float(adj.total_value or 0.0)
        signed_pl = -pl_val if (adj.direction or 'LOSS').upper() == 'LOSS' else pl_val
        ws.append([
            adj.adjustment_date,
            adj.adjustment_time or '',
            adj.reference,
            'Stock Loss' if (adj.direction or 'LOSS').upper() == 'LOSS' else 'Stock Surplus',
            adj.material,
            float(adj.qty or 0.0),
            (float(adj.system_qty) if adj.system_qty is not None else ''),
            (float(adj.physical_qty) if adj.physical_qty is not None else ''),
            float(adj.diff_qty or 0.0),
            adj.reason or '',
            float(adj.unit_cost or 0.0),
            (0.0 if adj.is_void else signed_pl),
            ('Deleted/Void' if adj.is_void else 'Active'),
            ('Stock Summary' if adj.source == 'stock_summary' else 'Lost Ledger'),
            adj.note or '',
            adj.created_by or 'System',
        ])

    for col in ws.columns:
        max_len = max((len(str(c.value or '')) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 3, 12), 42)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = pk_now().strftime('%Y%m%d_%H%M%S')
    return send_file(
        buf,
        as_attachment=True,
        download_name=f'lost_ledger_{stamp}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
