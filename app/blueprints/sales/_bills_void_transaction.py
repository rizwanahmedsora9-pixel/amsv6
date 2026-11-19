"""bills — split from sales.py."""
from ._common import *  # noqa
from app.services.void_rebuild import (
    hard_delete_transaction,
    _set_direct_sale_void_state,
    _set_booking_void_state,
    _set_payment_void_state,
    _set_material_return_void_state,
    _set_grn_void_state,
    _set_entry_void_state,
)

#: type name -> (model, soft-void setter)
_VOID_TARGETS = {
    'DirectSale': ('direct_sale', _set_direct_sale_void_state),
    'Sale': ('direct_sale', _set_direct_sale_void_state),
    'Booking': ('booking', _set_booking_void_state),
    'Payment': ('payment', _set_payment_void_state),
    'MaterialReturn': ('material_return', _set_material_return_void_state),
    'Return': ('material_return', _set_material_return_void_state),
    'GRN': ('grn', _set_grn_void_state),
    'Entry': ('entry', _set_entry_void_state),
}


@bp.route('/void_transaction/<string:type>/<int:id>', methods=['POST'])
@login_required
def void_transaction(type, id):
    """Void a posted document: keep the row, reverse its effects.

    A posted bill is evidence.  Voiding flags it (``is_void``), writes the
    reversal, restores stock and releases the receivable -- but the document,
    its bill number and its line detail stay in the database, and the reason
    is recorded.  For an outright removal use ``/delete_transaction`` with an
    explicit confirmation; that path is reserved for drafts and corrections.
    """
    if not _user_can('can_manage_sales') and getattr(current_user, 'role', '') != 'admin':
        flash('Permission denied', 'danger')
        return redirect(request.referrer or url_for('index'))

    entry = _VOID_TARGETS.get((type or '').strip())
    if entry is None:
        flash(f'"{type}" cannot be voided from this screen.', 'warning')
        return redirect(request.referrer or url_for('index'))

    model_name, setter = entry
    model = {'direct_sale': DirectSale, 'booking': Booking, 'payment': Payment,
             'material_return': MaterialReturn, 'grn': GRN, 'entry': Entry}.get(model_name)
    reason = (request.form.get('reason') or '').strip()
    if not reason:
        flash('A reason is required to void a posted document.', 'danger')
        return redirect(request.referrer or url_for('index'))

    try:
        obj = db.session.get(model, id) if model is not None else None
        if obj is None:
            flash(f'{type} not found', 'warning')
            return redirect(request.referrer or url_for('index'))
        changed = setter(obj, True)
        db.session.add(AuditLog(
            user_id=getattr(current_user, 'id', None),
            action=f'transaction.void.{type}',
            details=f'id={id}, reason={reason}'
        ))
        db.session.commit()
        if changed:
            flash(f'{type} voided — document kept, effects reversed.', 'success')
        else:
            flash(f'{type} was already void.', 'info')
    except Exception as exc:
        db.session.rollback()
        logging.getLogger(__name__).exception('Void failed')
        flash(f'Unable to void: {exc or "the record could not be voided. Please try again."}', 'danger')
    return redirect(request.referrer or url_for('index'))


@bp.route('/delete_transaction/<string:type>/<int:id>', methods=['POST'])
@login_required
def delete_transaction(type, id):
    """Permanently remove a document (destructive, admin-only, must be typed).

    Reserved for drafts and data-entry mistakes.  A posted bill that a customer
    holds should be voided, not deleted -- that keeps the bill number, the line
    detail and the evidence intact.
    """
    if getattr(current_user, 'role', '') != 'admin' and not _user_can('can_manage_sales'):
        flash('Permission denied', 'danger')
        return redirect(request.referrer or url_for('index'))

    confirmation = (request.form.get('confirm') or '').strip().upper()
    if confirmation != 'DELETE':
        flash(
            'Permanent delete refused: type DELETE in the confirmation box. '
            'Use Void to reverse a posted bill and keep its record.',
            'danger'
        )
        return redirect(request.referrer or url_for('index'))

    try:
        ok = hard_delete_transaction(type, id)
        if ok:
            db.session.add(AuditLog(
                user_id=getattr(current_user, 'id', None),
                action=f'transaction.delete.{type}',
                details=f'id={id}, reason={(request.form.get("reason") or "").strip()}'
            ))
            db.session.commit()
            flash(f'{type} deleted', 'success')
        else:
            flash(f'{type} not found', 'warning')
    except Exception as exc:
        db.session.rollback()
        logging.getLogger(__name__).exception('Hard delete failed')
        flash(f'Unable to delete: {exc or "the record could not be deleted. Please try again."}', 'danger')
    return redirect(request.referrer or url_for('index'))
