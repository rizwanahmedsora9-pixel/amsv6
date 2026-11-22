"""Cement Bag -> Open Cement conversion screen and history."""
from __future__ import annotations

from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from ._common import *  # noqa
from app.services.cement_conversion import (
    conversion_detail,
    conversion_history,
    create_conversion,
    preview_conversion,
    reverse_conversion,
)
from app.services.edit_policy import (
    EditBlocked,
    authorise_edit,
    audit_override,
    form_reason,
    is_backdate_restricted,
    record_date_of,
)
from app.services.lookups import get_material_by_input
from app.services.open_cement import (
    DEFAULT_BAG_WEIGHT_KG,
    ensure_open_cement_category,
    is_open_cement_material,
    linked_open_materials,
    source_materials_for_conversion,
)
from app.services.time_money import pk_now


def _linked_open_payload(source_mat) -> list:
    """Serialise the Open Cement items linked to *source_mat*.

    Used by both the dedicated linked-items endpoint and the cost preview so
    the destination dropdown can never be blocked by an unrelated validation
    error (empty quantity, no stock, missing cost layer).
    """
    return [
        {
            'id': m.id,
            'name': m.name,
            'unit': m.unit or 'KG',
            'stock': round(float(m.total or 0.0), 4),
        }
        for m in linked_open_materials(source_mat)
    ]


def _resolve_material_input(value):
    """Resolve a material from a submitted id *or* name/code.

    The form posts the select's value (a material id); a manipulated request
    may post a name instead, so both are accepted and neither can create a row.
    """
    raw = (value or '').strip()
    if not raw:
        return None
    try:
        mat = db.session.get(Material, int(raw))
    except (TypeError, ValueError):
        mat = None
    if mat is not None:
        return mat
    return get_material_by_input(raw)


def _conversion_filters():
    return {
        'date_from': (request.args.get('date_from') or '').strip(),
        'date_to': (request.args.get('date_to') or '').strip(),
        'source_material': (request.args.get('source_material') or '').strip(),
        'dest_material': (request.args.get('dest_material') or '').strip(),
        'q': (request.args.get('q') or '').strip(),
        'page': request.args.get('page', 1, type=int) or 1,
        'per_page': request.args.get('per_page', 25, type=int) or 25,
    }


@inventory_bp.route('/cement_conversion')
@login_required
def cement_conversion():
    ensure_open_cement_category()
    f = _conversion_filters()
    history = conversion_history(
        date_from=f['date_from'] or None,
        date_to=f['date_to'] or None,
        source_material=f['source_material'],
        dest_material=f['dest_material'],
        q=f['q'],
        page=f['page'],
        per_page=f['per_page'],
    )
    source_materials = source_materials_for_conversion()
    # All open-cement items, each carrying its explicit source link.
    from app.services.open_cement import open_cement_materials
    open_items = open_cement_materials()
    return render_template(
        'cement_conversion.html',
        filters=f,
        source_materials=source_materials,
        open_items=open_items,
        history=history,
        default_bag_weight=DEFAULT_BAG_WEIGHT_KG,
        today_str=pk_now().strftime('%Y-%m-%d'),
        saved_reference=(request.args.get('saved') or '').strip(),
    )


@inventory_bp.route('/cement_conversion/export')
@login_required
def cement_conversion_export():
    f = _conversion_filters()
    history = conversion_history(
        date_from=f['date_from'] or None,
        date_to=f['date_to'] or None,
        source_material=f['source_material'],
        dest_material=f['dest_material'],
        q=f['q'],
        page=1,
        per_page=100,
    )
    import csv
    import io
    from flask import Response
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        'Conversion ID', 'Date', 'Source', 'Destination', 'Bags', 'Source Unit',
        'KG Produced', 'Dest Unit', 'Weight per Bag', 'Cost per Bag', 'Cost per KG',
        'Total Transferred Cost', 'Remaining Source Stock', 'User', 'Remarks', 'Status',
    ])
    for conv in history['rows']:
        writer.writerow([
            conv.reference, conv.conversion_date, conv.source_material, conv.dest_material,
            f'{float(conv.bags_converted or 0):.3f}', conv.source_unit,
            f'{float(conv.kg_produced or 0):.3f}', conv.dest_unit,
            f'{float(conv.weight_per_bag_kg or 0):.3f}',
            f'{float(conv.source_cost_per_bag or 0):.2f}',
            f'{float(conv.source_cost_per_kg or 0):.4f}',
            f'{float(conv.total_transferred_cost or 0):.2f}',
            f'{float(conv.source_stock_after or 0):.3f}',
            conv.created_by or '', conv.note or '', conv.status,
        ])
    buffer.seek(0)
    filename = f'cement_conversions_{f["date_from"] or "all"}_{f["date_to"] or "all"}.csv'.replace(':', '-')
    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


