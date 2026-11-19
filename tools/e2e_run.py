"""MASTER ERP END-TO-END BUSINESS TRANSACTION TEST — driver.

Runs the owner's Part A–P transaction script through the REAL Flask app over
HTTP, exactly as the browser posts each screen.  Every result is captured
(flash text, HTTP status, redirect) and later verified against the app's own
reports.  No database writes happen in this file.

Period: 1 Nov 2026 – 15 Nov 2026   (app "today" = 18 Nov 2026, so no future dates)
Opening balances / stock: zero.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime

sys.path.insert(0, '/home/user/AMS/tools')
from e2e_client import Client, db_rows  # noqa: E402

RESULTS: list[dict] = []


def rec(part, label, res, expected='', note=''):
    item = {
        'part': part, 'label': label, 'path': res.get('path'),
        'status': res.get('status'), 'location': res.get('location'),
        'flashes': res.get('flashes') or [], 'ok': bool(res.get('ok')),
        'error': res.get('error'), 'expected': expected, 'note': note,
        'at': datetime.now().isoformat(timespec='seconds'),
    }
    RESULTS.append(item)
    flag = 'OK  ' if item['ok'] else 'FAIL'
    print(f"[{flag}] {part:3s} | {label:44s} | {item['status']} -> {str(item['location'])[:70]}")
    for kind, text in item['flashes']:
        print(f"           ({kind}) {text[:300]}")
    return item


def d(s):
    """Date -> the app's datetime-local / date input format."""
    return s


# ---------------------------------------------------------------------------
# PART A — master records
# ---------------------------------------------------------------------------
def part_a(c):
    rec('A', 'Client Asif (03000000001)', c.post('/add_client', [
        ('name', 'Asif'), ('code', 'ASIF-001'), ('category', 'General'),
        ('phone', '03000000001'), ('address', 'Islamabad'),
        ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
        ('page_entry', 'bookings'),
    ]), 'client exists, opening balance 0')

    rec('A', 'Client Bilal (03000000002)', c.post('/add_client', [
        ('name', 'Bilal'), ('code', 'BILAL-002'), ('category', 'General'),
        ('phone', '03000000002'), ('address', 'Islamabad'),
        ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
        ('page_entry', 'bookings'),
    ]), 'client exists, opening balance 0')

    rec('A', 'Supplier Nadeem (03000000003)', c.post('/add_supplier', [
        ('name', 'Nadeem'), ('phone', '03000000003'), ('address', 'Islamabad'),
        ('opening_balance', '0'), ('opening_balance_date', '2026-11-01'),
    ]), 'supplier exists, opening balance 0')

    for name, code, unit in (('Cement Bag', 'CEM-01', 'Bag'),
                             ('12mm Steel', 'STL-12', 'KG'),
                             ('20mm Steel', 'STL-20', 'KG')):
        rec('A', f'Material {name} / {unit}', c.post('/add_material', [
            ('material_name', name), ('material_code', code),
            ('category_id', '1'), ('material_unit', unit),
        ]), f'{name} unit={unit}')

    # The cash account every payment screen posts against (openings zero).
    rec('A', 'Account: Main Cash (opening 0)', c.post('/accounts/accounts/add', [
        ('name', 'Main Cash'), ('class_category', 'Assets'),
        ('class_subcategory', 'Cash'), ('class_account_type', 'Main Cash'),
        ('channel', 'cash'), ('account_status', 'active'),
        ('opening_amount', '0'), ('opening_position', 'debit'),
        ('opening_effective_date', '2026-11-01'),
        ('cash_location', 'Shop'), ('note', 'Master e2e test cash account'),
    ]), 'cash account, balance 0')


