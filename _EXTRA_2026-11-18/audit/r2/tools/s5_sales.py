"""S5 — cash/credit sales, discounts, receipts, overpayment, returns, edits, voids.

Order matters: the return is taken BEFORE the overpayment so every step has a real
open balance to work against.
"""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L

c = H.client()
T = L.TODAY
print("== S5 sales / receipts / returns ==")
M12, M20 = "R2 12mm Steel Bar", "R2 20mm Steel Bar"
CLI, CODE = "R2-CLI-02", "AC02"
_n = [0]


def sale(cat, mat, qty, rate, paid, disc="0", client=CLI, code=CODE, note="R2 sale"):
    _n[0] += 1
    return c.post("/add_direct_sale", data={
        "category": cat, "client_name": client, "client_code": code,
        "driver_name": "R2 Driver", "product_name[]": mat, "qty[]": str(qty),
        "unit_rate[]": str(rate), "paid_amount": str(paid), "discount": str(disc),
        "payment_method": "Cash", "payment_account_id": "1", "sale_date": T,
        "note": note, "idempotency_key": f"r2-sale-{_n[0]}-{note}"}, follow_redirects=True)


def due(client=CLI):
    return L.f(H.scalar("SELECT SUM(amount) FROM pending_bill WHERE client_name=? "
                        "AND COALESCE(is_void,0)=0 AND COALESCE(is_paid,0)=0", (client,)))


def client_net(code=CODE):
    with H.app.app_context():
        from app.services.financial_ledgers import build_client_financial_ledger, Client
        cl = Client.query.filter_by(code=code).first()
        return float(build_client_financial_ledger(cl).get("closing_balance") or 0)


# ------------------------------------------------------------------ S-01 cash sale
cash_b, s_b, due_b = L.cash(), L.stock(M12), due()
r = sale("Cash", M12, 100, 150, 14500, disc="500", client="R2 Walk-in", code="",
         note="R2 cash sale with discount")
cash_a, s_a, due_a = L.cash(), L.stock(M12), due()
ds = H.one("SELECT * FROM direct_sale ORDER BY id DESC LIMIT 1")
L.check("R2-S01", "Sales / Cash", "/add_direct_sale",
        "Cash sale 100 kg at 150 = 15,000 with a 500 discount, paid 14,500 over the counter",
        "stock -100, cash +14,500, nothing left as a receivable for that walk-in",
        f"stock {s_b}->{s_a}; cash {cash_b}->{cash_a}; walk-in due {due_b}->{due_a}; "
        f"sale={ {k: ds[k] for k in ('id','category','amount','paid_amount','discount')} }; "
        f"flash={H.flash_messages(r)}",
        abs((s_b - s_a) - 100) < 0.01 and abs((cash_a - cash_b) - 14500) < 0.01
        and abs(due_a - due_b) < 0.01, "HIGH")

# ------------------------------------------------------------------ S-02 underpaid cash sale
cash_b, due_b = L.cash(), due("R2 Walk-in")
r = sale("Cash", M12, 50, 150, 5000, client="R2 Walk-in", code="",
         note="R2 counter sale part paid")
cash_a, due_a = L.cash(), due("R2 Walk-in")
ds2 = H.one("SELECT * FROM direct_sale ORDER BY id DESC LIMIT 1")
pb2 = H.q("SELECT bill_no, amount, is_paid, is_void, is_cash FROM pending_bill "
          "WHERE source_table='direct_sale' AND source_id=?", (ds2["id"],))
L.check("R2-S02", "Sales / Cash (part paid)", "/add_direct_sale",
        "Counter sale of 7,500 with only 5,000 handed over",
        "the 2,500 balance becomes a real receivable and the operator is warned",
        f"cash {cash_b}->{cash_a}; walk-in due {due_b}->{due_a}; pending bill={pb2}; "
        f"flash={H.flash_messages(r)}",
        abs((due_a - due_b) - 2500) < 0.01 and bool(pb2), "HIGH")

# ------------------------------------------------------------------ S-03 credit sale
s_b, due_b, net_b = L.stock(M20), due(), client_net()
r = sale("Credit Customer", M20, 200, 160, 0, note="R2 credit sale AC02")
s_a, due_a, net_a = L.stock(M20), due(), client_net()
ds3 = H.one("SELECT * FROM direct_sale ORDER BY id DESC LIMIT 1")
pb3 = H.q("SELECT bill_no, amount, is_paid, is_void FROM pending_bill "
          "WHERE source_table='direct_sale' AND source_id=?", (ds3["id"],))
