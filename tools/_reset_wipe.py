"""Run the real Settings -> Data Wipe route, exactly as the UI posts it."""
import os
import sys

APP_ROOT = '/home/user/AMS'
sys.path.insert(0, APP_ROOT)
os.environ['AMS_ENABLE_BACKGROUND_WORKERS'] = '0'
os.environ['AMS_LOGIN_THROTTLE'] = '0'

from app import create_app

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
app.config['WTF_CSRF_ENABLED'] = False
client = app.test_client()

r = client.post('/login', data={'username': 'Admin', 'password': 'Admin@fbm12345'},
                follow_redirects=True)
print('login ->', r.status_code)

data = [('delete_targets', t) for t in TARGETS]
data += [('confirm_text', 'DELETE ALL DATA'), ('hard_delete_override', '1')]
import urllib.parse
body = urllib.parse.urlencode(data)

r = client.post('/delete_selected_data', data=body,
                content_type='application/x-www-form-urlencoded', follow_redirects=False)
print('wipe POST ->', r.status_code, '| redirect:', r.headers.get('Location'))

# Read the flash messages the operator would see
with client.session_transaction() as sess:
    for cat, msg in (sess.get('_flashes') or []):
        print(f'  flash[{cat}]: {msg[:200]}')