# ---------------------------------------------------------------------------
# PART B — GRN 1 (1 Nov): 100 bags @1200 = 120,000
#          freight 3,000 PAID + loading 1,000 UNPAID = 124,000
# ---------------------------------------------------------------------------
def part_b(c):
    rows = [
        ('GRN', '1', '1'),          # item grid row count marker
    ]
    rec('B', 'GRN 1 (100 bags @1200 + freight 3000 PAID + loading 1000 UNPAID)',
        c.post('/grn', [
            ('action', 'add'),
            ('supplier', 'Nadeem'), ('supplier_id', '1'),
            ('date', '2026-11-01'), ('time', '09:00'),
            ('item_grid_present', '1'),
            ('grn_item_id[]', ''), ('mat_name[]', 'Cement Bag'),
            ('qty[]', '100'), ('price[]', '1200'),
            # legacy scalar mirrors of the two charges
            ('loading_cost', '1000'), ('freight_cost', '3000'), ('other_expense', '0'),
            # charge grid: freight PAID (owner rule says the grid has no paid
            # field — we post the cost fact and record the payment separately)
            ('charge_grid_present', '1'),
            ('charge_supplier_billing_present', '1'),
            ('charge_id[]', ''), ('charge_type[]', 'VEHICLE_FARE'),
            ('charge_label[]', 'Freight to Islamabad'), ('charge_amount[]', '3000'),
            ('charge_row_key[]', 'row-freight'),
            ('charge_recipient_type[]', 'TRANSPORTER'),
            ('charge_recipient[]', 'Rana Transport'),
            ('charge_treatment[]', 'INVENTORY'), ('charge_basis[]', 'VALUE'),
            ('charge_id[]', ''), ('charge_type[]', 'LOADING'),
            ('charge_label[]', 'Loading at plant'), ('charge_amount[]', '1000'),
            ('charge_row_key[]', 'row-loading'),
            ('charge_recipient_type[]', 'LABOUR'),
            ('charge_recipient[]', 'Loading crew'),
            ('charge_treatment[]', 'INVENTORY'), ('charge_basis[]', 'VALUE'),
            ('allocation_basis_default', 'VALUE'),
            ('supplier_invoice_no', 'NAD-INV-001'),
            ('bill_date', '2026-11-01'), ('due_date', '2026-11-15'),
            ('note', 'GRN 1 — 100 bags cement'),
        ]), 'GRN total cost 124,000; stock 100 bags')


# ---------------------------------------------------------------------------
# PART C — supplier payment 50,000 cash (2 Nov)
# ---------------------------------------------------------------------------
def part_c(c):
    rec('C', 'Supplier payment 50,000 cash (2 Nov)', c.post('/add_supplier_payment', [
        ('supplier_id', '1'), ('amount', '50000'), ('date', '2026-11-02'),
        ('method', 'Cash'), ('payment_account_id', '1'),
        ('note', 'Part payment against GRN-1'),
        ('idempotency_key', 'e2e-C-50000'),
        ('return_to', '/suppliers'),
    ]), 'cash -50,000; supplier payable -50,000')


# ---------------------------------------------------------------------------
# PART D — booking Asif 50 bags @1500 = 75,000, advance 20,000 cash (3 Nov)
# ---------------------------------------------------------------------------
def part_d(c):
    rec('D', 'Booking Asif 50 bags @1500, advance 20,000 (3 Nov)', c.post('/add_booking', [
        ('client_code', 'ASIF-001'), ('client', 'Asif'),
        ('material_id[]', '1'), ('material_name[]', 'Cement Bag'),
        ('qty[]', '50'), ('unit_rate[]', '1500'), ('service_id[]', ''),
        ('amount', '75000'), ('paid_amount', '20000'),
        ('payment_method', 'Cash'), ('payment_account_id', '1'),
        ('date', '2026-11-03T09:00'), ('discount', '0'), ('discount_reason', ''),
        ('note', 'Booking 50 bags @1500 (e2e)'),
    ]), 'booking 75,000; advance 20,000 cash in')


# ---------------------------------------------------------------------------
# PART E — delivery of 30 bags against that booking (5 Nov)
# ---------------------------------------------------------------------------
def part_e(c):
    rec('E', 'Delivery 30 bags against booking (5 Nov)', c.post('/add_record', [
        ('type', 'OUT'), ('client', 'Asif'),
        ('material_id', '1'), ('material', 'Cement Bag'),
        ('qty', '30'), ('date', '2026-11-05'),
        ('driver_name', 'Rana Transport'),
        ('note', 'Delivery against booking of 3 Nov'),
        ('has_bill', '1'), ('create_invoice', '1'),
        ('track_as_cash', ''),
    ]), '30 bags leave stock; Asif owes 30x1500=45,000 less advance')


# ---------------------------------------------------------------------------
# PART F — material return 2 bags @1500 (6 Nov)
# ---------------------------------------------------------------------------
def part_f(c):
    rec('F', 'Material return 2 bags @1500 (6 Nov)', c.post('/add_material_return', [
        ('client_code', 'ASIF-001'), ('client_name', 'Asif'),
        ('date', '2026-11-06'), ('return_type', 'normal'),
        ('material_id[]', '1'), ('material_name[]', 'Cement Bag'),
        ('qty[]', '2'), ('unit_rate[]', '1500'), ('rent_rate[]', '0'),
        ('service_id[]', ''),
        ('note', '2 bags returned from the 5 Nov delivery'),
    ]), 'stock +2, client credit 3,000')


