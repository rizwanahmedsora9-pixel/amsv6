"""PART Q/R/S/T/U/V — pull every published figure + probe the overdraft rule."""
import json
import re
import sys

sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client, db_rows

c = Client()
c.get('/login')
c.post('/login', [('username', 'Admin'), ('password', 'Admin@fbm12345')])


def text(path):
    st, body = c.get(path)
    t = re.sub(r'<[^>]+>', ' ', body)
    return st, re.sub(r'\s+', ' ', t)


def between(t, start_kw, end_kw, limit=1200):
    i = t.find(start_kw)
    if i < 0:
        return f'[{start_kw} NOT FOUND]'
    j = t.find(end_kw, i + len(start_kw))
    return t[i:(j if j > 0 else i + limit)][:limit]


out = {}

# ---------------------------------------------------------------- stock value
st, t = text('/stock_summary')
out['stock_summary'] = between(t, 'Material Brand Category', 'Reason (if variance)', 700)

st, t = text('/material_ledger/1')
out['material_ledger'] = between(t, 'Cement Bag', 'Lost', 900)

# ------------------------------------------------------------------- bookings
st, t = text('/bookings')
out['bookings'] = between(t, 'Asif', 'Bilal', 900)
for kw in ('Pending', 'Remaining', 'Booked', 'Delivered'):
    i = t.find(kw)
    if i > 0:
        out[f'bookings_{kw}'] = t[max(0, i - 200):i + 260]

# --------------------------------------------------------------- payables etc
st, t = text('/accounts/purchase_payables')
out['purchase_payables'] = between(t, 'Payable', 'Nadeem', 900) or t[:600]
i = t.find('Nadeem')
out['purchase_payables_nadeem'] = t[max(0, i - 400):i + 500] if i > 0 else ''

st, t = text('/unpaid_transactions')
out['unpaid'] = between(t, 'Unpaid', 'Bilal', 800)

st, t = text('/financial_tracker')
out['financial_tracker'] = between(t, 'Receivable', 'Payable', 800)

st, t = text('/accounts/expenditures')
out['expenditures'] = between(t, 'Expenditure', 'Owner', 900)

st, t = text('/accounts/receipts')
out['receipts'] = between(t, 'Receipt', 'Total', 900)

st, t = text('/accounts/payments/clients')
out['client_payments'] = between(t, 'Asif', 'Bilal', 800)

st, t = text('/accounts/payments/suppliers')
out['supplier_payments'] = between(t, 'Nadeem', 'Total', 800)

st, t = text('/accounts/pay_grn')
out['pay_grn'] = between(t, 'GRN', 'Total', 900)

# ------------------------------------------------------- overdraft probe (live)
before = db_rows('select balance from account where id=1')[0]['balance']
probe = c.post('/cash_flow', [
    ('action', 'record_movement'), ('direction', 'out'),
    ('amount', str(before + 5000)), ('cash_account_id', '1'),
    ('category_name', 'Rent'), ('party_name', 'Overdraft probe'),
    ('party_type', 'other'), ('description', 'Overdraft probe - should be refused'),
    ('movement_date', '2026-11-15'),
    ('idempotency_key', 'e2e-overdraft-probe'),
])
after = db_rows('select balance from account where id=1')[0]['balance']
out['overdraft_probe'] = {
    'balance_before': before, 'attempted': before + 5000,
    'status': probe['status'], 'flashes': probe['flashes'],
    'balance_after': after, 'refused': not probe['ok'],
}

# --------------------------------------- retry Part C now (balance is higher)
c_retry = c.post('/add_supplier_payment', [
    ('supplier_id', '1'), ('amount', '50000'), ('date', '2026-11-02'),
    ('method', 'Cash'), ('payment_account_id', '1'),
    ('note', 'Part payment against GRN-1 (retry at 2 Nov business date)'),
    ('idempotency_key', 'e2e-C-50000-retry2'), ('return_to', '/suppliers'),
])
out['part_c_retry'] = {'balance_now': after, 'amount': 50000,
                       'status': c_retry['status'], 'flashes': c_retry['flashes'],
                       'ok': c_retry['ok']}

json.dump(out, open('/home/user/q_detail.json', 'w'), indent=2)
for k, v in out.items():
    print(f'\n===== {k} =====')
    print(v if isinstance(v, str) else json.dumps(v, indent=2))
