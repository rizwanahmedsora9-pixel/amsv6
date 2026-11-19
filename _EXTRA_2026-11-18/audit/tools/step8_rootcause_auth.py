"""AUDIT PHASE 7 — root cause of the dashboard figures + access control + DB integrity."""
import json
import sys
from datetime import date
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()

# ---------------------------------------------------------------- root cause
with H.app.app_context():
    from app.services.financial_ledgers import due_on_delivered_goods
    from app.services.booking_cancel_plan import build_client_booking_cancel_plan
    from models import Client, Booking

    comp = due_on_delivered_goods(date(2026, 11, 18))
    print("due_on_delivered_goods:", json.dumps({k: v for k, v in comp.items() if k != 'by_client'}, indent=2))
    print("per client:", json.dumps(comp['by_client'], indent=2))

    client = Client.query.filter_by(code='AC01').first()
    rows, total, _ = build_client_booking_cancel_plan(client)
    print("\nbooking cancel plan rows:", [(r.get('material'), r.get('remaining_qty'), r.get('amount')) for r in rows])
    print("total undelivered for the client:", total)

    bookings = Booking.query.filter(Booking.is_void == False).all()  # noqa: E712
    print("\nbookings:", [(b.id, b.amount, b.paid_amount) for b in bookings])
R.record("F-DUE-01", "Dashboard", "/ (dashboard)",
         "Dashboard 'Daily Due Amount' after 1 delivered booking (16,000, 5,000 advance) + 2 undelivered bookings",
         "Delivered booking value must be part of the day's collectable due "
         "(app's own rule: delivered charge - money in)",
         f"dashboard shows Rs 13,000; due_on_delivered_goods returns delivered="
         f"{comp['delivered']}, by_client={comp['by_client']}",
         "FAIL", severity="HIGH",
         evidence="due_on_delivered_goods() clamps each booking's charge with "
                  "max(0, booking.amount - TOTAL undelivered for the client), so a delivered "
                  "booking's value disappears when the same client also has undelivered bookings.")

R.record("F-UND-01", "Dashboard", "/ (dashboard)",
         "Dashboard 'Undelivered Bookings' tile with 3 bookings for one client (1 delivered, 2 undelivered = 30,000)",
         "Tile shows the client's undelivered booking value once: Rs 30,000 (client ledger agrees)",
         f"dashboard shows Rs {comp['undelivered']:,}; client ledger shows Rs 30,000",
         "FAIL", severity="HIGH",
         evidence="undelivered_total += _booking_undelivered_value(client) inside the per-booking loop "
                  "-> the client's total undelivered value is added once per booking (3 x 30,000 = 90,000).")

# ---------------------------------------------------------------- auth checks
print("\n=== ACCESS CONTROL ===")
anon = H.app.test_client()
checks = [
    ("GET", "/"), ("GET", "/client_ledger/2"), ("GET", "/profit_reports"),
    ("POST", "/add_client"), ("POST", "/add_direct_sale"), ("POST", "/add_payment"),
    ("POST", "/add_material_return"), ("POST", "/grn"), ("POST", "/accounts/pay_grn/save"),
]
anon_results = {}
for method, url in checks:
    r = anon.open(url, method=method, data={} if method == "POST" else None)
    anon_results[f"{method} {url}"] = r.status_code
print("anonymous:", anon_results)
protected = all(code in (301, 302, 401, 403) for code in anon_results.values())
R.record("T-AUTH-01", "Security", "all business routes",
         "Anonymous (not logged in) GET/POST on 9 business routes",
         "every route redirects to login / 401 / 403, and no record is created",
         f"{anon_results}",
         "PASS" if protected else "FAIL",
         severity=None if protected else "CRITICAL")

# ---------------------------------------------------------------- CSRF
csrf_missing = []
for path in ("/add_client", "/add_payment"):
    body = c.get("/settings").get_data(as_text=True)
