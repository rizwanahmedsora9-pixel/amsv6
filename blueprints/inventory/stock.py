"""stock — split from inventory.py."""
from ._common import *  # noqa
from app.services.edit_policy import (
    EditBlocked, authorise_edit, audit_override, is_backdate_restricted,
)
from app.services.stock_adjustments import (
    ALL_REASONS, build_material_cost_map, save_physical_counts_batch,
)

@inventory_bp.route('/stock_summary')
@login_required
def stock_summary():
    date_from = request.args.get('date_from', date.today().strftime('%Y-%m-%d')).strip()
    date_to = request.args.get('date_to', date_from).strip()
    # Backward compatibility for old single-date link/query.
    single_date = request.args.get('date', '').strip()
    if single_date:
        date_from = single_date
        date_to = single_date
    category_id = request.args.get('material_category', '').strip()
    material_filter = request.args.get('material', '').strip()

    # Normalize invalid range silently to a safe value.
    if date_to < date_from:
        date_to = date_from

    sel_date = date_to

    mat_norm = material_filter.lower() if material_filter else ''
    exact_mat = None
    if material_filter:
        exact_mat = Material.query.filter(func.lower(Material.name) == mat_norm).first()

    selected_mcat = None
    category_name = ''
    allowed_cat_materials = None
    if category_id:
        try:
            cat_id_int = int(category_id)
            selected_mcat = db.session.get(MaterialCategory, cat_id_int)
            allowed_cat_materials = [m.name for m in Material.query.filter(Material.category_id == cat_id_int).all()]
            if selected_mcat:
                category_name = selected_mcat.name
        except ValueError:
            selected_mcat = MaterialCategory.query.filter(
                func.lower(MaterialCategory.name) == category_id.lower()
            ).first()
            if selected_mcat:
                category_id = str(selected_mcat.id)
                category_name = selected_mcat.name
                allowed_cat_materials = [m.name for m in Material.query.filter(Material.category_id == selected_mcat.id).all()]
            else:
                category_name = category_id
                matching_cats = [
                    c.id for c in MaterialCategory.query.filter(
                        MaterialCategory.name.ilike(f'%{category_id}%')
                    ).all()
                ]
                allowed_cat_materials = (
                    [m.name for m in Material.query.filter(Material.category_id.in_(matching_cats)).all()]
                    if matching_cats else []
                )

    prev_stats = db.session.query(
        Entry.material,
        func.sum(case(
            (Entry.type == 'IN', Entry.qty),
            (Entry.type == 'OUT', -Entry.qty),
            else_=0
        )).label('prev_net')
    ).filter(Entry.date < sel_date, Entry.is_void == False)

    if material_filter:
        if exact_mat:
            prev_stats = prev_stats.filter(func.lower(func.coalesce(Entry.material, '')) == mat_norm)
        else:
            prev_stats = prev_stats.filter(Entry.material.ilike(f'%{material_filter}%'))
    prev_stats = prev_stats.group_by(Entry.material).all()
    prev_map = {row.material: float(row.prev_net or 0) for row in prev_stats}
    
    day_stats = db.session.query(
        Entry.material,
        func.sum(case((Entry.type == 'IN', Entry.qty), else_=0)).label('day_in'),
        func.sum(case((Entry.type == 'OUT', Entry.qty), else_=0)).label('day_out')
    ).filter(Entry.date == sel_date, Entry.is_void == False)
    if material_filter:
        if exact_mat:
            day_stats = day_stats.filter(func.lower(func.coalesce(Entry.material, '')) == mat_norm)
        else:
            day_stats = day_stats.filter(Entry.material.ilike(f'%{material_filter}%'))
    day_stats = day_stats.group_by(Entry.material).all()
    day_map = {row.material: {'in': float(row.day_in or 0), 'out': float(row.day_out or 0)} for row in day_stats}
    
    all_material_objs = Material.query.all()
    category_map = {m.name: m.category.name if m.category else '' for m in all_material_objs}
    unit_map = {m.name: (m.unit or 'Bags') for m in all_material_objs}
    all_materials = set(prev_map.keys()) | set(day_map.keys())
    for mat in Material.query.with_entities(Material.name).all():
        all_materials.add(mat.name)

    if allowed_cat_materials is not None:
        allowed = set(allowed_cat_materials)
        all_materials = {m for m in all_materials if m in allowed}
    if material_filter:
        if exact_mat:
            all_materials = {m for m in all_materials if m and m.lower() == mat_norm}
        else:
            all_materials = {m for m in all_materials if m and mat_norm in m.lower()}

    cost_map = build_material_cost_map(sel_date)
    adj_rows_today = StockAdjustment.query.filter(
        StockAdjustment.adjustment_date == sel_date,
        StockAdjustment.is_void == False,
    ).order_by(StockAdjustment.id.asc()).all()
    adj_by_material = {}
    for a in adj_rows_today:
        entry = adj_by_material.setdefault(a.material, {'net': 0.0, 'refs': []})
        entry['net'] += float(a.diff_qty or 0.0)
        if a.reference:
            entry['refs'].append(a.reference)
    
    stats = []
    # Services have Entry rows (the booking-pool ledger) but are never stock.
    for mat_name in sorted([m for m in all_materials if m is not None and not is_service_name(m)]):
        prev_net = float(prev_map.get(mat_name, 0) or 0)
        day_in = float(day_map.get(mat_name, {}).get('in', 0) or 0)
        day_out = float(day_map.get(mat_name, {}).get('out', 0) or 0)
        closing_val = round(prev_net + day_in - day_out, 6)
        cinfo = cost_map.get(mat_name, {})
        ainfo = adj_by_material.get(mat_name)
        
        stats.append({
            'name': mat_name,
            'category': category_map.get(mat_name, ''),
            'unit': unit_map.get(mat_name, 'Bags'),
            'opening': prev_net,
            'in': day_in,
            'out': day_out,
            'closing': closing_val,
            'unit_cost': float(cinfo.get('unit_cost') or 0.0),
            # Value of what is physically on hand at the same FIFO cost the
            # Profit & Loss uses.  Nothing on any screen used to state what the
            # stock was worth; the shop could not read its own inventory value.
            'stock_value': round(closing_val * float(cinfo.get('unit_cost') or 0.0), 2),
            'adj_net': float(ainfo['net']) if ainfo else 0.0,
            'adj_refs': ainfo['refs'] if ainfo else [],
        })

    range_query = Entry.query.filter(
        Entry.is_void == False,
        Entry.date >= date_from,
        Entry.date <= date_to
    )
    if allowed_cat_materials is not None:
        if allowed_cat_materials:
            range_query = range_query.filter(Entry.material.in_(allowed_cat_materials))
        else:
            range_query = range_query.filter(Entry.id == -1)
    if material_filter:
        if exact_mat:
            range_query = range_query.filter(func.lower(func.coalesce(Entry.material, '')) == mat_norm)
        else:
            range_query = range_query.filter(Entry.material.ilike(f'%{material_filter}%'))

    totals_rows = db.session.query(
        Entry.material,
        func.sum(case((Entry.type == 'IN', Entry.qty), else_=0)).label('total_in'),
        func.sum(case((Entry.type == 'OUT', Entry.qty), else_=0)).label('total_out')
    ).filter(
        Entry.id.in_(range_query.with_entities(Entry.id))
    ).group_by(Entry.material).order_by(Entry.material.asc()).all()

    totals_by_material = [{
        'material': row.material,
        'received': float(row.total_in or 0),
        'delivered': float(row.total_out or 0),
        'net': float((row.total_in or 0) - (row.total_out or 0))
    } for row in totals_rows if row.material and not is_service_name(row.material)]

    daily_rows = db.session.query(
        Entry.date,
        func.sum(case((Entry.type == 'IN', Entry.qty), else_=0)).label('day_in'),
        func.sum(case((Entry.type == 'OUT', Entry.qty), else_=0)).label('day_out')
    ).filter(
        Entry.id.in_(range_query.with_entities(Entry.id))
    ).group_by(Entry.date).order_by(Entry.date.desc()).all()

    day_wise = [{
        'date': row.date,
        'received': float(row.day_in or 0),
        'delivered': float(row.day_out or 0),
        'net': float((row.day_in or 0) - (row.day_out or 0))
    } for row in daily_rows if row.date]

    grand_received = float(sum(r['received'] for r in totals_by_material))
    grand_delivered = float(sum(r['delivered'] for r in totals_by_material))
    grand_net = grand_received - grand_delivered
    grand_stock_value = round(sum(row['stock_value'] for row in stats), 2)

    # GRN incoming-history: every stock-IN movement in the range that traces
    # back to an active GRN (authoritative ledger — entry rows are created by
    # the GRN save; GRN is the source document).  Customer returns and manual
    # IN entries are separate and stay visible in the Received totals above.
    grn_receipts = _grn_receipt_history(
        date_from,
        date_to,
        material_filter,
        allowed_cat_materials=allowed_cat_materials,
        exact_mat=bool(exact_mat),
    )

    categories = MaterialCategory.query.order_by(MaterialCategory.name.asc()).all()
    materials = [m for m in Material.query.order_by(Material.name.asc()).all() if not is_service_name(m.name)]
    return render_template(
        'stock_summary.html',
        stats=stats,
        sel_date=sel_date,
        categories=categories,
        materials=materials,
        category_filter=category_id,
        category_name=category_name,
        date_from=date_from,
        date_to=date_to,
        material_filter=material_filter,
        totals_by_material=totals_by_material,
        day_wise=day_wise,
        grn_receipts=grn_receipts,
        grand_received=grand_received,
        grand_delivered=grand_delivered,
        grand_net=grand_net,
        grand_stock_value=grand_stock_value,
        all_reasons=ALL_REASONS,
        adjustments_today=adj_rows_today,
    )