L.check("R2-S03", "Sales / Credit", "/add_direct_sale",
        "Credit sale to AC02: 200 kg of 20mm at 160 = 32,000 on account",
        "stock -200, AC02's due +32,000 and their ledger shows the bill",
        f"stock {s_b}->{s_a}; AC02 due {due_b}->{due_a}; client net {net_b}->{net_a}; pending bill={pb3}",
        abs((s_b - s_a) - 200) < 0.01 and abs((due_a - due_b) - 32000) < 0.01
        and abs((net_a - net_b) - 32000) < 0.01, "HIGH")

# ------------------------------------------------------------------ S-04 receipt
cash_b, due_b = L.cash(), due()
r = c.post("/add_payment", data={
    "client_code": CODE, "client_name": CLI, "amount": "10000", "payment_type": "Receipt",
    "method": "Cash", "payment_account_id": "1", "date": T, "note": "R2 receipt against credit sale"},
    follow_redirects=True)
cash_a, due_a = L.cash(), due()
L.check("R2-S04", "Payments / Receipt", "/add_payment",
        "Take 10,000 from AC02 against the 32,000 bill",
        "cash +10,000 and AC02's due drops by exactly 10,000",
        f"cash {cash_b}->{cash_a}; AC02 due {due_b}->{due_a}; flash={H.flash_messages(r)}",
        abs((cash_a - cash_b) - 10000) < 0.01 and abs((due_b - due_a) - 10000) < 0.01, "HIGH")

# ------------------------------------------------------------------ S-05 return before overpayment
s_b, due_b = L.stock(M20), due()
r = c.post("/add_material_return", data={
    "bill_no": (pb3[0]["bill_no"] if pb3 else ""), "client_code": CODE, "client_name": CLI,
    "material_name[]": M20, "qty[]": "20", "unit_rate[]": "160", "amount": "3200",
    "date": T, "note": "R2 return 20 kg against the credit bill", "refund_method": "Credit Note",
}, follow_redirects=True)
s_a, due_a = L.stock(M20), due()
ret = H.one("SELECT * FROM material_return ORDER BY id DESC LIMIT 1")
L.check("R2-S05", "Returns / Credit sale", "/add_material_return",
        "Return 20 kg of the 20mm sold on credit (3,200 at the sale rate)",
        "stock +20, AC02's due falls by 3,200, and the return is linked to the sale it came from",
        f"stock {s_b}->{s_a}; AC02 due {due_b}->{due_a}; "
        f"return={ {k: ret[k] for k in ('id','amount','source_sale_id','source_bill_no')} }; "
        f"lines={H.q('SELECT material_name, qty, unit_rate FROM material_return_item WHERE material_return_id=?', (ret['id'],))}; "
        f"flash={H.flash_messages(r)}",
        abs((s_a - s_b) - 20) < 0.01 and abs((due_b - due_a) - 3200) < 0.01
        and ret["source_sale_id"] == ds3["id"], "HIGH")

# ------------------------------------------------------------------ S-06 over-return
s_b, due_b = L.stock(M20), due()
r = c.post("/add_material_return", data={
    "bill_no": (pb3[0]["bill_no"] if pb3 else ""), "client_code": CODE, "client_name": CLI,
    "material_name[]": M20, "qty[]": "200", "unit_rate[]": "160", "amount": "32000",
    "date": T, "note": "R2 attempt to return more than sold", "refund_method": "Credit Note",
}, follow_redirects=True)
s_a, due_a = L.stock(M20), due()
ret2 = H.one("SELECT * FROM material_return ORDER BY id DESC LIMIT 1")
L.check("R2-S06", "Returns / Over-return", "/add_material_return",
        "Return 200 kg against a bill with only 180 kg still returnable",
        "refused — a return can never exceed what is left of the bill",
        f"stock {s_b}->{s_a}; AC02 due {due_b}->{due_a}; last return id={ret2['id']}; "
        f"flash={H.flash_messages(r)}",
        abs(s_a - s_b) < 0.01, "HIGH")

# ------------------------------------------------------------------ S-07 overpayment
cash_b, due_b, net_b = L.cash(), due(), client_net()
r = c.post("/add_payment", data={
    "client_code": CODE, "client_name": CLI, "amount": "30000", "payment_type": "Receipt",
    "method": "Cash", "payment_account_id": "1", "date": T, "note": "R2 overpayment"},
    follow_redirects=True)
