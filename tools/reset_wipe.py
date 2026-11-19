"""Run the real Settings -> Data Wipe route, exactly as the UI posts it.

SAFETY (added after an incident on 2026-11-18): ``app.create_app()`` inserts the
*AMS root* into sys.path and hard-codes ``instance_path=root/instance``, so
running this file from a *copy* of the tree still wipes the AMS live database.
The tool therefore refuses to run unless the operator passes the DB path it is
about to destroy and that path matches what the app actually resolved.

Usage:
    python3 tools/reset_wipe.py --confirm-db /home/user/AMS/instance/ahmed_cement_v44_fresh.db
"""
import os
import sys
import urllib.parse

APP_ROOT = '/home/user/AMS'
sys.path.insert(0, APP_ROOT)
os.environ['AMS_ENABLE_BACKGROUND_WORKERS'] = '0'
os.environ['AMS_LOGIN_THROTTLE'] = '0'

from app import create_app  # noqa: E402

TARGETS = [
    'clients', 'suppliers', 'supplier_payments', 'pending_bills', 'notifications',
    'bookings', 'dispatching', 'receiving', 'stock_adjustments', 'grn', 'materials',
    'material_categories', 'services', 'direct_sales', 'material_returns',
    'delivery_rents', 'delivery_persons', 'delivery_person_payments', 'invoices',
    'payments', 'direct_sale_drafts', 'accounts', 'account_categories',
    'account_transactions', 'account_reconciliations', 'cash_flow_entries',
    'cash_flow_categories', 'daily_reconciliation', 'cash_reconciliation_data',
    'cash_reconciliation_audit', 'cash_drawer_entries', 'cash_drawer_categories',
    'fbm_rentals', 'fbm_rental_clients', 'fbm_rental_items',
]

app = create_app()
resolved = app.config.get('SQLALCHEMY_DATABASE_URI', '')
print('app resolved DB ->', resolved)

confirm = None
if '--confirm-db' in sys.argv:
    confirm = sys.argv[sys.argv.index('--confirm-db') + 1]
if not confirm:
    sys.exit('REFUSING TO WIPE: pass --confirm-db <path> naming the database you '
             'intend to destroy, after checking the resolved path above.')
if confirm not in resolved:
    sys.exit(f'REFUSING TO WIPE: --confirm-db {confirm} does not match {resolved}')

app.config['WTF_CSRF_ENABLED'] = False
client = app.test_client()

r = client.post('/login', data={'username': 'Admin', 'password': 'Admin@fbm12345'},
                follow_redirects=True)
print('login ->', r.status_code)

data = [('delete_targets', t) for t in TARGETS]
data += [('confirm_text', 'DELETE ALL DATA'), ('hard_delete_override', '1')]
body = urllib.parse.urlencode(data)

r = client.post('/delete_selected_data', data=body,
                content_type='application/x-www-form-urlencoded', follow_redirects=False)
print('wipe POST ->', r.status_code, '| redirect:', r.headers.get('Location'))

with client.session_transaction() as sess:
    for cat, msg in (sess.get('_flashes') or []):
        print(f'  flash[{cat}]: {msg[:200]}')
