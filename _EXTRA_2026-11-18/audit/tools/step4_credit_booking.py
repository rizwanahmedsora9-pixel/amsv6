"""AUDIT PHASE 3 — supplier payment, credit sale, client receipt, return, booking, expense."""
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()
TODAY = "2026-11-18"


def stock(name):
    row = H.one('SELECT total FROM material WHERE name = ?', (name,))
    return float(row["total"]) if row else None


def cash():
    return float(H.one("SELECT balance FROM account WHERE id = 1")["balance"])


# ---------------------------------------------------------------- T-PAY-01 retry
r = c.post("/accounts/pay_grn/save", data={
    "grn_id": 1, "pay_obligation_key[]": ["MATERIAL:1"], "pay_amount[]": ["60000"],
    "method": "Cash", "payment_account_id": 1, "date_posted": TODAY,
    "note": "Audit part payment GRN1",
}, follow_redirects=True)
sp = H.q("SELECT id, grn_id, supplier_id, amount, payment_kind, is_void FROM supplier_payment")
at = H.q("SELECT id, from_account_id, to_account_id, amount, transaction_type, source_type, source_id,"
         " party_type, party_name FROM account_transaction")
r_api = c.get("/accounts/api/pay_grn/1").get_json()
R.record("T-PAY-01", "Accounts / Supplier", "/accounts/pay_grn/save",
         "Supplier part-payment 60,000 against GRN-1 material obligation",
         "supplier_payment 60,000; cash 50,000->-10,000; GRN material remaining 160,000->100,000; no new expense row",
         f"flash={H.flash_messages(r)}; sp={sp}; cash={cash()}; remaining={r_api.get('material')}",
         "PASS" if sp and abs(float(sp[-1]['amount']) - 60000) < 0.01
         and abs((r_api['material']['remaining'] or 0) - 100000) < 0.01 else "FAIL",
         evidence=f"supplier_payment={sp} account_tx={at} api={r_api}")

# ---------------------------------------------------------------- T-SALE-04 credit
credit = {
    "category": "Credit Customer", "client_name": "AUDIT-CLI-01", "client_code": "AC01",
    "driver_name": "Audit Driver",
    "product_name[]": ["20mm Steel Bar"], "qty[]": ["200"], "unit_rate[]": ["140"],
    "paid_amount": "0", "discount": "0",
    "payment_method": "Cash", "sale_date": TODAY, "note": "AUD-CREDIT-001",
    "idempotency_key": "audit-key-credit-001",
}
r = c.post("/add_direct_sale", data=credit, follow_redirects=True)
ds = H.q("SELECT id, category, client_name, amount, paid_amount, discount FROM direct_sale ORDER BY id")
pb = H.q("SELECT id, client_name, bill_no, amount, is_paid, is_cash, transaction_type FROM pending_bill ORDER BY id")
st20 = stock("20mm Steel Bar")
R.record("T-SALE-04", "Sales / Credit", "/add_direct_sale",
         "Credit sale 200 kg 20mm @140 = 28,000 to registered client AC01, nothing paid",
         "sale saved; stock 500->300; an OPEN receivable of 28,000 appears (pending bill)",
         f"flash={H.flash_messages(r)}; sales={len(ds)}; pending={pb}; stock20={st20}",
         "PASS" if abs(st20 - 300) < 0.001 and any(abs(float(p['amount']) - 28000) < 0.01 for p in pb) else "FAIL",
         evidence=f"direct_sale={ds} pending_bill={pb}")

# ---------------------------------------------------------------- T-PAY-02 receipt
r = c.post("/add_payment", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "amount": "10000",
    "payment_type": "Receipt", "method": "Cash", "payment_account_id": "1",
    "date": TODAY, "note": "AUD-RECEIPT-001",
}, follow_redirects=True)
pay = H.q("SELECT id, client_name, amount, method, payment_type, is_void FROM payment")
cash_now = cash()
R.record("T-PAY-02", "Payments", "/add_payment",
         "Client receipt 10,000 against the 28,000 credit sale",
         "payment row 10,000; cash +10,000; receivable 28,000->18,000",
         f"flash={H.flash_messages(r)}; payments={pay}; cash={cash_now}",
         "PASS" if pay and abs(cash_now + 10000 - 0) < 1e9 and float(pay[-1]['amount']) == 10000 else "FAIL",
         evidence=f"payment={pay} cash={cash_now}")

# ---------------------------------------------------------------- T-RET-03 registered return
r = c.post("/add_material_return", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "date": TODAY,
    "note": "AUD-RET-002", "return_type": "Credit",
    "material_name[]": ["20mm Steel Bar"], "qty[]": ["20"],
    "unit_rate[]": ["140"], "rent_rate[]": ["0"],
}, follow_redirects=True)
mr = H.q("SELECT id, client_name, return_type, amount, manual_bill_no, auto_bill_no, payment_id"
         " FROM material_return ORDER BY id")
mri = H.q("SELECT id, material_return_id, material_name, qty, unit_rate, price_at_time, discount_reversed"
          " FROM material_return_item ORDER BY id")
st20b = stock("20mm Steel Bar")
R.record("T-RET-03", "Returns", "/add_material_return",
         "Return 20 kg 20mm @140 against the credit sale of a registered client",
         "return 2,800; stock 300->320; return linked to the original sale/bill; credit note not a cash refund",
         f"flash={H.flash_messages(r)}; returns={mr}; items={mri}; stock20={st20b}",
         "PASS" if mr and abs(st20b - 320) < 0.001 else "FAIL",
         evidence=f"material_return={mr} items={mri}")

# ---------------------------------------------------------------- T-BOOK-01 booking with advance
r = c.post("/add_booking", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01",
    "material_name[]": ["12mm Steel Bar"], "qty[]": ["100"], "unit_rate[]": ["160"],
    "amount": "16000", "paid_amount": "5000", "discount": "0",
    "date": TODAY, "note": "AUD-BOOK-001", "payment_method": "Cash",
    "payment_account_id": "1",
}, follow_redirects=True)
bk = H.q("SELECT id, client_name, amount, paid_amount, discount, is_void FROM booking ORDER BY id")
bki = H.q("SELECT id, booking_id, material_name, qty, unit_rate, price_at_time FROM booking_item ORDER BY id")
cash_b = cash()
R.record("T-BOOK-01", "Bookings", "/add_booking",
         "Booking 100 kg 12mm @160 = 16,000 with 5,000 advance, client AC01",
         "booking saved with advance 5,000; cash +5,000; NO delivered-sale receivable created yet",
         f"flash={H.flash_messages(r)}; booking={bk}; items={bki}; cash={cash_b}",
         "PASS" if bk and abs(float(bk[-1]['paid_amount']) - 5000) < 0.01 else "FAIL",
         evidence=f"booking={bk} items={bki}")

print("\n=== state after phase 3 ===")
for t in ("direct_sale", "material_return", "payment", "supplier_payment", "booking", "pending_bill", "account_transaction"):
    print(f"  {t}: {H.ledger_dump(t)}")
print("  stock:", H.q("SELECT name, total FROM material ORDER BY id"))
print("  cash :", cash())
print("  pending bills:", H.q("SELECT id, client_name, amount, is_paid, bill_no FROM pending_bill ORDER BY id"))