@inventory_bp.route('/cement_conversion/linked_open')
@login_required
def cement_conversion_linked_open():
    """List the Open Cement items linked to a source cement item.

    Deliberately separate from the cost preview: the destination dropdown must
    populate as soon as a source is picked, before the operator types a
    quantity, and it must keep working when the preview itself is invalid.
    """
    source = _resolve_material_input(request.args.get('source') or '')
    if source is None:
        return jsonify({'ok': False, 'error': 'Source material not found', 'linked_open': []}), 404
    if is_open_cement_material(source):
        return jsonify({'ok': False, 'error': 'Open Cement items cannot be a conversion source', 'linked_open': []}), 400
    return jsonify({'ok': True, 'linked_open': _linked_open_payload(source)})


@inventory_bp.route('/cement_conversion/preview')
@login_required
def cement_conversion_preview():
    source = _resolve_material_input(request.args.get('source') or '')
    if source is None:
        return jsonify({'ok': False, 'error': 'Source material not found'}), 404
    if is_open_cement_material(source):
        return jsonify({'ok': False, 'error': 'Open Cement items cannot be a conversion source'}), 400
    # The linked list is independent of the quantity/stock validation below,
    # so build it first and return it even when the preview is refused.
    linked = _linked_open_payload(source)
    try:
        preview = preview_conversion(
            source,
            request.args.get('date') or pk_now().strftime('%Y-%m-%d'),
            request.args.get('bags', 1.0, type=float) or 0.0,
            request.args.get('weight_per_bag', type=float),
        )
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc), 'linked_open': linked}), 400
    return jsonify({
        'ok': True,
        'source': source.name,
        'source_unit': source.unit or 'Bags',
        'date': preview['date'],
        'bags': preview['bags'],
        'weight_per_bag': preview['weight_per_bag'],
        'available_stock': preview['available_stock'],
        'kg_produced': preview['kg_produced'],
        'cost_per_bag': preview['cost_per_bag'],
        'cost_per_kg': preview['cost_per_kg'],
        'total_cost': preview['total_cost'],
        'basis': preview['basis'],
        'covered': preview['covered'],
        'sufficient_stock': preview['sufficient_stock'],
        'valid': preview['valid'],
        'linked_open': linked,
    })