# ---------------------------------------------------------------------------
# PART G — client payment 24,000 (7 Nov)
# ---------------------------------------------------------------------------
def part_g(c):
    rec('G', 'Client payment 24,000 cash (7 Nov)', c.post('/add_payment', [
        ('client_code', 'ASIF-001'), ('client_name', 'Asif'),
        ('amount', '24000'), ('discount', '0'), ('discount_reason', ''),
        ('payment_type', 'Receipt'), ('method', 'Cash'),
        ('payment_account_id', '1'), ('date', '2026-11-07'),
        ('note', 'Receipt from Asif'), ('idempotency_key', 'e2e-G-24000'),
    ]), 'cash +24,000; Asif receivable -24,000')


# ---------------------------------------------------------------------------
# PART H — refund 2,000 to Asif (8 Nov)
# ---------------------------------------------------------------------------
def part_h(c):
    rec('H', 'Refund 2,000 to Asif (8 Nov)', c.post('/add_payment', [
        ('client_code', 'ASIF-001'), ('client_name', 'Asif'),
        ('amount', '2000'), ('discount', '0'), ('discount_reason', ''),
        ('payment_type', 'Refund'), ('method', 'Cash'),
        ('payment_account_id', '1'), ('date', '2026-11-08'),
        ('note', 'Refund to Asif'), ('idempotency_key', 'e2e-H-2000'),
    ]), 'cash -2,000; client receivable +2,000')


# ---------------------------------------------------------------------------
# PART I — cash sale 10 bags @1600 = 16,000 less 1,000 discount = 15,000 paid
# ---------------------------------------------------------------------------
def part_i(c):
    rec('I', 'Cash direct sale 10 bags @1600 - 1000 disc = 15,000 paid (9 Nov)',
        c.post('/add_direct_sale', [
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
            ('idempotency_key', 'e2e-I-cashsale'),
        ]), 'revenue 15,000; stock -10; cash +15,000')


# ---------------------------------------------------------------------------
# PART J — credit sale to Bilal, 20 bags @1550 = 31,000 unpaid (10 Nov)
# ---------------------------------------------------------------------------
def part_j(c):
    rec('J', 'Credit sale Bilal 20 bags @1550 = 31,000 unpaid (10 Nov)',
        c.post('/add_direct_sale', [
            ('client_code', 'BILAL-002'), ('client_name', 'Bilal'),
            ('client', 'Bilal'),
            ('sale_date', '2026-11-10'),
            ('material_id[]', '1'), ('product_name[]', 'Cement Bag'),
            ('qty[]', '20'), ('unit_rate[]', '1550'), ('service_id[]', ''),
            ('amount', '31000'), ('discount', '0'), ('discount_reason', ''),
            ('paid_amount', '0'), ('payment_method', 'Cash'),
            ('payment_account_id', ''),
            ('has_bill', '1'), ('create_invoice', '1'),
            ('delivery_person_id[]', '1'), ('delivery_bags[]', '20'),
            ('delivery_rent_amount[]', '0'),
            ('note', 'Credit sale to Bilal'),
            ('idempotency_key', 'e2e-J-credit'),
        ]), 'revenue 31,000; Bilal owes 31,000; stock -20')


# ---------------------------------------------------------------------------
# PART K — payment 15,000 from Bilal (11 Nov)
# ---------------------------------------------------------------------------
def part_k(c):
    rec('K', 'Bilal payment 15,000 cash (11 Nov)', c.post('/add_payment', [
        ('client_code', 'BILAL-002'), ('client_name', 'Bilal'),
        ('amount', '15000'), ('discount', '0'), ('discount_reason', ''),
        ('payment_type', 'Receipt'), ('method', 'Cash'),
        ('payment_account_id', '1'), ('date', '2026-11-11'),
        ('note', 'Part receipt from Bilal'), ('idempotency_key', 'e2e-K-15000'),
    ]), 'cash +15,000; Bilal 31,000-15,000=16,000')