@inventory_bp.route('/stock_summary/save_physical', methods=['POST'])
@login_required
def stock_summary_save_physical():
    date_from = (request.form.get('date_from') or date.today().strftime('%Y-%m-%d')).strip()
    date_to = (request.form.get('date_to') or date_from).strip()
    sel_date = (request.form.get('adjustment_date') or date_to or date.today().strftime('%Y-%m-%d')).strip()
    material_filter = (request.form.get('material') or '').strip()
    category_id = (request.form.get('material_category') or '').strip()
    reason = (request.form.get('reason') or '').strip()
    note = (request.form.get('note') or '').strip()

    redirect_url = url_for(
        'inventory.stock_summary',
        date_from=date_from,
        date_to=date_to,
        material=material_filter,
        material_category=category_id,
    )

    try:
        authorise_edit(
            current_user,
            sel_date,
            action_label='Save physical stock count',
            reason=note or reason or 'Physical stock count',
        )
    except EditBlocked as blocked:
        flash(blocked.message, 'danger')
        return redirect(redirect_url)

    mat_names = request.form.getlist('material_name')
    phys_vals = request.form.getlist('physical_qty')
    if not mat_names or len(mat_names) != len(phys_vals):
        flash('Invalid physical count submission.', 'danger')
        return redirect(redirect_url)

    actor = getattr(current_user, 'username', None) or 'System'
    try:
        result = save_physical_counts_batch(
            list(zip(mat_names, phys_vals)),
            adjustment_date=sel_date,
            reason=reason,
            note=note,
            actor=actor,
            actor_user=current_user,
        )
        created = result['created']
        matched = result['matched']
        if created:
            db.session.commit()
            if is_backdate_restricted(current_user):
                for adj in created:
                    audit_override(
                        current_user,
                        action='create',
                        entity='StockAdjustment',
                        entity_id=adj.id,
                        reason=adj.note or adj.reason,
                    )
                db.session.commit()
            refs = ', '.join(adj.reference for adj in created[:5])
            if len(created) > 5:
                refs += f' (+{len(created) - 5} more)'
            msg = f'Saved {len(created)} stock adjustment(s) ({refs}). Stock & Profit/Loss updated.'
            if matched:
                msg += f' ({matched} matched system stock.)'
            flash(msg, 'success')
        elif matched > 0:
            flash(f'All {matched} entered physical count(s) match system closing stock (0 difference). No adjustment needed.', 'info')
        else:
            flash('Enter at least one physical stock count before saving.', 'warning')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception:
        db.session.rollback()
        flash('Could not save physical stock counts. Please verify your entries and try again.', 'danger')

    return redirect(redirect_url)


