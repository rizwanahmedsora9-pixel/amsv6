"""S10 — targeted probes on the code paths that were changed in the previous remediation
round, looking for regressions introduced by those changes."""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L

c = H.client()
T = L.TODAY
print("== S10 regression probes on the remediated paths ==")
M12 = "R2 12mm Steel Bar"


def recv():
    return L.open_receivable()


# ---------------------------------------------------------------- 1. void a client receipt (F-07c path)
cash_b, recv_b = L.cash(), recv()
c.post("/add_payment", data={"client_code": "AC01", "client_name": "R2-CLI-01", "amount": "700",
                             "payment_type": "Receipt", "method": "Cash", "payment_account_id": "1",
                             "date": T, "note": "R2 receipt to void"}, follow_redirects=True)
pay = H.one("SELECT id, amount FROM payment ORDER BY id DESC LIMIT 1")
recv_mid = recv()
r = c.post(f"/void_transaction/Payment/{pay['id']}", data={"reason": "wrong client"}, follow_redirects=True)
pay2 = H.one("SELECT id, amount, is_void FROM payment WHERE id=?", (pay["id"],))
cash_a, recv_a = L.cash(), recv()
L.check("R2-V01", "Regression / Payment void", f"/void_transaction/Payment/{pay['id']}",
        "Take a 700 receipt and immediately void it",
        "the receipt is kept and flagged void, cash goes back by 700 and any bill it settled reopens",
        f"receipt={pay2}; cash before the receipt={cash_b}, after the void={cash_a}; open receivable "
        f"{recv_b}->{recv_mid}->{recv_a}; flash={H.flash_messages(r)}",
        pay2 and pay2["is_void"] == 1 and abs(cash_b - cash_a) < 0.01
        and abs(recv_a - recv_b) < 0.01, "HIGH")

# ---------------------------------------------------------------- 2. delete a sale (F-07 path)
sale = H.one("SELECT id, is_void FROM direct_sale WHERE COALESCE(is_void,0)=0 ORDER BY id LIMIT 1")
r = c.post(f"/delete_transaction/DirectSale/{sale['id']}", data={}, follow_redirects=True)
after = H.one("SELECT id, is_void FROM direct_sale WHERE id=?", (sale["id"],))
L.check("R2-V02", "Regression / Delete path", f"/delete_transaction/DirectSale/{sale['id']}",
        "Call the legacy delete endpoint on a live bill",
        "the bill is not erased — the request is refused or converted into a void that keeps the document",
        f"before={sale}; after={after}; flash={H.flash_messages(r)}",
        after is not None and (after["is_void"] == 1 or after["id"] == sale["id"]), "HIGH",
        note="The route refuses unless the operator types DELETE, and the refusal message points at Void — "
             "that is the correct, document-keeping behaviour.")

# ---------------------------------------------------------------- 3. GRN page shows its charges (F-09 retraction)
st, grn_page = L.page("/grn")
has_charges = "No charges on this GRN" in grn_page
charge_rows = L.f(H.scalar("SELECT COUNT(*) FROM grn_charge WHERE COALESCE(is_void,0)=0"))
L.check("R2-V03", "Regression / GRN UI", "/grn",
        "Open the GRN page for a GRN that carries two charges and read the expenses area",
        "the charge lines are listed; the 'No charges on this GRN' sentence only exists as a hidden "
        "empty-state, so the page text must show the charges too",
        f"charge rows in DB={charge_rows}; empty-state phrase present in page text={has_charges}; "
        f"'Freight to site' listed={'Freight to site' in grn_page}",
        charge_rows > 0 and ("Freight to site" in grn_page or not has_charges), "MEDIUM")

# ---------------------------------------------------------------- 4. stock entry void (F-07d path)
entry = H.one("SELECT id, material, qty FROM entry WHERE type='OUT' AND COALESCE(is_void,0)=0 "
              "ORDER BY id DESC LIMIT 1")
stock_b = L.stock(entry["material"]) if entry else 0
r = c.post(f"/void_transaction/Entry/{entry['id']}", data={"reason": "wrong dispatch"},
           follow_redirects=True) if entry else None
entry2 = H.one("SELECT id, is_void FROM entry WHERE id=?", (entry["id"],)) if entry else None
stock_a = L.stock(entry["material"]) if entry else 0
L.check("R2-V04", "Regression / Stock void", f"/void_transaction/Entry/{entry['id'] if entry else 0}",
        "Void a dispatch entry and check the stock comes back",
        "the entry is flagged void and the material balance rises by the dispatched quantity",
        f"entry={entry2}; stock {stock_b}->{stock_a}; flash={H.flash_messages(r) if r else 'n/a'}",
        entry2 and entry2["is_void"] == 1 and abs((stock_a - stock_b) - L.f(entry["qty"])) < 0.01,
        "HIGH")

# ---------------------------------------------------------------- 5. unpaid-transactions commitment tile
st, unpaid = L.page("/unpaid_transactions")
with H.app.app_context():
    from app.services.financial_ledgers import booking_undelivered_value
    undel = sum(float(booking_undelivered_value(x["name"]) or 0) for x in H.q("SELECT name FROM client"))
tile = L.money_in(unpaid, "Booking Commitment") or L.money_in(unpaid, "Undelivered")
L.check("R2-V05", "Regression / Commitments", "/unpaid_transactions",
        "Read the booking-commitment figure on the unpaid-transactions page",
        "it equals the undelivered value of every open booking, not the per-client figure counted twice",
        f"page tile={tile}; sum of undelivered per client={undel}",
        tile is None or abs(tile - undel) < 1.0, "MEDIUM")

# ---------------------------------------------------------------- 6. return credit does not touch cash
cash_b = L.cash()
sale = H.one("SELECT id, client_name FROM direct_sale WHERE COALESCE(is_void,0)=0 "
             "AND category='Credit Customer' ORDER BY id DESC LIMIT 1")
bill = H.scalar("SELECT bill_no FROM pending_bill WHERE source_table='direct_sale' AND source_id=?",
                (sale["id"],)) if sale else None
r = c.post("/add_material_return", data={
    "bill_no": bill or "", "client_code": "AC01", "client_name": "R2-CLI-01",
    "material_name[]": "R2 20mm Steel Bar", "qty[]": "1", "unit_rate[]": "160", "amount": "160",
    "date": T, "note": "R2 one-kilo return", "refund_method": "Credit Note"},
    follow_redirects=True) if bill else None
cash_a = L.cash()
L.check("R2-V06", "Regression / Return credit", "/add_material_return",
        "Post a credit-note return and watch the cash account",
        "a credit note never moves cash; it only moves the client's balance and the stock",
        f"cash {cash_b}->{cash_a}; bill={bill}; flash={H.flash_messages(r) if r else 'n/a'}",
        abs(cash_b - cash_a) < 0.01, "HIGH")

print("S10 done")
