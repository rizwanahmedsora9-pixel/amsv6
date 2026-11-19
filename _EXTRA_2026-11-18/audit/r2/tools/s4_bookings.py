"""S4 — bookings, price edits, partial/complete/alternative/excess delivery, cancellation."""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L

c = H.client()
T = L.TODAY
print("== S4 bookings & delivery ==")
M12, M20 = "R2 12mm Steel Bar", "R2 20mm Steel Bar"
CLI, CODE = "R2-CLI-01", "AC01"


def undelivered(name=CLI):
    """The app's own undelivered-booking figure for a client (read-only)."""
    with H.app.app_context():
        from app.services.financial_ledgers import booking_undelivered_value
        return float(booking_undelivered_value(name) or 0)


def allocs():
    return H.q("SELECT id, sale_id, sale_item_id, booking_item_id, qty, is_void "
               "FROM booking_allocation ORDER BY id")


def book(mat, qty, rate, advance, note):
    return c.post("/add_booking", data={
        "client_code": CODE, "client_name": CLI,
        "material_name[]": mat, "qty[]": str(qty), "unit_rate[]": str(rate),
        "amount": str(float(qty) * float(rate)), "paid_amount": str(advance),
        "discount": "0", "date": T, "note": note, "payment_method": "Cash",
        "payment_account_id": "1"}, follow_redirects=True)


_dsid = [0]


def deliver(qty, mat, rate="0"):
    _dsid[0] += 1
    return c.post("/add_direct_sale", data={
        "category": "Booking Delivery", "client_name": CLI, "client_code": CODE,
        "driver_name": "R2 Driver", "product_name[]": mat, "qty[]": str(qty),
        "unit_rate[]": rate, "paid_amount": "0", "discount": "0",
        "payment_method": "Cash", "sale_date": T, "note": "R2 delivery",
        "idempotency_key": f"r2-del-{_dsid[0]}-{mat}-{qty}"}, follow_redirects=True)


# ---------------------------------------------------------------- B-01 booking + advance
cash_b = L.cash()
r = book(M12, 100, 160, 5000, "R2 BOOK-A partial advance")
bkA = H.one("SELECT * FROM booking ORDER BY id DESC LIMIT 1")
cash_a = L.cash()
L.check("R2-B01", "Bookings", "/add_booking",
        "Book 100 kg of 12mm at 160 and take a 5,000 cash advance",
        "booking 16,000 created, the 5,000 advance is received into cash, 11,000 left to collect",
        f"booking amount={bkA['amount']} paid={bkA['paid_amount']}; cash {cash_b}->{cash_a}; "
        f"flash={H.flash_messages(r)}; undelivered for client={undelivered()}",
        abs(float(bkA["amount"]) - 16000) < 0.01 and abs(float(bkA["paid_amount"]) - 5000) < 0.01
        and abs((cash_a - cash_b) - 5000) < 0.01, "HIGH")

# ---------------------------------------------------------------- B-02 edit the price
r = c.post(f"/edit_bill/Booking/{bkA['id']}", data={
    "client_code": CODE, "client_name": CLI,
    "material_name[]": M12, "qty[]": "100", "unit_rate[]": "165",
    "amount": "16500", "paid_amount": "5000", "discount": "0", "date": T,
    "note": "R2 BOOK-A price revised to 165"}, follow_redirects=True)
bkA2 = H.one("SELECT * FROM booking WHERE id=?", (bkA["id"],))
L.check("R2-B02", "Bookings / Edit", f"/edit_bill/Booking/{bkA['id']}",
        "Revise the booking rate from 160 to 165 after the advance was taken",
        "booking total becomes 16,500, the 5,000 advance is untouched, nothing is double-posted",
        f"booking now amount={bkA2['amount']} paid={bkA2['paid_amount']}; "
        f"undelivered={undelivered()}; flash={H.flash_messages(r)}",
        abs(float(bkA2["amount"]) - 16500) < 0.01 and abs(float(bkA2["paid_amount"]) - 5000) < 0.01,
        "HIGH")

