"""AUDIT PHASE 8 — over-return, return-to-bill linkage, void reversal, duplicate payment."""
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()
TODAY = "2026-11-18"


def stock(n):
    r = H.one("SELECT total FROM material WHERE name = ?", (n,))
    return float(r["total"]) if r else None


def cash():
    return float(H.one("SELECT balance FROM account WHERE id = 1")["balance"])


# ---------------------------------------------------------------- T-RET-04 over-return (registered client)
before = stock("20mm Steel Bar")
r = c.post("/add_material_return", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "date": TODAY,
    "note": "AUD-OVERRETURN-500", "return_type": "normal",
    "material_name[]": ["20mm Steel Bar"], "qty[]": ["500"],
    "unit_rate[]": ["140"], "rent_rate[]": ["0"],
}, follow_redirects=True)
after = stock("20mm Steel Bar")
returns = H.q("SELECT id, client_name, amount, note FROM material_return ORDER BY id")
R.record("T-RET-04", "Returns", "/add_material_return",
         "Return 500 kg 20mm to AC01 who only ever bought 200 kg (net 180 kg after a 20 kg return)",
         "refused — cannot return more than was sold to that client; stock stays 320",
         f"flash={H.flash_messages(r)}; stock {before}->{after}; returns={returns}",
         "PASS" if abs(after - before) < 0.001 else "FAIL",
         severity=None if abs(after - before) < 0.001 else "CRITICAL",
         evidence=f"material_return rows={returns}")

# ---------------------------------------------------------------- T-RET-05 which bill does a credit hit?
# second credit sale for the same client, then a small return
r = c.post("/add_direct_sale", data={
    "category": "Credit Customer", "client_name": "AUDIT-CLI-01", "client_code": "AC01",
    "driver_name": "Audit Driver",
    "product_name[]": ["20mm Steel Bar"], "qty[]": ["50"], "unit_rate[]": ["145"],
    "paid_amount": "0", "discount": "0", "payment_method": "Cash",
    "sale_date": TODAY, "note": "AUD-CREDIT-002", "idempotency_key": "audit-key-credit-002",
}, follow_redirects=True)
sales2 = H.q("SELECT id, auto_bill_no, amount FROM direct_sale ORDER BY id")
bills_before = H.q("SELECT id, bill_no, amount, source_id FROM pending_bill ORDER BY id")

r = c.post("/add_material_return", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "date": TODAY,
    "note": "AUD-RET-003", "return_type": "normal",
    "material_name[]": ["20mm Steel Bar"], "qty[]": ["10"],
    "unit_rate[]": ["145"], "rent_rate[]": ["0"],
}, follow_redirects=True)
bills_after = H.q("SELECT id, bill_no, amount, source_id FROM pending_bill ORDER BY id")
mr = H.q("SELECT id, client_name, amount, note FROM material_return ORDER BY id")
print("\nbills before return 1,450:", bills_before)
print("bills after  return 1,450:", bills_after)
print("returns:", mr)

R.record("T-RET-05", "Returns / Receivable", "/add_material_return",
         "Second credit sale (7,250) then a 1,450 return — which bill absorbs the credit?",
         "the return is linked to the bill it returns against (or the allocation is documented), "
         "and total receivable drops by exactly 1,450",
         f"before={[ (b['bill_no'], b['amount']) for b in bills_before ]} "
         f"after={[ (b['bill_no'], b['amount']) for b in bills_after ]}",
         "INFO", evidence=f"material_return={mr}")

# ---------------------------------------------------------------- T-VOID-01 void a credit sale
sale_to_void = [s for s in sales2 if s['note'] != 'AUD-SALE-001'][-1] if False else sales2[-1]
stock_before = stock("20mm Steel Bar")
r = c.post(f"/void_transaction/DirectSale/{sale_to_void['id']}", data={"reason": "audit void test"},
           follow_redirects=True)
voided = H.q("SELECT id, is_void, amount, auto_bill_no FROM direct_sale WHERE id = ?", (sale_to_void['id'],))
stock_after = stock("20mm Steel Bar")
pb = H.q("SELECT id, bill_no, amount, is_paid FROM pending_bill WHERE source_table='direct_sale' AND source_id=?",
         (sale_to_void['id'],))
print("\nvoid attempt:", H.flash_messages(r), "| sale:", voided, "| stock:", stock_before, "->", stock_after, "| bill:", pb)
R.record("T-VOID-01", "Sales / Void", f"/void_transaction/DirectSale/{sale_to_void['id']}",
         "Void a credit sale (50 kg 20mm, Rs 7,250) through the void route",
         "sale flagged void; stock returns by 50 kg; its receivable/pending bill is cleared; "
         "cash and profit reports exclude it",
         f"flash={H.flash_messages(r)}; sale={voided}; stock {stock_before}->{stock_after}; pending_bill={pb}",
         "PASS" if voided and voided[0]['is_void'] == 1 and abs((stock_after or 0) - (stock_before + 50)) < 0.001
         else "FAIL", severity="HIGH")

# ---------------------------------------------------------------- T-PAY-03 duplicate payment
prev = H.scalar("SELECT COUNT(*) FROM payment")
payload = {"client_code": "AC01", "client_name": "AUDIT-CLI-01", "amount": "777",
           "payment_type": "Receipt", "method": "Cash", "payment_account_id": "1",
           "date": TODAY, "note": "AUD-DUPE-PAY", "idempotency_key": "audit-pay-dupe"}
c.post("/add_payment", data=payload, follow_redirects=True)
c.post("/add_payment", data=payload, follow_redirects=True)
now = H.scalar("SELECT COUNT(*) FROM payment")
dupes = H.q("SELECT id, amount, note FROM payment WHERE note='AUD-DUPE-PAY'")
R.record("T-PAY-03", "Payments", "/add_payment",
         "Same receipt posted twice with the same idempotency_key",
         "second post ignored (one payment row)",
         f"payment rows {prev}->{now}; rows with that note={dupes}",
         "PASS" if len(dupes) == 1 else "FAIL", severity="HIGH" if len(dupes) > 1 else None)

print("\n=== final ledger state ===")
print("stock:", H.q("SELECT name, total FROM material ORDER BY id"))
print("cash:", cash())
print("payments:", H.q("SELECT COUNT(*) n, SUM(amount) s FROM payment"))
print("pending:", H.q("SELECT bill_no, amount, is_paid FROM pending_bill ORDER BY id"))