cash_a, due_a, net_a = L.cash(), due(), client_net()
L.check("R2-S07", "Payments / Overpayment", "/add_payment",
        "AC02 owes 18,800 but hands over 30,000",
        "their bills are cleared and the 11,200 excess is carried as a client advance (credit balance), "
        "never booked as income",
        f"cash {cash_b}->{cash_a}; AC02 due {due_b}->{due_a}; client net {net_b}->{net_a}; "
        f"flash={H.flash_messages(r)}",
        abs(due_a) < 0.01 and abs((net_a - net_b) + 30000) < 0.01, "HIGH")

# ------------------------------------------------------------------ S-08 edit a part-paid cash bill
due_b, s_b = due("R2 Walk-in"), L.stock(M12)
r = c.post(f"/edit_bill/DirectSale/{ds2['id']}", data={
    "category": "Cash", "client_name": "R2 Walk-in", "client_code": "",
    "product_name[]": M12, "qty[]": "50", "unit_rate[]": "160",
    "discount": "0", "paid_amount": "5000", "payment_method": "Cash",
    "payment_account_id": "1", "sale_date": T, "note": "R2 counter sale repriced to 160"},
    follow_redirects=True)
ds2b = H.one("SELECT * FROM direct_sale WHERE id=?", (ds2["id"],))
due_a, s_a = due("R2 Walk-in"), L.stock(M12)
L.check("R2-S08", "Sales / Edit", f"/edit_bill/DirectSale/{ds2['id']}",
        "Re-price the part-paid counter sale from 150 to 160 (7,500 -> 8,000)",
        "bill becomes 8,000, the 5,000 already paid stays, the balance becomes 3,000 and stock does not move",
        f"bill amount 7500->{ds2b['amount']} paid={ds2b['paid_amount']}; walk-in due {due_b}->{due_a}; "
        f"stock {s_b}->{s_a}; flash={H.flash_messages(r)}",
        abs(float(ds2b["amount"]) - 8000) < 0.01 and abs(due_a - 3000) < 0.01
        and abs(s_a - s_b) < 0.01, "HIGH")

# ------------------------------------------------------------------ S-09 void a sale that has a return
stock_b, net_b = L.stock(M20), client_net()
paid_to_us = L.f(H.scalar("SELECT SUM(amount) FROM payment WHERE client_name=? AND COALESCE(is_void,0)=0 "
                          "AND payment_type='Receipt'", (CLI,)))
r = c.post(f"/void_transaction/DirectSale/{ds3['id']}", data={"reason": "client cancelled the order"},
           follow_redirects=True)
ds3b = H.one("SELECT id, is_void, amount FROM direct_sale WHERE id=?", (ds3["id"],))
stock_a, net_a = L.stock(M20), client_net()
entries = H.q("SELECT id, type, qty, parent_id, is_void, source_table, source_id FROM entry "
              "WHERE material=? ORDER BY id", (M20,)) if 'parent_id' in [x['name'] for x in
                                                                       H.q("PRAGMA table_info(entry)")] else \
    H.q("SELECT id, type, qty, is_void, source_table, source_id FROM entry WHERE material=? ORDER BY id", (M20,))
# goods physics: 500 in, 200 delivered, 20 came back, invoice voided -> 500 must be on hand
L.check("R2-S09", "Sales / Void (+ prior return)", f"/void_transaction/DirectSale/{ds3['id']}",
        "Void the 32,000 credit sale that already has a 20 kg return booked against it",
        "stock returns to the physical 500 kg (the 200 delivered came back, the 20 already returned is not "
        "counted twice) and the client is owed back the 35,000 they actually paid",
        f"sale={ds3b}; stock 20mm {stock_b}->{stock_a} (physical expectation 500); "
        f"client paid {paid_to_us}; client net {net_b}->{net_a} (expecting -35000 after the 5,000 opening); "
        f"entries={entries}; returns={H.q('SELECT id, amount, is_void, source_sale_id FROM material_return')}; "
        f"flash={H.flash_messages(r)}",
        ds3b["is_void"] == 1 and abs(stock_a - 500) < 0.01
        and abs(net_a - (5000 - paid_to_us)) < 0.01, "HIGH",
        evidence="Every delivered kg must come back exactly once: the voided OUT entry restores 200 and the "
                 "return entry already restored 20, so 520 on hand means 20 kg of phantom stock and a "
                 "3,200 client credit that was never reversed.")

print("S5 done")
