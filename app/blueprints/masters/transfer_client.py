from ._common import *  # noqa


@bp.route('/transfer_client/<int:id>', methods=['POST'])
@login_required
def transfer_client(id):
    # The old operation rewrites only entries, pending bills and waive-offs.
    # Sales, bookings and payments remain behind, and there is no record of
    # which rows moved. Keep old links non-mutating until a complete, audited
    # transfer can preserve the ownership of each record.
    flash('Client data transfer is paused to protect ledger history. No records were changed.', 'warning')
    return redirect(url_for('clients'))
