"""MASTER ERP E2E — RUN 2, with a FUNDED cash account.

Owner's correction: the cash account now carries an opening balance
(PKR 200,000, effective 31 Oct 2026 — i.e. BEFORE the test period, so it is an
opening position and not an in-period receipt) so the supplier payment on
2 Nov can actually post.  Every transaction still goes through the real forms.

Business period: 1 Nov 2026 – 15 Nov 2026.
"""
import json
import os
import sys

sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client, db_rows

OPENING_CASH = os.environ.get('E2E_OPENING_CASH', '200000')
# Variant B: freight charges are ticked "Add this expense to the supplier ledger",
# so the supplier payable carries them (testing the owner's 111,000 expectation).
VARIANT = os.environ.get('E2E_VARIANT', 'A')
BILL_TO_SUPPLIER = VARIANT.upper() == 'B'
RESULTS = []


def rec(part, label, res, expected=''):
    item = {'part': part, 'label': label, 'path': res.get('path'),
            'status': res.get('status'), 'location': res.get('location'),
            'flashes': res.get('flashes') or [], 'ok': bool(res.get('ok')),
            'error': res.get('error'), 'expected': expected}
    RESULTS.append(item)
    print(f"[{'OK  ' if item['ok'] else 'FAIL'}] {part:3s} | {label[:58]:58s} | "
          f"{item['status']} -> {str(item['location'])[:55]}")
    for k, t in item['flashes']:
        print(f'          ({k}) {t[:240]}')
    return item


c = Client()
c.get('/login')
c.post('/login', [('username', 'Admin'), ('password', 'Admin@fbm12345')])

# =========================== A — masters ==================================
rec('A', 'Client Asif', c.post('/add_client', [
    ('name', 'Asif'), ('code', 'ASIF-001'), ('category', 'General'),
    ('phone', '03000000001'), ('address', 'Islamabad'),
    ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
    ('page_entry', 'bookings')]))
rec('A', 'Client Bilal', c.post('/add_client', [
    ('name', 'Bilal'), ('code', 'BILAL-002'), ('category', 'General'),
    ('phone', '03000000002'), ('address', 'Islamabad'),
    ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
    ('page_entry', 'bookings')]))
rec('A', 'Supplier Nadeem', c.post('/add_supplier', [
    ('name', 'Nadeem'), ('phone', '03000000003'), ('address', 'Islamabad'),
    ('opening_balance', '0'), ('opening_balance_date', '2026-11-01')]))
for name, code, unit in (('Cement Bag', 'CEM-01', 'Bag'),
                         ('12mm Steel', 'STL-12', 'KG'),
                         ('20mm Steel', 'STL-20', 'KG')):
    rec('A', f'Material {name}', c.post('/add_material', [
        ('material_name', name), ('material_code', code),
        ('category_id', '1'), ('material_unit', unit)]))

rec('A', f'Cash account "Main Cash" opening {OPENING_CASH} (31 Oct)',
    c.post('/accounts/accounts/add', [
        ('name', 'Main Cash'), ('class_category', 'Assets'),
        ('class_subcategory', 'Cash'), ('class_account_type', 'Main Cash'),
        ('channel', 'cash'), ('account_status', 'active'),
        ('opening_amount', OPENING_CASH), ('opening_position', 'debit'),
        ('opening_effective_date', '2026-10-31'),
        ('cash_location', 'Shop'),
        ('note', 'Opening cash position funded by owner'),
    ]), f'opening cash {OPENING_CASH}')