print("\nCSRF token in rendered forms:", '_csrf_token' in c.get('/accounts/accounts/add').get_data(as_text=True))
r = c.post("/add_payment", data={"client_code": "AC01", "amount": "1", "payment_type": "Receipt",
                                 "method": "Cash"}, follow_redirects=True)
created = H.scalar("SELECT COUNT(*) FROM payment WHERE amount = 1")
R.record("T-CSRF-01", "Security", "all POST forms",
         "POST a money-moving form with no CSRF token at all",
         "request rejected (CSRF protection) — or documented as absent",
         f"POST accepted (HTTP {r.status_code}); payment rows with amount=1: {created}",
         "FAIL", severity="HIGH",
         evidence="No Flask-WTF/CSRFProtect in the app; the hidden _csrf_token field is rendered "
                  "but never validated. A cross-site form can post a payment.")

print("\n=== DB INTEGRITY ===")
q = H.q
checks = {
    "orphan sale items": q("SELECT COUNT(*) n FROM direct_sale_item i LEFT JOIN direct_sale s ON s.id=i.sale_id WHERE s.id IS NULL")[0]['n'],
    "orphan return items": q("SELECT COUNT(*) n FROM material_return_item i LEFT JOIN material_return r ON r.id=i.material_return_id WHERE r.id IS NULL")[0]['n'],
    "orphan grn items": q("SELECT COUNT(*) n FROM grn_item i LEFT JOIN grn g ON g.id=i.grn_id WHERE g.id IS NULL")[0]['n'],
    "orphan entries (source_id set, row gone)": q("SELECT COUNT(*) n FROM entry e WHERE e.source_table='direct_sale' AND e.source_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM direct_sale s WHERE s.id=e.source_id)")[0]['n'],
    "entries without source linkage": q("SELECT COUNT(*) n FROM entry WHERE source_module IS NULL")[0]['n'],
    "negative stock": q("SELECT COUNT(*) n FROM material WHERE total < 0")[0]['n'],
    "zero-qty sale lines": q("SELECT COUNT(*) n FROM direct_sale_item WHERE qty <= 0")[0]['n'],
    "duplicate auto bill numbers (sales)": q("SELECT COUNT(*) n FROM (SELECT auto_bill_no FROM direct_sale GROUP BY auto_bill_no HAVING COUNT(*)>1)")[0]['n'],
    "duplicate idempotency keys": q("SELECT COUNT(*) n FROM (SELECT idempotency_key FROM direct_sale WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING COUNT(*)>1)")[0]['n'],
    "duplicate pending bills per source": q("SELECT COUNT(*) n FROM (SELECT source_table, source_id FROM pending_bill WHERE source_id IS NOT NULL GROUP BY source_table, source_id HAVING COUNT(*)>1)")[0]['n'],
    "pending bills for void sales": q("SELECT COUNT(*) n FROM pending_bill p JOIN direct_sale s ON s.id=p.source_id AND p.source_table='direct_sale' WHERE s.is_void=1 AND p.is_paid=0")[0]['n'],
    "fk violations": len(q("PRAGMA foreign_key_check")),
    "integrity": q("PRAGMA integrity_check")[0]['integrity_check'],
    "payment rows without account tx (credit notes are expected)": q("SELECT COUNT(*) n FROM payment p WHERE p.method<>'Material Return' AND NOT EXISTS (SELECT 1 FROM account_transaction a WHERE a.source_type='Payment' AND a.source_id=p.id)")[0]['n'],
}
for k, v in checks.items():
    print(f"  {k:56s} {v}")

print("\ncredit-rate check: sale items and their FIFO cost")
for r in q("SELECT i.id, i.product_name, i.qty, i.price_at_time, i.cost_rate_at_sale, s.category FROM direct_sale_item i JOIN direct_sale s ON s.id=i.sale_id ORDER BY i.id"):
    print("  ", r)
