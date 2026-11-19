"""AUDIT PHASE 4 — registered-client return, booking delivery, expense, then reports."""
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


# ---------------------------------------------------------------- T-RET-03 retry
r = c.post("/add_material_return", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "date": TODAY,
    "note": "AUD-RET-002", "return_type": "normal",
    "material_name[]": ["20mm Steel Bar"], "qty[]": ["20"],
    "unit_rate[]": ["140"], "rent_rate[]": ["0"],
}, follow_redirects=True)
mr = H.q("SELECT id, client_name, return_type, amount, manual_bill_no, auto_bill_no, payment_id"
         " FROM material_return ORDER BY id")
mri = H.q("SELECT id, material_return_id, material_name, qty, unit_rate, price_at_time, discount_reversed"
          " FROM material_return_item ORDER BY id")
st20 = stock("20mm Steel Bar")
cash_now = cash()
print("  returns:", mr, "items:", mri, "stock20:", st20, "cash:", cash_now)
R.record("T-RET-03", "Returns", "/add_material_return",
         "Return 20 kg 20mm @140 (Rs 2,800) against AC01's credit sale, type=normal",
         "return saved; stock 300->320; return linked to the client; credit note is not a cash refund (cash unchanged)",
         f"flash={H.flash_messages(r)}; returns={mr}; items={mri}; stock20={st20}; cash={cash_now}",
         "PASS" if mr and abs(st20 - 320) < 0.001 else "FAIL",
         evidence=f"material_return={mr} items={mri}")

# does the return reduce the client's receivable?
pb_after_return = H.q("SELECT id, client_name, bill_no, amount, is_paid FROM pending_bill ORDER BY id")
print("  pending bills after return:", pb_after_return)

# ---------------------------------------------------------------- T-BOOK-02 delivery
r = c.post("/add_direct_sale", data={
    "category": "Booking Delivery", "client_name": "AUDIT-CLI-01", "client_code": "AC01",
    "driver_name": "Audit Driver",
    "product_name[]": ["12mm Steel Bar"], "qty[]": ["100"], "unit_rate[]": ["0"],
    "paid_amount": "0", "discount": "0", "payment_method": "Cash",
    "sale_date": TODAY, "note": "AUD-BOOKDEL-001", "idempotency_key": "audit-key-bookdel",
}, follow_redirects=True)
ds = H.q("SELECT id, category, client_name, amount, paid_amount FROM direct_sale ORDER BY id")
ba = H.q("SELECT id, sale_id, sale_item_id, booking_item_id, qty, is_void FROM booking_allocation")
st12 = stock("12mm Steel Bar")
R.record("T-BOOK-02", "Bookings / Delivery", "/add_direct_sale (Booking Delivery)",
         "Deliver the 100 kg 12mm booking to AC01 (rate left 0 = booking price)",
         "booking allocation 100 rows created; stock 1150->1050; delivered value 16,000 becomes receivable; "
         "booking shows fully delivered",
         f"flash={H.flash_messages(r)}; sale={ds[-1] if ds else None}; alloc={ba}; stock12={st12}",
         "PASS" if ba and abs(st12 - 1050) < 0.001 else "FAIL",
         evidence=f"direct_sale={ds} booking_allocation={ba}")

# ---------------------------------------------------------------- T-EXP-01 expense
r = c.post("/accounts/expenditures", data={
    "category": "", "subcategory": "", "amount": "3000", "date": TODAY,
    "note": "AUDIT shop rent", "payment_account_id": "1", "method": "Cash",
    "recipient": "Landlord Audit", "description": "Audit shop rent",
}, follow_redirects=True)
print("\n  expense attempt:", H.brief(r))

print("\n=== state after phase 4 ===")
for t in ("direct_sale", "material_return", "payment", "supplier_payment", "booking",
          "booking_allocation", "pending_bill", "account_transaction"):
    print(f"  {t}: {H.ledger_dump(t)}")
print("  stock:", H.q("SELECT name, total FROM material ORDER BY id"))
print("  cash :", cash())
print("  pending:", H.q("SELECT id, client_name, bill_no, amount, is_paid, is_cash FROM pending_bill ORDER BY id"))
