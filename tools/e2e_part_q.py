"""PART Q — read every reporting screen and pull the numbers it publishes."""
import json
import re
import sys

sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client, db_rows, parse_flashes

c = Client()
c.get('/login')
c.post('/login', [('username', 'Admin'), ('password', 'Admin@fbm12345')])

PAGES = [
    ('P&L', '/profit_reports?start_date=2026-11-01&end_date=2026-11-15&period=custom&view=overview'),
    ('P&L entries', '/profit_reports?start_date=2026-11-01&end_date=2026-11-15&period=custom&view=entries'),
    ('Stock summary', '/stock_summary'),
    ('Asif ledger', '/client_ledger/2'),
    ('Bilal ledger', '/client_ledger/3'),
    ('Supplier ledger (Nadeem)', '/supplier_ledger/1'),
    ('Cash flow', '/cash_flow?from_date=2026-11-01&to_date=2026-11-15&filter_type=all'),
    ('Accounts dashboard', '/accounts/'),
    ('Purchase payables', '/accounts/purchase_payables'),
    ('Unpaid transactions', '/unpaid_transactions'),
    ('Financial tracker', '/financial_tracker'),
    ('Daily transactions', '/daily_transactions'),
    ('Inventory log', '/inventory_log'),
    ('Material ledger (cement)', '/material_ledger/1'),
    ('Bookings', '/bookings'),
    ('Direct sales', '/direct_sales'),
    ('Material returns', '/material_returns'),
    ('Payments (clients)', '/accounts/payments/clients'),
    ('Supplier payments', '/accounts/payments/suppliers'),
    ('Expenditures', '/accounts/expenditures'),
    ('Receipts', '/accounts/receipts'),
    ('Pay GRN expenses', '/accounts/pay_grn'),
    ('GRN payments', '/accounts/grn_payments'),
    ('Integrity audit', '/api/audit/financial-integrity'),
]

summary = {}
for label, path in PAGES:
    status, body = c.get(path)
    summary[label] = {'path': path, 'status': status, 'bytes': len(body)}
    print(f'{status} {label:32s} {len(body):>8d}B  {path}')

json.dump(summary, open('/home/user/q_pages.json', 'w'), indent=2)

# ---- extract the headline P&L figures the page publishes
st, body = c.get(PAGES[0][1])
for label in ('Net Sales', 'Gross Profit', 'Operating Expenses', 'Net Profit', 'Cost of Goods',
              'COGS', 'Revenue'):
    for m in re.finditer(re.escape(label) + r'[^0-9\-]{0,200}?(-?[\d,]+\.\d\d|-?[\d,]{3,})', body):
        print(f'  P&L {label}: {m.group(1)}')
        break

txt = re.sub(r'<[^>]+>', ' ', body)
txt = re.sub(r'\s+', ' ', txt)
open('/home/user/q_pl_overview.txt', 'w').write(txt)
print('\nwrote /home/user/q_pl_overview.txt', len(txt))
