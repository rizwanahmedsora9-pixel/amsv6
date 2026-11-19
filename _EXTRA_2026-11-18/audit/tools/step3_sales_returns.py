"""AUDIT PHASE 2 — cash sale, supplier payment, returns, oversell, duplicate submit."""
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()
TODAY = "2026-11-18"


def stock(name):
    row = H.one('SELECT total FROM material WHERE name = ?', (name,))
    return float(row["total"]) if row else None


def acct_balance(acc_id=1):
    row = H.one("SELECT balance FROM account WHERE id = ?", (acc_id,))
    return float(row["balance"]) if row else None


print("=== baseline ===")
print("stock 12mm:", stock("12mm Steel Bar"), "| cash account:", acct_balance())

# ---------------------------------------------------------------- T-PAY-01
# Supplier payment against GRN1 material obligation (Rs 60,000 of 160,000)
r = c.post("/accounts/pay_grn/save", data={
    "grn_id": 1, "pay_obligation_key[]": ["MATERIAL"], "pay_amount[]": ["60000"],
    "method": "Cash", "payment_account_id": 1, "date_posted": TODAY,
    "note": "Audit part payment GRN1",
}, follow_redirects=True)
sp = H.q("SELECT id, grn_id, supplier_id, amount, payment_kind, is_void FROM supplier_payment")
at = H.q("SELECT id, from_account_id, to_account_id, amount, transaction_type, source_type, source_id"
         " FROM account_transaction")
R.record("T-PAY-01", "Accounts / Supplier", "/accounts/pay_grn/save",
         "Supplier part-payment 60,000 against GRN-1 material obligation",
         "supplier_payment row 60,000; cash account 50,000-60,000; GRN payable 160,000->100,000; no extra expense row",
         f"flash={H.flash_messages(r)}; supplier_payment={sp}; account_tx={at}; cash={acct_balance()}",
         "PASS" if sp and abs(float(sp[0]['amount']) - 60000) < 0.01 and abs(acct_balance() - (-10000)) < 0.01 else "FAIL",
         evidence=f"supplier_payment={sp} account_transaction={at}")

# ---------------------------------------------------------------- T-SALE-01
# Cash sale: 300 kg 12mm @ 150, discount 500, fully paid
sale = {
    "category": "Cash", "client_name": "Cash Customer", "client_code": "",
    "driver_name": "Audit Driver",
    "product_name[]": ["12mm Steel Bar"], "qty[]": ["300"], "unit_rate[]": ["150"],
    "paid_amount": "44500", "discount": "500", "discount_reason": "Audit discount",
    "payment_method": "Cash", "payment_account_id": "1",
    "track_as_cash": "1", "sale_date": TODAY, "note": "AUD-SALE-001",
    "idempotency_key": "audit-key-001",
}
r = c.post("/add_direct_sale", data=sale, follow_redirects=True)
ds = H.q("SELECT id, category, amount, paid_amount, discount, client_name, auto_bill_no FROM direct_sale")
dil = H.q("SELECT id, sale_id, product_name, qty, price_at_time, cost_rate_at_sale, discount, discount_kind"
          " FROM direct_sale_item")
ent = H.q("SELECT id, type, material, client, qty, bill_no, client_category, transaction_category FROM entry")
pb = H.q("SELECT id, client_name, bill_no, amount, is_paid, is_cash FROM pending_bill")
s1 = ds[0] if ds else {}
st12 = stock("12mm Steel Bar")
R.record("T-SALE-01", "Sales / Cash", "/add_direct_sale",
         "Cash sale 300kg 12mm @150, discount 500, fully paid 44,500",
         "amount 45,000; paid 44,500; discount 500; stock 1400->1100; 1 OUT entry; no pending bill",
         f"flash={H.flash_messages(r)}; sale={s1}; item={dil}; entries={len(ent)}; pending={len(pb)}; stock12={st12}",
         "PASS" if (abs(float(s1.get('amount', 0)) - 45000) < 0.01
                    and abs(float(s1.get('discount', 0)) - 500) < 0.01
                    and abs(st12 - 1100) < 0.001 and len(pb) == 0) else "FAIL",
         evidence=f"direct_sale={ds} items={dil} entries={ent}")

