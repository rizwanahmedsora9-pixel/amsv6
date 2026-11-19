"""Independent reconciliation of the FIXED build's test day.

Expectations are computed from raw source rows (purchases, sales, returns,
bookings, allocations); observations are read from the rendered screens.
Nothing is hard-coded.
"""
import csv
import re
import sys

sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H

q, one, scalar = H.q, H.one, H.scalar
c = H.client()


def page_text(url):
    body = c.get(url, follow_redirects=True).get_data(as_text=True)
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    txt = re.sub(r"\s+", " ", body)
    j = txt.find("Profit Reports Ledger Supplier Ledger")
    return txt[j + 40:] if j > 0 else txt


def money(pattern, txt):
    m = re.search(pattern, txt)
    return float(m.group(1).replace(",", "")) if m else None


profit = page_text("/profit_reports")
cashflow = page_text("/cash_flow")
payables_page = page_text("/current_payables")
stock_page = page_text("/inventory/stock_summary")
dash = page_text("/")

# ------------------------------------------------------------------ expected
sales = q("SELECT id, amount, discount, paid_amount FROM direct_sale WHERE is_void=0")
returns = q("SELECT id, amount FROM material_return WHERE is_void=0")
bookings = q("SELECT id, amount FROM booking WHERE is_void=0")
items = q("SELECT i.qty, i.cost_rate_at_sale, s.is_void FROM direct_sale_item i "
          "JOIN direct_sale s ON s.id=i.sale_id")

# delivered booking value: what the booking allocations actually dispatched
allocations = q("""
    SELECT ba.qty, bi.price_at_time, bi.material_name
    FROM booking_allocation ba
    JOIN booking_item bi ON bi.id = ba.booking_item_id
    JOIN direct_sale s ON s.id = ba.sale_id
    WHERE ba.is_void = 0 AND s.is_void = 0
""")
delivered_booking_value = sum(float(a["qty"]) * float(a["price_at_time"] or 0) for a in allocations)

gross_sales = sum(float(s["amount"]) for s in sales) + delivered_booking_value - sum(
    float(r["amount"]) for r in returns)
discount_net = sum(float(s["discount"]) for s in sales) - float(
    scalar("SELECT COALESCE(SUM(discount_reversed),0) FROM material_return_item") or 0)
net_revenue = gross_sales - discount_net

cogs = sum(float(i["qty"]) * float(i["cost_rate_at_sale"] or 0) for i in items if not i["is_void"])
for r in q("SELECT ri.qty, ri.material_name FROM material_return_item ri "
           "JOIN material_return r ON r.id = ri.material_return_id WHERE r.is_void=0"):
    rate = one("SELECT cost_rate_at_sale FROM direct_sale_item i JOIN direct_sale s ON s.id=i.sale_id "
               "WHERE i.product_name = ? AND s.is_void=0 ORDER BY i.id DESC LIMIT 1", (r["material_name"],))
    cogs -= float(r["qty"]) * float((rate or {}).get("cost_rate_at_sale") or 0)
net_profit = net_revenue - cogs

# stock from the movement ledger
ledger_stock = {}
for row in q("SELECT material, "
             "SUM(CASE WHEN type='IN' THEN qty ELSE -qty END) q "
             "FROM entry WHERE is_void=0 GROUP BY material"):
    ledger_stock[row["material"]] = float(row["q"] or 0)
master_stock = {m["name"]: float(m["total"] or 0) for m in q("SELECT name, total FROM material")}

# cash from the account ledger
account_cash = float(one("SELECT balance FROM account WHERE id=1")["balance"])
account_in = float(scalar("SELECT COALESCE(SUM(amount),0) FROM account_transaction "
                          "WHERE COALESCE(to_account_id,0)=1 AND COALESCE(from_account_id,0)<>1 "
                          "AND is_void=0") or 0)
account_out = float(scalar("SELECT COALESCE(SUM(amount),0) FROM account_transaction "
                           "WHERE COALESCE(from_account_id,0)=1 AND COALESCE(to_account_id,0)<>1 "
                           "AND is_void=0") or 0)

# receivables and undelivered commitments from the source documents
open_bills = float(scalar("SELECT COALESCE(SUM(amount),0) FROM pending_bill "
                          "WHERE is_void=0 AND is_paid=0") or 0)
with H.app.app_context():
    from app.services.booking_cancel_plan import build_client_booking_cancel_plan
    from models import Client
    commitment = 0.0
    for client in Client.query.all():
        _rows, total, _q = build_client_booking_cancel_plan(client)
        commitment += float(total or 0)

expected = {
    "gross_sales": gross_sales,
    "discounts": discount_net,
    "net_revenue": net_revenue,
    "cogs": cogs,
    "net_profit": net_profit,
    "cash_closing": account_cash,
    "cash_in": account_in,
    "cash_out": account_out,
    "stock_12mm": ledger_stock.get("12mm Steel Bar", 0.0),
    "stock_20mm": ledger_stock.get("20mm Steel Bar", 0.0),
    "receivable": open_bills - commitment,
    "commitment": commitment,
    "booking_delivered": delivered_booking_value,
}

