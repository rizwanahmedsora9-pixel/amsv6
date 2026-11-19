from ._common import *  # noqa


@bp.route('/reclaim_client/<int:id>', methods=['POST'])
@login_required
def reclaim_client(id):
    # A link to the target client is not proof of which rows were transferred.
    # The old reclaim moved the target's original and later rows too.
    flash('Legacy transfers cannot be reclaimed automatically. Ask an administrator to compare backups. No records were changed.', 'warning')
    return redirect(url_for('clients'))