# ---------------------------------------------------------------- T-SALE-02
# Duplicate submit with the SAME idempotency key (double click / retry)
before = H.scalar("SELECT COUNT(*) FROM direct_sale")
r2 = c.post("/add_direct_sale", data=sale, follow_redirects=True)
after = H.scalar("SELECT COUNT(*) FROM direct_sale")
R.record("T-SALE-02", "Sales / Cash", "/add_direct_sale",
         "Same sale re-posted with identical idempotency_key (double submit)",
         "second submission is ignored; direct_sale count unchanged",
         f"count {before}->{after}; flash={H.flash_messages(r2)}",
         "PASS" if before == after else "FAIL",
         evidence=f"flash={H.flash_messages(r2)}")

# ---------------------------------------------------------------- T-SALE-03
# Oversell: 10,000 kg of 12mm against 1,100 kg stock
over = dict(sale)
over.update({"qty[]": ["10000"], "unit_rate[]": ["150"], "paid_amount": "1500000",
             "idempotency_key": "audit-key-oversell", "note": "AUD-OVERSELL"})
r3 = c.post("/add_direct_sale", data=over, follow_redirects=True)
cnt3 = H.scalar("SELECT COUNT(*) FROM direct_sale")
st12b = stock("12mm Steel Bar")
R.record("T-SALE-03", "Sales / Cash", "/add_direct_sale",
         "Sell 10,000 kg against 1,100 kg on hand (no negative-stock flag)",
         "sale refused with an insufficient-stock message; sale count unchanged; stock unchanged",
         f"flash={H.flash_messages(r3)}; sales={cnt3}; stock={st12b}",
         "PASS" if cnt3 == before and abs(st12b - 1100) < 0.001 else "FAIL",
         evidence=f"flash={H.flash_messages(r3)}")

# ---------------------------------------------------------------- T-RET-01
print("\n=== returns ===")
ret = {
    "client_name": "Cash Customer", "date": TODAY, "note": "AUD-RET-001",
    "return_type": "Cash",
    "material_name[]": ["12mm Steel Bar"], "qty[]": ["50"],
    "unit_rate[]": ["150"], "rent_rate[]": ["0"],
}
r4 = c.post("/add_material_return", data=ret, follow_redirects=True)
mr = H.q("SELECT id, client_name, return_type, amount, manual_bill_no, auto_bill_no FROM material_return")
mri = H.q("SELECT id, material_return_id, material_name, qty, unit_rate, price_at_time, discount_reversed"
          " FROM material_return_item")
st12c = stock("12mm Steel Bar")
R.record("T-RET-01", "Returns", "/add_material_return",
         "Return 50 kg 12mm @150 against the cash sale",
         "return amount 7,500; stock 1100->1150; COGS reversal recorded; linked to the original bill",
         f"flash={H.flash_messages(r4)}; returns={mr}; items={mri}; stock={st12c}",
         "PASS" if mr and abs(st12c - 1150) < 0.001 else "FAIL",
         evidence=f"material_return={mr} items={mri}")

# ---------------------------------------------------------------- T-RET-02
# Over-return: 5,000 kg returned although only 300 kg were sold
ret2 = dict(ret)
ret2.update({"qty[]": ["5000"], "note": "AUD-RET-OVERRET"})
r5 = c.post("/add_material_return", data=ret2, follow_redirects=True)
st12d = stock("12mm Steel Bar")
mr_count = H.scalar("SELECT COUNT(*) FROM material_return")
R.record("T-RET-02", "Returns", "/add_material_return",
         "Return 5,000 kg when only 300 kg were ever sold to that customer",
         "refused (over-return / no matching sale) - stock must not grow by 5,000",
         f"flash={H.flash_messages(r5)}; returns={mr_count}; stock={st12d}",
         "PASS" if abs(st12d - 1150) < 0.001 else "FAIL",
         severity=None,
         evidence=f"flash={H.flash_messages(r5)}")

print("\n=== state after phase 2 ===")
for t in ("direct_sale", "direct_sale_item", "entry", "material_return", "material_return_item",
          "supplier_payment", "account_transaction", "pending_bill", "payment"):
    print(f"  {t}: {H.ledger_dump(t)}")
print("  stock:", H.q("SELECT name, total FROM material ORDER BY id"))
print("  cash:", acct_balance())