# =========================== B — GRN 1 ====================================
rec('B', 'GRN 1: 100 bags @1200 + freight 3000 + loading 1000 = 124,000',
    c.post('/grn', [
        ('action', 'add'), ('supplier', 'Nadeem'), ('supplier_id', '1'),
        ('date', '2026-11-01'), ('time', '09:00'),
        ('item_grid_present', '1'),
        ('grn_item_id[]', ''), ('mat_name[]', 'Cement Bag'),
        ('qty[]', '100'), ('price[]', '1200'),
        ('loading_cost', '1000'), ('freight_cost', '3000'), ('other_expense', '0'),
        ('charge_grid_present', '1'), ('charge_supplier_billing_present', '1'),
        ('charge_id[]', ''), ('charge_type[]', 'VEHICLE_FARE'),
        ('charge_label[]', 'Freight to Islamabad'), ('charge_amount[]', '3000'),
        ('charge_row_key[]', 'row-freight'),
        ('charge_recipient_type[]', 'TRANSPORTER'),
        ('charge_recipient[]', 'Rana Transport'),
        ('charge_treatment[]', 'INVENTORY'), ('charge_basis[]', 'VALUE'),
        *(([('charge_include_in_supplier[]', 'row-freight')] if BILL_TO_SUPPLIER else [])),
        ('charge_id[]', ''), ('charge_type[]', 'LOADING'),
        ('charge_label[]', 'Loading at plant'), ('charge_amount[]', '1000'),
        ('charge_row_key[]', 'row-loading'),
        ('charge_recipient_type[]', 'LABOUR'),
        ('charge_recipient[]', 'Loading crew'),
        ('charge_treatment[]', 'INVENTORY'), ('charge_basis[]', 'VALUE'),
        *(([('charge_include_in_supplier[]', 'row-loading')] if BILL_TO_SUPPLIER else [])),
        ('allocation_basis_default', 'VALUE'),
        ('supplier_invoice_no', 'NAD-INV-001'),
        ('bill_date', '2026-11-01'), ('due_date', '2026-11-15'),
        ('note', 'GRN 1 — 100 bags cement'),
    ]), 'landed 124,000; stock 100 bags')

# freight was PAID on the day (the GRN grid has no paid control)
rec('B2', 'Freight 3,000 PAID to transporter (1 Nov)' + (' [SKIPPED: billed to supplier in variant B]' if BILL_TO_SUPPLIER else ''),
    {'path': '/accounts/grn_expenses/pay', 'status': 'skipped', 'location': '', 'flashes': [], 'ok': True, 'error': None} if BILL_TO_SUPPLIER else
    c.post('/accounts/grn_expenses/pay', [
        ('grn_expense_grn_id', '1'),
        ('expense_key[]', 'CHARGE:1'), ('expense_amount[]', '3000'),
        ('grn_expense_method', 'Cash'), ('grn_expense_account_id', '1'),
        ('date_posted', '2026-11-01'),
        ('note', 'Freight paid at unloading'),
        ('grn_expense_idempotency_key', 'r2-B-freight'),
    ]), 'freight settled same day')

# =========================== C — supplier payment 50,000 ==================
rec('C', 'Supplier payment 50,000 cash (2 Nov)', c.post('/add_supplier_payment', [
    ('supplier_id', '1'), ('amount', '50000'), ('date', '2026-11-02'),
    ('method', 'Cash'), ('payment_account_id', '1'),
    ('note', 'Part payment against GRN-1'),
    ('idempotency_key', 'r2-C-50000'), ('return_to', '/suppliers'),
]), 'cash -50,000; supplier -50,000')

# =========================== D — booking =================================
rec('D', 'Booking Asif 50 bags @1500 = 75,000, advance 20,000 (3 Nov)',
    c.post('/add_booking', [
        ('client_code', 'ASIF-001'), ('client', 'Asif'),
        ('material_id[]', '1'), ('material_name[]', 'Cement Bag'),
        ('qty[]', '50'), ('unit_rate[]', '1500'), ('service_id[]', ''),
        ('amount', '75000'), ('paid_amount', '20000'),
        ('payment_method', 'Cash'), ('payment_account_id', '1'),
        ('date', '2026-11-03T09:00'), ('discount', '0'), ('discount_reason', ''),
        ('note', 'Booking 50 bags @1500'),
    ]), 'booking 75,000; advance 20,000')