# ---------------------------------------------------------------- B-03 partial delivery
s12_b = L.stock(M12)
r = deliver(60, M12)
s12_a = L.stock(M12)
delA = H.one("SELECT * FROM direct_sale ORDER BY id DESC LIMIT 1")
itemA = H.one("SELECT * FROM direct_sale_item WHERE sale_id=? ORDER BY id DESC LIMIT 1", (delA["id"],))
L.check("R2-B03", "Bookings / Delivery", "/add_direct_sale (Booking Delivery)",
        "Deliver 60 of the 100 kg booked at 165 (booking rate, no new money)",
        "stock falls by 60, the line's cost basis is frozen for COGS, and the delivery is "
        "allocated to the booking item",
        f"stock12 {s12_b}->{s12_a}; line cost_rate_at_sale={itemA.get('cost_rate_at_sale')}; "
        f"line value={itemA.get('total_price') or itemA.get('amount')}; allocations={allocs()}",
        abs((s12_b - s12_a) - 60) < 0.01 and itemA.get("cost_rate_at_sale") is not None
        and any(a["qty"] == 60 for a in allocs()), "HIGH")

# ---------------------------------------------------------------- B-04 deliver the balance
s12_b, und_b = L.stock(M12), undelivered()
r = deliver(40, M12)
s12_a, und_a = L.stock(M12), undelivered()
L.check("R2-B04", "Bookings / Delivery", "/add_direct_sale (Booking Delivery)",
        "Deliver the remaining 40 kg so booking A is fully delivered",
        "stock falls by 40 and the client's undelivered commitment drops to zero",
        f"stock12 {s12_b}->{s12_a}; undelivered {und_b}->{und_a}; allocations={allocs()}",
        abs((s12_b - s12_a) - 40) < 0.01 and abs(und_a) < 0.01, "HIGH")

# ---------------------------------------------------------------- B-05 alternative material
book(M12, 50, 150, 0, "R2 BOOK-B booked 12mm, delivered as 20mm")
bkB = H.one("SELECT * FROM booking ORDER BY id DESC LIMIT 1")
sales_before = H.scalar("SELECT COALESCE(MAX(id),0) FROM direct_sale")
s20_b, und_b = L.stock(M20), undelivered()
r = deliver(50, M20)          # 20mm delivered against a 12mm booking
s20_a, und_a = L.stock(M20), undelivered()
new_sale = H.scalar("SELECT COALESCE(MAX(id),0) FROM direct_sale") != sales_before
L.check("R2-B05", "Bookings / Delivery (alternative material)", "/add_direct_sale (Booking Delivery)",
        "Client booked 50 kg of 12mm but the yard offers 20mm instead — try to deliver 50 kg of 20mm",
        "either the substitution is accepted and traceable (20mm stock down 50, the booked 12mm qty "
        "consumed, delivery line records the 20mm), or it is refused with a clear message — "
        "what must never happen is a saved zero-priced delivery with no stock movement",
        f"sale created={new_sale}; 20mm stock {s20_b}->{s20_a}; 12mm stock={L.stock(M12)}; "
        f"undelivered {und_b}->{und_a}; flash={H.flash_messages(r)}",
        (not new_sale) or (abs((s20_b - s20_a) - 50) < 0.01 and abs(und_a) < 0.01), "HIGH")

# ---------------------------------------------------------------- B-06 excess delivery
book(M20, 50, 150, 0, "R2 BOOK-C to over-deliver")
bkC = H.one("SELECT * FROM booking ORDER BY id DESC LIMIT 1")
sales_before = H.scalar("SELECT COALESCE(MAX(id),0) FROM direct_sale")
s20_b, und_b = L.stock(M20), undelivered()
r = deliver(60, M20)          # 60 kg against a 50 kg booking
s20_a, und_a = L.stock(M20), undelivered()
new_sale = H.scalar("SELECT COALESCE(MAX(id),0) FROM direct_sale") != sales_before
last_id = H.scalar("SELECT COALESCE(MAX(id),0) FROM direct_sale")
al = [a for a in allocs() if a["sale_id"] == last_id] if new_sale else []
L.check("R2-B06", "Bookings / Delivery (excess)", "/add_direct_sale (Booking Delivery)",
        "Deliver 60 kg against a 50 kg booking",
        "either refused, or the 10 kg excess is charged as an ordinary sale — it must never be "
        "absorbed into a zero-priced booking delivery",
        f"sale created={new_sale}; 20mm stock {s20_b}->{s20_a}; undelivered {und_b}->{und_a}; "
        f"allocated qty to the booking={sum(a['qty'] for a in al)}; flash={H.flash_messages(r)}",
        (not new_sale) or (abs((s20_b - s20_a) - 60) < 0.01 and abs(und_a) < 0.01), "HIGH")

