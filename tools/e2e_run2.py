"""Continuation of the master test: GRN-expense payment, correct sale entry,
and the two owner steps the app refuses (C, F).

Everything posts through the app's own routes.
"""
import json
import sys

sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client, db_rows

OUT = '/home/user/e2e_results_cont.json'
RESULTS = []


def rec(part, label, res, expected=''):
    item = {'part': part, 'label': label, 'path': res.get('path'),
            'status': res.get('status'), 'location': res.get('location'),
            'flashes': res.get('flashes') or [], 'ok': bool(res.get('ok')),
            'error': res.get('error'), 'expected': expected}
    RESULTS.append(item)
    print(f"[{'OK  ' if item['ok'] else 'FAIL'}] {part:3s} | {label[:56]:56s} | "
          f"{item['status']} -> {str(item['location'])[:60]}")
    for k, t in item['flashes']:
        print(f'          ({k}) {t[:260]}')
    return item


c = Client()
c.get('/login')
c.post('/login', [('username', 'Admin'), ('password', 'Admin@fbm12345')])

# --- B continuation: the freight was PAID on the GRN date, but the GRN form has
#     no paid control (owner rule A1).  Pay it on the Pay GRN Expenses screen.
print('GRN expense obligations:')
print(db_rows('select id,grn_id,charge_type,label,amount,recipient_type,include_in_supplier '
              'from grn_charge'))

rec('B', 'Pay GRN-1 freight 3,000 to transporter (1 Nov)', c.post('/accounts/grn_expenses/pay', [
    ('grn_expense_grn_id', '1'),
    ('expense_key[]', 'CHARGE:1'),
    ('expense_amount[]', '3000'),
    ('method', 'Cash'), ('payment_account_id', '1'),
    ('date_posted', '2026-11-01'),
    ('note', 'Freight already paid at unloading'),
    ('idempotency_key', 'e2e-B-freight-paid'),
]), 'freight 3,000 settled in cash on GRN date')

# --- C: supplier payment 50,000 cash (2 Nov).  Account holds 16,100 here.
rec('C', 'Supplier payment 50,000 cash (2 Nov)', c.post('/add_supplier_payment', [
    ('supplier_id', '1'), ('amount', '50000'), ('date', '2026-11-02'),
    ('method', 'Cash'), ('payment_account_id', '1'),
    ('note', 'Part payment against GRN-1'),
    ('idempotency_key', 'e2e-C-50000-retry'), ('return_to', '/suppliers'),
]), 'cash -50,000; supplier payable -50,000')

# --- F: return of 2 bags from a BOOKED delivery (6 Nov)
rec('F', 'Material return 2 bags @1500 booked (6 Nov)', c.post('/add_material_return', [
    ('client_code', 'ASIF-001'), ('client_name', 'Asif'),
    ('date', '2026-11-06'), ('return_type', 'booked'),
    ('material_id[]', '1'), ('material_name[]', 'Cement Bag'),
    ('qty[]', '2'), ('unit_rate[]', '1500'), ('rent_rate[]', '0'),
    ('service_id[]', ''), ('note', '2 bags returned from the 5 Nov booking delivery'),
]), 'stock +2, client credit 3,000')

# --- J (re-entry): credit sale Bilal 20 bags @1550 = 31,000 unpaid (10 Nov)
rec('J', 'Credit sale Bilal 20 bags @1550 = 31,000 unpaid (10 Nov)',
    c.post('/add_direct_sale', [
        ('category', 'Credit Customer'),
        ('client_code', 'BILAL-002'), ('client_name', 'Bilal'), ('client', 'Bilal'),
        ('sale_date', '2026-11-10'),
        ('material_id[]', '1'), ('product_name[]', 'Cement Bag'),
        ('qty[]', '20'), ('unit_rate[]', '1550'), ('service_id[]', ''),
        ('amount', '31000'), ('discount', '0'), ('discount_reason', ''),
        ('paid_amount', '0'), ('payment_method', 'Cash'), ('payment_account_id', ''),
        ('has_bill', '1'), ('create_invoice', '1'),
        ('delivery_person_id[]', '1'), ('delivery_bags[]', '20'),
        ('delivery_rent_amount[]', '0'),
        ('note', 'Credit sale to Bilal'),
        ('idempotency_key', 'e2e-J-credit-2'),
    ]), 'revenue 31,000; Bilal owes 31,000; stock -20')

# --- I: cash sale 10 bags @1600 - 1,000 discount = 15,000 paid (9 Nov)
rec('I', 'Cash sale 10 bags @1600 -1000 disc = 15,000 paid (9 Nov)',
    c.post('/add_direct_sale', [
        ('category', 'Cash'),
        ('client_code', 'OPEN-KHATA'), ('client_name', 'Walk-in customer'),
        ('client', 'OPEN-KHATA'),
        ('sale_date', '2026-11-09'),
        ('material_id[]', '1'), ('product_name[]', 'Cement Bag'),
        ('qty[]', '10'), ('unit_rate[]', '1600'), ('service_id[]', ''),
        ('amount', '16000'), ('discount', '1000'),
        ('discount_reason', 'Cash sale negotiation'),
        ('paid_amount', '15000'), ('payment_method', 'Cash'),
        ('payment_account_id', '1'),
        ('has_bill', '1'), ('create_invoice', '1'),
        ('delivery_person_id[]', '1'), ('delivery_bags[]', '10'),
        ('delivery_rent_amount[]', '0'),
        ('note', 'Cash sale 10 bags'),
        ('idempotency_key', 'e2e-I-cashsale-2'),
    ]), 'revenue 15,000; stock -10; cash +15,000')

with open(OUT, 'w') as fh:
    json.dump(RESULTS, fh, indent=2)

print('\n--- ledger cross-check ---')
print('account  ', db_rows('select id,name,balance from account'))
print('sales    ', db_rows('select id,category,client_name,amount,discount,paid_amount,'
                           'auto_bill_no,date_posted,is_void from direct_sale'))
print('entries  ', db_rows('select id,type,client_code,material,qty,bill_no,nimbus_no,'
                           'client_category from entry'))
print('grn pay  ', db_rows('select id,grn_id,amount,payment_kind,date_posted from supplier_payment'))
