"""profit — split from reports.py."""
from ._common import *  # noqa

@bp.route('/stock_summary')
@login_required
def stock_summary_redirect():
    return redirect(url_for('inventory.stock_summary'))


@bp.route('/lost_ledger')
@login_required
def lost_ledger_redirect():
    return redirect(url_for('inventory.lost_ledger'))