# =========================== E — delivery 30 bags ========================
rec('E', 'Delivery 30 bags against booking (5 Nov)', c.post('/add_record', [
    ('type', 'OUT'), ('client', 'Asif'),
    ('material_id', '1'), ('material', 'Cement Bag'),
    ('qty', '30'), ('date', '2026-11-05'),
    ('driver_name', 'Rana Transport'),
    ('note', 'Delivery against booking of 3 Nov'),
    ('has_bill', '1'), ('create_invoice', '1'), ('track_as_cash', ''),
]), '30 bags out; 45,000 receivable')

# =========================== F — return 2 bags ===========================
rec('F', 'Material return 2 bags @1500 (Booked, 6 Nov)',
    c.post('/add_material_return', [
        ('client_code', 'ASIF-001'), ('client_name', 'Asif'),
        ('date', '2026-11-06'), ('return_type', 'booked'),
        ('material_id[]', '1'), ('material_name[]', 'Cement Bag'),
        ('qty[]', '2'), ('unit_rate[]', '1500'), ('rent_rate[]', '0'),
        ('service_id[]', ''), ('note', '2 bags returned'),
    ]), 'stock +2; client credit 3,000')

# =========================== G — receipt 24,000 ==========================
rec('G', 'Client payment 24,000 cash (7 Nov)', c.post('/add_payment', [
    ('client_code', 'ASIF-001'), ('client_name', 'Asif'),
    ('amount', '24000'), ('discount', '0'), ('discount_reason', ''),
    ('payment_type', 'Receipt'), ('method', 'Cash'),
    ('payment_account_id', '1'), ('date', '2026-11-07'),
    ('note', 'Receipt from Asif'), ('idempotency_key', 'r2-G-24000'),
]), 'cash +24,000')

# =========================== H — refund 2,000 ============================
rec('H', 'Refund 2,000 to Asif (8 Nov)', c.post('/add_payment', [
    ('client_code', 'ASIF-001'), ('client_name', 'Asif'),
    ('amount', '2000'), ('discount', '0'), ('discount_reason', ''),
    ('payment_type', 'Refund'), ('method', 'Cash'),
    ('payment_account_id', '1'), ('date', '2026-11-08'),
    ('note', 'Refund to Asif'), ('idempotency_key', 'r2-H-2000'),
]), 'cash -2,000')

# =========================== I — cash sale ===============================
rec('I', 'Cash sale 10 bags @1600 - 1000 disc = 15,000 paid (9 Nov)',
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
        ('idempotency_key', 'r2-I-cashsale'),
    ]), 'revenue 15,000; cash +15,000; stock -10')

# =========================== J — credit sale =============================
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
        ('idempotency_key', 'r2-J-credit'),
    ]), 'revenue 31,000; Bilal owes 31,000; stock -20')

# =========================== K — Bilal pays 15,000 =======================
rec('K', 'Bilal payment 15,000 cash (11 Nov)', c.post('/add_payment', [
    ('client_code', 'BILAL-002'), ('client_name', 'Bilal'),
    ('amount', '15000'), ('discount', '0'), ('discount_reason', ''),
    ('payment_type', 'Receipt'), ('method', 'Cash'),
    ('payment_account_id', '1'), ('date', '2026-11-11'),
    ('note', 'Part receipt from Bilal'), ('idempotency_key', 'r2-K-15000'),
]), 'Bilal 31,000-15,000=16,000')

# =========================== L — expenses ================================
for cat, label, amt, dt in (
    ('Rent', 'Shop rent for November', '2000', '2026-11-11'),
    ('Utilities', 'Electricity bill', '500', '2026-11-12'),
    ('Vehicle', 'Vehicle running / fuel', '400', '2026-11-12'),
):
    rec('L', f'Expense {label} {amt}', c.post('/cash_flow', [
        ('action', 'record_movement'), ('direction', 'out'),
        ('amount', amt), ('cash_account_id', '1'),
        ('category_name', cat), ('party_name', 'Landlord / WAPDA / Fuel'),
        ('party_type', 'other'), ('description', label),
        ('movement_note', label), ('movement_date', dt),
        ('idempotency_key', f'r2-L-{cat}-{amt}'),
    ]), f'opex {amt}')