# ---------------------------------------------------------------------------
# PART L — expenses: rent 2,000 (11 Nov), electricity 500 (12), vehicle 400 (12)
# ---------------------------------------------------------------------------
def part_l(c):
    expenses = [
        ('Rent', 'Shop rent for November', '2000', '2026-11-11'),
        ('Utilities', 'Electricity bill', '500', '2026-11-12'),
        ('Vehicle', 'Vehicle running / fuel', '400', '2026-11-12'),
    ]
    for category, label, amount, date in expenses:
        rec('L', f'Expense {label} {amount} ({date})', c.post('/cash_flow', [
            ('action', 'record_movement'), ('direction', 'out'),
            ('amount', amount), ('cash_account_id', '1'),
            ('category_name', category), ('party_name', 'Landlord / WAPDA / Fuel'),
            ('party_type', 'other'), ('description', label),
            ('movement_note', label), ('movement_date', date),
            ('idempotency_key', f'e2e-L-{category}-{amount}'),
        ]), f'opex {amount}; cash -{amount}')


# ---------------------------------------------------------------------------
# PART M — extra discount 500 to Bilal against the sale (13 Nov)
# ---------------------------------------------------------------------------
def part_m(c):
    rec('M', 'Extra discount 500 to Bilal (13 Nov)', c.post('/add_payment', [
        ('client_code', 'BILAL-002'), ('client_name', 'Bilal'),
        ('amount', '0'), ('discount', '500'),
        ('discount_reason', 'Goodwill discount on the 10 Nov sale'),
        ('payment_type', 'Waive-Off'), ('method', 'Cash'),
        ('payment_account_id', '1'), ('date', '2026-11-13'),
        ('note', 'Extra discount 500'), ('idempotency_key', 'e2e-M-500'),
    ]), 'Bilal 16,000-500=15,500; discount 500')


# ---------------------------------------------------------------------------
# PART N — GRN 2 (14 Nov): 50 bags @1300 = 65,000 + freight 2,000 UNPAID
# ---------------------------------------------------------------------------
def part_n(c):
    rec('N', 'GRN 2 (50 bags @1300 + freight 2000 unpaid, 14 Nov)', c.post('/grn', [
        ('action', 'add'),
        ('supplier', 'Nadeem'), ('supplier_id', '1'),
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
    ]), 'GRN 2 total 67,000; stock +50')


# ---------------------------------------------------------------------------
# PART O — supplier payment 30,000 (15 Nov)
# ---------------------------------------------------------------------------
def part_o(c):
    rec('O', 'Supplier payment 30,000 (15 Nov)', c.post('/add_supplier_payment', [
        ('supplier_id', '1'), ('amount', '30000'), ('date', '2026-11-15'),
        ('method', 'Cash'), ('payment_account_id', '1'),
        ('note', 'Payment against GRN-2'),
        ('idempotency_key', 'e2e-O-30000'), ('return_to', '/suppliers'),
    ]), 'cash -30,000; supplier -30,000')


# ---------------------------------------------------------------------------
# PART P — owner withdrawal 5,000 (15 Nov)
# ---------------------------------------------------------------------------
def part_p(c):
    rec('P', 'Owner withdrawal 5,000 (15 Nov)', c.post('/cash_flow', [
        ('action', 'record_movement'), ('direction', 'out'),
        ('amount', '5000'), ('cash_account_id', '1'),
        ('category_name', 'Owner Drawings'), ('party_name', 'Owner'),
        ('party_type', 'partner'), ('description', 'Owner withdrawal'),
        ('movement_note', 'Owner withdrawal'), ('movement_date', '2026-11-15'),
        ('idempotency_key', 'e2e-P-drawing'),
    ]), 'cash -5,000, NOT an expense (drawings)')


PARTS = [part_a, part_b, part_c, part_d, part_e, part_f, part_g, part_h,
         part_i, part_j, part_k, part_l, part_m, part_n, part_o, part_p]


def main():
    c = Client()
    s, _ = c.get('/login')
    r = c.post('/login', [('username', 'Admin'), ('password', 'Admin@fbm12345')])
    print('login ->', r['status'], r['flashes'])

    only = sys.argv[1:] or None
    for fn in PARTS:
        name = fn.__name__
        if only and not any(o.lower() in name for o in only):
            continue
        print(f'\n=== {name.upper()} ===')
        try:
            fn(c)
        except Exception:
            traceback.print_exc()
            RESULTS.append({'part': name, 'label': 'EXCEPTION',
                            'error': traceback.format_exc()[-800:], 'ok': False})

    with open('/home/user/e2e_results.json', 'w') as fh:
        json.dump(RESULTS, fh, indent=2)
    ok = sum(1 for r in RESULTS if r['ok'])
    print(f'\n===== {ok}/{len(RESULTS)} steps saved successfully =====')
    for r in RESULTS:
        if not r['ok']:
            print(f"  REFUSED: {r['part']} {r['label']}: {r['error']}")


if __name__ == '__main__':
    main()