@inventory_bp.route('/cement_conversion/save', methods=['POST'])
@login_required
def cement_conversion_save():
    source = _resolve_material_input(request.form.get('source_material') or '')
    dest = _resolve_material_input(request.form.get('dest_material') or '')
    conversion_date = (request.form.get('conversion_date') or pk_now().strftime('%Y-%m-%d')).strip()
    bags_raw = request.form.get('bags')
    weight_raw = (request.form.get('weight_per_bag') or '').strip()
    note = (request.form.get('note') or '').strip()
    idem_key = (request.form.get('idempotency_key') or '').strip()

    redirect_url = url_for('inventory.cement_conversion')

    if source is None:
        flash('Select a valid source cement item.', 'danger')
        return redirect(redirect_url)
    if dest is None:
        flash('Select a valid Open Cement destination item.', 'danger')
        return redirect(redirect_url)

    try:
        authorise_edit(
            current_user,
            conversion_date,
            action_label='Add cement conversion',
            reason=note or 'Cement bag to open cement conversion',
        )
    except EditBlocked as blocked:
        flash(blocked.message, 'danger')
        return redirect(redirect_url)

    # Serialize writers so a double-click or two cashiers cannot both consume
    # the same bags.  The whole preview/alloc/create path runs under this lock.
    if db.engine.dialect.name == 'sqlite':
        from app.services.errors import DatabaseContended, begin_write_transaction
        try:
            begin_write_transaction(db.session)
        except DatabaseContended as contended:
            flash(str(contended), 'danger')
            return redirect(redirect_url)

    actor = getattr(current_user, 'username', None) or 'System'
    try:
        conv = create_conversion(
            source_material=source,
            dest_material=dest,
            conversion_date=conversion_date,
            bags=bags_raw,
            weight_per_bag=(weight_raw if weight_raw else None),
            note=note,
            actor=actor,
            actor_user=current_user,
            idempotency_key=idem_key,
        )
        db.session.commit()
        if is_backdate_restricted(current_user):
            audit_override(
                current_user,
                action='create',
                entity='CementConversion',
                entity_id=conv.id,
                reason=note or 'Cement conversion',
            )
            db.session.commit()
        flash(
            f'Saved {conv.reference}: {conv.bags_converted:g} {conv.source_unit} '
            f'→ {conv.kg_produced:g} {conv.dest_unit} · cost Rs. '
            f'{conv.total_transferred_cost:,.2f}. Stock and FIFO cost updated.',
            'success',
        )
        return redirect(url_for('inventory.cement_conversion', saved=conv.reference))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception as exc:
        db.session.rollback()
        import logging
        logging.getLogger('inventory').exception('Cement conversion failed')
        flash(f'Could not save the conversion: {exc or "please check the values and try again."}', 'danger')
    return redirect(redirect_url)


@inventory_bp.route('/cement_conversion/<int:conversion_id>')
@login_required
def cement_conversion_detail(conversion_id):
    from models import CementConversion
    conv = db.session.get(CementConversion, conversion_id)
    if conv is None:
        flash('Conversion not found.', 'danger')
        return redirect(url_for('inventory.cement_conversion'))
    detail = conversion_detail(conv)
    return render_template('cement_conversion_detail.html', **detail)


@inventory_bp.route('/cement_conversion/<int:conversion_id>/reverse', methods=['POST'])
@login_required
def cement_conversion_reverse(conversion_id):
    from models import CementConversion
    conv = db.session.get(CementConversion, conversion_id)
    redirect_url = url_for('inventory.cement_conversion_detail', conversion_id=conversion_id)
    if conv is None:
        flash('Conversion not found.', 'danger')
        return redirect(url_for('inventory.cement_conversion'))

    reason = form_reason(request.form) or 'Reversed from conversion history'
    try:
        authorise_edit(
            current_user,
            record_date_of(conv, 'conversion_date') or pk_now().date(),
            action_label='Reverse cement conversion',
            reason=reason,
        )
    except EditBlocked as blocked:
        flash(blocked.message, 'danger')
        return redirect(redirect_url)

    if db.engine.dialect.name == 'sqlite':
        from app.services.errors import DatabaseContended, begin_write_transaction
        try:
            begin_write_transaction(db.session)
        except DatabaseContended as contended:
            flash(str(contended), 'danger')
            return redirect(redirect_url)

    actor = getattr(current_user, 'username', None) or 'System'
    try:
        reverse_conversion(conv, actor=actor, actor_user=current_user, reason=reason)
        db.session.commit()
        if is_backdate_restricted(current_user):
            audit_override(
                current_user,
                action='delete',
                entity='CementConversion',
                entity_id=conv.id,
                reason=reason,
            )
            db.session.commit()
        flash(
            f'{conv.reference} reversed. {conv.bags_converted:g} bag(s) restored to '
            f'{conv.source_material} and {conv.kg_produced:g} kg removed from '
            f'{conv.dest_material}.',
            'success',
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception as exc:
        db.session.rollback()
        import logging
        logging.getLogger('inventory').exception('Cement conversion reversal failed')
        flash(f'Could not reverse the conversion: {exc or "please try again."}', 'danger')
    return redirect(redirect_url)