# =========================== M — extra discount 500 ======================
rec('M', 'Extra discount 500 to Bilal (13 Nov)', c.post('/add_payment', [
    ('client_code', 'BILAL-002'), ('client_name', 'Bilal'),
    ('amount', '0'), ('discount', '500'),
    ('discount_reason', 'Goodwill discount on the 10 Nov sale'),
    ('payment_type', 'Waive-Off'), ('method', 'Cash'),
    ('payment_account_id', '1'), ('date', '2026-11-13'),
    ('note', 'Extra discount 500'), ('idempotency_key', 'r2-M-500'),
]), 'Bilal 16,000-500=15,500')

# =========================== N — GRN 2 ===================================
rec('N', 'GRN 2: 50 bags @1300 + freight 2000 unpaid = 67,000 (14 Nov)',
    c.post('/grn', [
        ('action', 'add'), ('supplier', 'Nadeem'), ('supplier_id', '1'),
        ('date', '2026-11-14'), ('time', '09:00'),
        ('item_grid_present', '1'),
        ('grn_item_id[]', ''), ('mat_name[]', 'Cement Bag'),
        ('qty[]', '50'), ('price[]', '1300'),
        ('loading_cost', '0'), ('freight_cost', '2000'), ('other_expense', '0'),
        ('charge_grid_present', '1'), ('charge_supplier_billing_present', '1'),
        ('charge_id[]', ''), ('charge_type[]', 'VEHICLE_FARE'),
        ('charge_label[]', 'Freight — GRN 2'), ('charge_amount[]', '2000'),
        ('charge_row_key[]', 'row-freight2'),
        ('charge_recipient_type[]', 'TRANSPORTER'),
        ('charge_recipient[]', 'Rana Transport'),
        ('charge_treatment[]', 'INVENTORY'), ('charge_basis[]', 'VALUE'),
        ('allocation_basis_default', 'VALUE'),
        ('supplier_invoice_no', 'NAD-INV-002'),
        ('bill_date', '2026-11-14'), ('due_date', '2026-11-30'),
        ('note', 'GRN 2 — 50 bags cement'),
    ]), 'landed 67,000; stock +50')

# =========================== O — supplier payment 30,000 =================
rec('O', 'Supplier payment 30,000 (15 Nov)', c.post('/add_supplier_payment', [
    ('supplier_id', '1'), ('amount', '30000'), ('date', '2026-11-15'),
    ('method', 'Cash'), ('payment_account_id', '1'),
    ('note', 'Payment against GRN-2'),
    ('idempotency_key', 'r2-O-30000'), ('return_to', '/suppliers'),
]), 'cash -30,000; supplier -30,000')

# =========================== P — owner withdrawal ========================
rec('P', 'Owner withdrawal 5,000 (15 Nov)', c.post('/cash_flow', [
    ('action', 'record_movement'), ('direction', 'out'),
    ('amount', '5000'), ('cash_account_id', '1'),
    ('category_name', 'Owner Drawings'), ('party_name', 'Owner'),
    ('party_type', 'partner'), ('description', 'Owner withdrawal'),
    ('movement_note', 'Owner withdrawal'), ('movement_date', '2026-11-15'),
    ('idempotency_key', 'r2-P-drawing'),
]), 'cash -5,000 (equity, not expense)')

json.dump(RESULTS, open(f'/home/user/e2e_run3_results_{VARIANT}.json', 'w'), indent=2)
ok = sum(1 for r in RESULTS if r['ok'])
print(f'\n===== {ok}/{len(RESULTS)} saved =====')
for r in RESULTS:
    if not r['ok']:
        print('  REFUSED:', r['part'], r['label'], '->', r['error'])

print('\n--- account / cash ---')
print(db_rows('select id,name,balance,opening_balance from account'))
print('--- supplier ---')
print('Nadeem billed/paid:',
      db_rows("select sum(landed_cost_total) from grn"))
print(db_rows('select id,amount,payment_kind,date_posted from supplier_payment'))