# ---------------------------------------------------------------- B-07 cancel with full advance
client_id = H.scalar("SELECT id FROM client WHERE code='AC01'")


def client_ledger():
    with H.app.app_context():
        from app.services.financial_ledgers import build_client_financial_ledger, Client
        cl = Client.query.filter_by(code='AC01').first()
        d = build_client_financial_ledger(cl)
        return float(d.get('total_debit') or 0), float(d.get('total_credit') or 0), \
            float(d.get('closing_balance') or 0), d


due_b, cred_b, net_b, _ = client_ledger()
cash_b = L.cash()
book(M20, 100, 150, 15000, "R2 BOOK-D full advance to cancel")
bkD = H.one("SELECT * FROM booking ORDER BY id DESC LIMIT 1")
cash_mid = L.cash()
items_b = H.scalar("SELECT COUNT(*) FROM booking_item WHERE booking_id=?", (bkD["id"],))
r = c.post(f"/client_booking_cancel/{client_id}",
           data={"client_id": str(client_id), "confirm": "1"}, follow_redirects=True)
cash_a = L.cash()
bkD2 = H.one("SELECT * FROM booking WHERE id=?", (bkD["id"],))
items_a = H.scalar("SELECT COUNT(*) FROM booking_item WHERE booking_id=?", (bkD["id"],))
due_a, cred_a, net_a, led = client_ledger()
und_a = undelivered()
cancel_rows = [(x['reference'], x['credit']) for x in led.get('rows', []) if x.get('type') == 'Booking Cancel']
act = H.q("SELECT summary FROM activity_event WHERE action='update:Booking' ORDER BY id DESC LIMIT 4")
L.check("R2-B07", "Bookings / Cancel", "/client_booking_cancel",
        "Cancel a 15,000 booking that was paid in full in advance (the client's undelivered bookings are "
        "cancelled LIFO)",
        "every cancelled booking stops being receivable, and the 15,000 that was actually paid stays as a "
        "client credit (the client is owed exactly 15,000 less the open bookings)",
        f"cash {cash_b}->{cash_mid}->{cash_a}; booking D amount {bkD['amount']}->{bkD2['amount']}; "
        f"client totals debit {due_b}->{due_a} credit {cred_b}->{cred_a} net {net_b}->{net_a}; "
        f"cancellation rows={cancel_rows}; undelivered after={und_a}; flash={H.flash_messages(r)}",
        abs((net_a - net_b) + sum(c[1] for c in cancel_rows)) < 1.0
        and abs(net_a + 3500.0) < 1.0, "HIGH",
        evidence=f"activity_event: {act}")

L.check("R2-B09", "Bookings / Cancel (document)", "/client_booking_cancel",
        "Inspect what is left of a cancelled booking document",
        "the cancelled booking still shows what was ordered (material, quantity, rate) or the original "
        "values are archived somewhere an auditor can read",
        f"booking D now amount={bkD2['amount']} paid={bkD2['paid_amount']} void={bkD2['is_void']}; "
        f"booking_item rows {items_b}->{items_a}; activity={act[:1]}",
        items_a == items_b, "MEDIUM",
        note="The money is right (the advance becomes a client credit), but cancelling rewrites booking.amount "
             "to the undelivered remainder and deletes the booking_item lines, so the order content survives "
             "only in the ledger description and the activity log.")

# ---------------------------------------------------------------- B-08 dashboard commitment
book(M12, 20, 150, 0, "R2 BOOK-E open commitment")
st, dash = L.page("/")
und = undelivered()
tile = L.money_in(dash, "Undelivered") or L.money_in(dash, "Booking Commitment") \
    or L.money_in(dash, "Undelivered Booking")
st2, unpaid = L.page("/unpaid_transactions")
tile2 = L.money_in(unpaid, "Booking Commitment") or L.money_in(unpaid, "Undelivered")
L.check("R2-B08", "Dashboard / Commitments", "/ + /unpaid_transactions",
        "Compare the dashboard and the unpaid-transactions commitment figures with the undelivered "
        "value the ledgers compute for the open bookings",
        "every screen shows the same undelivered commitment (3,000 for the new 20 kg booking)",
        f"dashboard figure={tile}; unpaid page figure={tile2}; computed undelivered={und}",
        und and tile is not None and abs(tile - und) < 1.0, "MEDIUM")

print("S4 done")