def _grn_receipt_history(date_from, date_to, material_filter='', allowed_cat_materials=None, exact_mat=True):
    """GRN stock-in movements for the range, with the source GRN document.

    One authoritative pass over the entry ledger (IN, non-void) joined to the
    GRN master by auto bill number, so every row is traceable to a GRN
    (bill no + supplier) rather than appearing as an anonymous "added qty".
    """
    in_rows = Entry.query.filter(
        Entry.is_void == False,
        Entry.type == 'IN',
        Entry.date >= date_from,
        Entry.date <= date_to,
        Entry.auto_bill_no.isnot(None),
    )
    if allowed_cat_materials is not None:
        if allowed_cat_materials:
            in_rows = in_rows.filter(Entry.material.in_(allowed_cat_materials))
        else:
            in_rows = in_rows.filter(Entry.id == -1)
    if material_filter:
        if exact_mat:
            in_rows = in_rows.filter(func.lower(func.coalesce(Entry.material, '')) == material_filter.strip().lower())
        else:
            in_rows = in_rows.filter(Entry.material.ilike(f'%{material_filter}%'))
    in_rows = in_rows.with_entities(
        Entry.date, Entry.material, Entry.qty, Entry.auto_bill_no, Entry.bill_no
    ).all()

    grn_map = {}
    for g in GRN.query.filter(GRN.is_void == False).with_entities(
            GRN.auto_bill_no, GRN.manual_bill_no, GRN.supplier).all():
        ab = (g.auto_bill_no or '').strip()
        if ab:
            grn_map[ab] = {'bill': g.manual_bill_no or ab, 'supplier': g.supplier or ''}

    receipts = []
    for row in in_rows:
        info = grn_map.get((row.auto_bill_no or '').strip())
        if not info:
            continue
        receipts.append({
            'date': row.date,
            'bill_no': info['bill'],
            'supplier': info['supplier'],
            'material': row.material,
            'qty': float(row.qty or 0),
        })
    return receipts


