"""extra — split from misc.py."""
from ._common import *  # noqa
from flask import current_app

@bp.route('/toggle_bill_paid/<int:id>', methods=['POST'])
@login_required
def toggle_bill_paid(id):
    bill = db.session.get(PendingBill, id)
    if bill:
        marking_paid = not bill.is_paid
        if marking_paid:
            _mark_pending_bill_paid_and_stop_reminders(
                bill,
                created_by=(current_user.username if current_user.is_authenticated else ''),
            )
        else:
            bill.is_paid = False
            is_open_khata_bill = (
                bill.client_code == OPEN_KHATA_CODE or
                (bill.client_name or '').strip().upper() == OPEN_KHATA_NAME
            )
            if is_open_khata_bill:
                bill.is_cash = False
        db.session.commit()
        return jsonify({'success': True, 'is_paid': bill.is_paid})
    return jsonify({'success': False}), 404


@bp.route('/export_unpaid_transactions')
@login_required
def export_unpaid_transactions():
    if current_user.role not in ['admin', 'root']:
        flash('Only tenant admin or root can run import/export operations.', 'danger')
        return redirect(url_for('index'))
    """Redirects to the generic export function for unpaid transactions."""
    args = request.args.to_dict()
    args['dataset'] = 'unpaid_transactions'
    return redirect(url_for('import_export.export_data', **args))


@bp.route('/fix_system_issues')
@login_required
def fix_system_issues():
    """Diagnose pending-bill / stock drift WITHOUT changing any data.

    This route used to run ``_rebuild_direct_sale_pending_bills()`` and
    ``_rebuild_material_totals()`` in place.  That rebuild ignored payments
    entirely, so one click on "Auto-Fix Issues" re-billed money the customer had
    already paid — a 1,000 sale that was fully paid showed 1,000 owed and 1,000
    received at the same time, and the pending_bill table grew on every click.

    Nothing that rewrites accounting may be reachable from a single GET, so the
    route now only reports what it finds.  Repairs stay where they belong: the
    delete/rebuild paths that reverse their own effects.
    """
    if current_user.role != 'admin': return redirect(url_for('index'))

    findings = []
    try:
        from app.services.void_rebuild import _direct_sale_bill_refs
        from models import DirectSale, PendingBill

        sales = DirectSale.query.filter(
            (DirectSale.is_void == False) | (DirectSale.is_void.is_(None))).all()
        missing = 0
        for sale in sales:
            refs = _direct_sale_bill_refs(sale)
            if not refs:
                continue
            row = PendingBill.query.filter(
                PendingBill.bill_no.in_(list(refs)),
                (PendingBill.is_void == False) | (PendingBill.is_void.is_(None))).first()
            if row is None:
                missing += 1
        if missing:
            findings.append(
                f'{missing} direct sale(s) have no pending bill row.')
    except Exception:
        logging.getLogger(__name__).exception('pending-bill diagnosis failed')

    try:
        # Same aggregation _rebuild_material_totals() would write, but only
        # compared — the stored total is never touched here.
        from models import Entry, Material

        def _grouped(entry_type):
            return dict(db.session.query(Entry.material, func.sum(Entry.qty)).filter(
                Entry.type == entry_type, Entry.is_void == False).group_by(Entry.material).all())

        incoming, outgoing = _grouped('IN'), _grouped('OUT')
        drifted = 0
        for mat in Material.query.all():
            expected = float(incoming.get(mat.name, 0) or 0) - float(outgoing.get(mat.name, 0) or 0)
            if abs(float(mat.total or 0) - expected) > 0.0001:
                drifted += 1
        if drifted:
            findings.append(
                f'{drifted} material(s) whose stock total does not match their movements.')
    except Exception:
        logging.getLogger(__name__).exception('material total diagnosis failed')

    if findings:
        flash('Diagnosis complete — no data was changed. ' + ' '.join(findings), 'warning')
    else:
        flash('Diagnosis complete — no data was changed, and no drift was found.', 'success')
    return redirect(url_for('system_report'))


@bp.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    # ``basedir`` is not imported in this module; referencing it raised
    # NameError -> HTTP 500 for every uploaded attachment.  Use the
    # configured upload directory instead (same default path).
    upload_dir = current_app.config.get('UPLOAD_DIR') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))),
        'static', 'uploads',
    )
    if not os.path.isdir(upload_dir):
        abort(404)
    return send_from_directory(upload_dir, filename)


@bp.route('/change_password', methods=['POST'])
@login_required
def change_password():
    raw_pw = str(request.form.get('password') or '').strip()
    if not raw_pw:
        flash('Password is required', 'danger')
        return redirect(url_for('settings'))
    current_user.password_hash = generate_password_hash(raw_pw)
    current_user.password_plain = None
    db.session.commit()
    flash('Password Updated', 'success')
    return redirect(url_for('settings'))


@bp.route('/delete_all_data', methods=['POST'])
@login_required
def delete_all_data():
    return redirect(url_for('settings'))


@bp.route('/generate_dummy_data')
@login_required
def generate_dummy_data():
    flash('This legacy test-data feature has been permanently removed.', 'warning')
    return redirect(url_for('settings'))