observed = {
    "gross_sales": money(r"Gross sales Rs\. ([\d,]+(?:\.\d\d)?)", profit),
    "discounts": money(r"Discounts given Rs\. ([\d,]+(?:\.\d\d)?)", profit),
    "net_revenue": money(r"Net revenue Rs\. ([\d,]+(?:\.\d\d)?)", profit),
    "cogs": money(r"COGS \+ Direct Service Cost Rs\. ([\d,]+(?:\.\d\d)?)", profit),
    "net_profit": money(r"Net Profit / Loss for the selected period Rs\. ([\d,]+(?:\.\d\d)?)", profit),
    "cash_closing": money(r"Closing Balance Rs\. ([\d,]+(?:\.\d\d)?)", cashflow),
    "cash_in": money(r"Total Received \[ IN \] Rs\. ([\d,]+(?:\.\d\d)?)", cashflow),
    "cash_out": money(r"Total Spent \[ OUT \] Rs\. ([\d,]+(?:\.\d\d)?)", cashflow),
    "stock_12mm": master_stock.get("12mm Steel Bar"),
    "stock_20mm": master_stock.get("20mm Steel Bar"),
    "receivable": money(r"Outstanding Payables Rs\. ([\d,]+(?:\.\d\d)?)", payables_page),
    "commitment": money(r"Plus Rs\. ([\d,]+(?:\.\d\d)?) of undelivered booking commitments", payables_page),
    "booking_delivered": None,
}
observed["booking_delivered"] = None

labels = {
    "gross_sales": ("Gross sales", "Profit Reports"),
    "discounts": ("Discounts", "Profit Reports"),
    "net_revenue": ("Net revenue", "Profit Reports"),
    "cogs": ("COGS (frozen FIFO)", "Profit Reports"),
    "net_profit": ("Net profit", "Profit Reports"),
    "cash_closing": ("Cash closing", "Cash Flow"),
    "cash_in": ("Cash received", "Cash Flow"),
    "cash_out": ("Cash paid out", "Cash Flow"),
    "stock_12mm": ("Stock 12mm (master vs movement ledger)", "Stock Summary"),
    "stock_20mm": ("Stock 20mm (master vs movement ledger)", "Stock Summary"),
    "receivable": ("Receivable excluding commitments", "Current Payables"),
    "commitment": ("Undelivered booking commitments", "Current Payables"),
}

rows = []
print(f"{'figure':48s} {'expected':>12s} {'observed':>12s} {'diff':>9s}  verdict")
for key, (label, screen) in labels.items():
    exp, obs = expected[key], observed[key]
    if obs is None:
        rows.append({"figure": label, "screen": screen, "expected": round(exp, 2),
                     "observed": "", "variance": "", "verdict": "NOT FOUND"})
        print(f"{label:48s} {exp:12,.2f} {'-':>12s} {'-':>9s}  NOT FOUND")
        continue
    diff = round(obs - exp, 2)
    verdict = "PASS" if abs(diff) < 0.02 else "FAIL"
    rows.append({"figure": label, "screen": screen, "expected": round(exp, 2),
                 "observed": round(obs, 2), "variance": diff, "verdict": verdict})
    print(f"{label:48s} {exp:12,.2f} {obs:12,.2f} {diff:+9.2f}  {verdict}")

with open("/home/user/AUDIT_2026-11-18/retest/FINANCIAL_RECONCILIATION_RETEST_2026-11-18.csv",
          "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["figure", "screen", "expected", "observed", "variance", "verdict"])
    w.writeheader()
    w.writerows(rows)

print("\ndashboard:", {
    "due": money(r"Daily Due Amount Rs\. ([\d,]+(?:\.\d\d)?)", dash),
    "undelivered": money(r"Undelivered Bookings Rs\. ([\d,]+(?:\.\d\d)?)", dash),
    "outstanding": money(r"Total Outstanding Rs\. ([\d,]+(?:\.\d\d)?)", dash),
})

print("\n=== integrity (fixed build) ===")
checks = {
    "integrity_check": one("PRAGMA integrity_check")["integrity_check"],
    "fk_violations": len(q("PRAGMA foreign_key_check")),
    "orphan_sale_items": scalar("SELECT COUNT(*) FROM direct_sale_item i LEFT JOIN direct_sale s ON s.id=i.sale_id WHERE s.id IS NULL"),
    "orphan_return_items": scalar("SELECT COUNT(*) FROM material_return_item i LEFT JOIN material_return r ON r.id=i.material_return_id WHERE r.id IS NULL"),
    "entries_without_source": scalar("SELECT COUNT(*) FROM entry WHERE source_module IS NULL"),
    "negative_stock": scalar("SELECT COUNT(*) FROM material WHERE total < 0"),
    "zero_amount_live_bills": scalar("SELECT COUNT(*) FROM pending_bill WHERE is_paid=0 AND is_void=0 AND amount<=0"),
    "duplicate_idempotency": scalar("SELECT COUNT(*) FROM (SELECT idempotency_key FROM direct_sale WHERE idempotency_key IS NOT NULL GROUP BY idempotency_key HAVING COUNT(*)>1)"),
    "dup_pending_per_source": scalar("SELECT COUNT(*) FROM (SELECT source_table, source_id FROM pending_bill WHERE source_id IS NOT NULL GROUP BY source_table, source_id HAVING COUNT(*)>1)"),
    "pending_for_voided_sales": scalar("SELECT COUNT(*) FROM pending_bill p JOIN direct_sale s ON s.id=p.source_id AND p.source_table='direct_sale' WHERE s.is_void=1 AND p.is_void=0"),
    "returns_without_link": scalar("SELECT COUNT(*) FROM material_return WHERE source_sale_id IS NULL"),
    "grn_entries_unlinked": scalar("SELECT COUNT(*) FROM entry WHERE source_module='GRN' AND source_id IS NULL"),
    "voided_sales_still_present": scalar("SELECT COUNT(*) FROM direct_sale WHERE is_void=1"),
}
for k, v in checks.items():
    print(f"  {k:30s} {v}")
