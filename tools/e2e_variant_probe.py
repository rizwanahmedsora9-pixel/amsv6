"""Focused probe: which GRN charge-billing configuration makes Nadeem's
outstanding balance come out at the owner's expected 111,000 while the cash
account still shows 74,000 in / 92,900 out?

Only the purchase side is entered (masters, GRN 1, freight payment, supplier
payment 50,000, GRN 2, supplier payment 30,000) so each variant is cheap.
Run against an ISOLATED copy; every POST goes through the real forms.
"""
import json
import sys

sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client, db_rows

BILL_G1_FREIGHT = True
BILL_G1_LOADING = True
BILL_G2_FREIGHT = True
PAY_FREIGHT_SEPARATELY = True


def rec(label, res):
    print(f"[{'OK  ' if res.get('ok') else 'FAIL'}] {label:52s} {res.get('status')} "
          f"-> {str(res.get('location'))[:40]}")
    for k, t in (res.get('flashes') or []):
        print(f'        ({k}) {t[:170]}')


c = Client()
c.get('/login')
c.post('/login', [('username', 'Admin'), ('password', 'Admin@fbm12345')])

rec('client Asif', c.post('/add_client', [
    ('name', 'Asif'), ('code', 'ASIF-001'), ('category', 'General'),
    ('phone', '03000000001'), ('opening_balance', '0'),
    ('opening_balance_date', '2026-11-01'), ('page_entry', 'bookings')]))
rec('supplier Nadeem', c.post('/add_supplier', [
    ('name', 'Nadeem'), ('phone', '03000000003'), ('opening_balance', '0'),
    ('opening_balance_date', '2026-11-01')]))
rec('material Cement Bag', c.post('/add_material', [
    ('material_name', 'Cement Bag'), ('material_code', 'CEM-01'),
    ('category_id', '1'), ('material_unit', 'Bag')]))
rec('cash account 200,000 opening', c.post('/accounts/accounts/add', [
    ('name', 'Main Cash'), ('class_category', 'Assets'),
    ('class_subcategory', 'Cash'), ('class_account_type', 'Main Cash'),
    ('channel', 'cash'), ('account_status', 'active'),
    ('opening_amount', '200000'), ('opening_position', 'debit'),
    ('opening_effective_date', '2026-10-31'), ('cash_location', 'Shop')]))


def grn(date, qty, price, freight, loading, bill_freight, bill_loading, inv):
    data = [
        ('action', 'add'), ('supplier', 'Nadeem'), ('supplier_id', '1'),
        ('date', date), ('time', '09:00'), ('item_grid_present', '1'),
        ('grn_item_id[]', ''), ('mat_name[]', 'Cement Bag'),
        ('qty[]', str(qty)), ('price[]', str(price)),
        ('loading_cost', str(loading)), ('freight_cost', str(freight)),
        ('other_expense', '0'),
        ('charge_grid_present', '1'), ('charge_supplier_billing_present', '1'),
        ('charge_id[]', ''), ('charge_type[]', 'VEHICLE_FARE'),
        ('charge_label[]', 'Freight'), ('charge_amount[]', str(freight)),
        ('charge_row_key[]', 'rf'), ('charge_recipient_type[]', 'TRANSPORTER'),
        ('charge_recipient[]', 'Rana Transport'),
        ('charge_treatment[]', 'INVENTORY'), ('charge_basis[]', 'VALUE'),
    ]
    if bill_freight:
        data.append(('charge_include_in_supplier[]', 'rf'))
    if loading:
        data += [('charge_id[]', ''), ('charge_type[]', 'LOADING'),
                 ('charge_label[]', 'Loading'), ('charge_amount[]', str(loading)),
                 ('charge_row_key[]', 'rl'),
                 ('charge_recipient_type[]', 'LABOUR'),
                 ('charge_recipient[]', 'Loading crew'),
                 ('charge_treatment[]', 'INVENTORY'), ('charge_basis[]', 'VALUE')]
        if bill_loading:
            data.append(('charge_include_in_supplier[]', 'rl'))
    data += [('allocation_basis_default', 'VALUE'),
             ('supplier_invoice_no', inv), ('bill_date', date),
             ('due_date', '2026-11-30')]
    return c.post('/grn', data)


rec('GRN 1 (100 @1200 + fr 3000 + load 1000)', grn(
    '2026-11-01', 100, 1200, 3000, 1000, BILL_G1_FREIGHT, BILL_G1_LOADING, 'INV-1'))

if PAY_FREIGHT_SEPARATELY:
    rec('Pay freight 3,000 to transporter', c.post('/accounts/grn_expenses/pay', [
        ('grn_expense_grn_id', '1'), ('expense_key[]', 'CHARGE:1'),
        ('expense_amount[]', '3000'), ('grn_expense_method', 'Cash'),
        ('grn_expense_account_id', '1'), ('date_posted', '2026-11-01'),
        ('grn_expense_idempotency_key', 'vp-freight')]))

rec('Supplier payment 50,000', c.post('/add_supplier_payment', [
    ('supplier_id', '1'), ('amount', '50000'), ('date', '2026-11-02'),
    ('method', 'Cash'), ('payment_account_id', '1'),
    ('idempotency_key', 'vp-c50'), ('return_to', '/suppliers')]))

rec('GRN 2 (50 @1300 + fr 2000)', grn(
    '2026-11-14', 50, 1300, 2000, 0, BILL_G2_FREIGHT, False, 'INV-2'))

rec('Supplier payment 30,000', c.post('/add_supplier_payment', [
    ('supplier_id', '1'), ('amount', '30000'), ('date', '2026-11-15'),
    ('method', 'Cash'), ('payment_account_id', '1'),
    ('idempotency_key', 'vp-o30'), ('return_to', '/suppliers')]))

print('\n--- result ---')
print('charges:', db_rows('select id,grn_id,charge_type,amount,include_in_supplier from grn_charge'))
print('supplier payments:', db_rows('select id,amount,payment_kind from supplier_payment'))
print('cash account:', db_rows('select balance from account'))
st, body = c.get('/api/supplier_balance/1')
print('supplier balance API:', body.strip())
