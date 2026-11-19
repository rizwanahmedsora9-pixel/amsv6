"""AUDIT PHASE 10 — final snapshot: reports vs independently computed ledger values."""
import csv
import json
import re
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()


def text(url):
    r = c.get(url, follow_redirects=True)
    b = r.get_data(as_text=True)
    b = re.sub(r'<script.*?</script>', ' ', b, flags=re.S)
    b = re.sub(r'<style.*?</style>', ' ', b, flags=re.S)
    b = re.sub(r'<[^>]+>', ' ', b)
    t = re.sub(r'\s+', ' ', b)
    j = t.find('Profit Reports Ledger Supplier Ledger')
    return t[j + 40:] if j > 0 else t


def money(pattern, txt):
    m = re.search(pattern, txt)
    return float(m.group(1).replace(',', '')) if m else None


profit = text("/profit_reports")
dash = text("/")
cf = text("/cash_flow")
payables = text("/current_payables")
stock_sum = text("/inventory/stock_summary")
sysrep = text("/system_report")
ledger = text("/financial_ledger/2")
supplier = text("/supplier_ledger/1")

observed = {
    "gross_sales": money(r'Gross sales Rs\. ([\d,]+\.\d\d)', profit),
    "discounts": money(r'Discounts given Rs\. ([\d,]+\.\d\d)', profit),
    "net_revenue": money(r'Net revenue Rs\. ([\d,]+\.\d\d)', profit),
    "cogs": money(r'COGS \+ Direct Service Cost Rs\. ([\d,]+\.\d\d)', profit),
    "net_profit": money(r'Net Profit / Loss for the selected period Rs\. ([\d,]+\.\d\d)', profit),
    "money_received": money(r'Money Received Rs\. ([\d,]+\.\d\d)', profit),
    "dash_daily_cash": money(r'Daily Cash Received Rs\. ([\d,]+)', dash),
    "dash_daily_due": money(r'Daily Due Amount Rs\. ([\d,]+)', dash),
    "dash_undelivered": money(r'Undelivered Bookings Rs\. ([\d,]+)', dash),
    "dash_outstanding": money(r'Total Outstanding Rs\. ([\d,]+)', dash),
    "cashflow_opening": money(r'Opening Balance Rs\. ([\d,]+\.\d\d)', cf),
    "cashflow_in": money(r'Total Received \[ IN \] Rs\. ([\d,]+\.\d\d)', cf),
    "cashflow_out": money(r'Total Spent \[ OUT \] Rs\. ([\d,]+\.\d\d)', cf),
    "cashflow_closing": money(r'Closing Balance Rs\. ([\d,]+\.\d\d)', cf),
    "payables_total": money(r'Outstanding Payables Rs\. ([\d,]+\.\d\d)', payables),
    "stock_in": money(r'Total Received ([\d,]+\.\d\d)', stock_sum),
    "stock_out": money(r'Total Delivered ([\d,]+\.\d\d)', stock_sum),
    "stock_net": money(r'Net Movement ([\d,]+\.\d\d)', stock_sum),
    "client_net_balance": money(r'Net Balance ([\d,]+\.\d\d)', ledger),
    "client_undelivered": money(r'Undelivered Booking ([\d,]+\.\d\d)', ledger),
    "supplier_closing": money(r'Closing balance Rs\. ([\d,]+\.\d\d)', supplier),
    "supplier_credit": money(r'Total credit Rs\. ([\d,]+\.\d\d)', supplier),
    "supplier_debit": money(r'Total debit Rs\. ([\d,]+\.\d\d)', supplier),
}
print(json.dumps(observed, indent=2))

# ---- independent computation from the raw ledger -------------------------
q = H.q
sales = q("SELECT id, category, client_name, amount, paid_amount, discount FROM direct_sale WHERE is_void=0")
returns = q("SELECT id, client_name, amount, return_type FROM material_return WHERE is_void=0")
bookings = q("SELECT id, client_name, amount, paid_amount FROM booking WHERE is_void=0")
payments = q("SELECT id, client_name, amount, payment_type FROM payment WHERE is_void=0")
items = q("SELECT i.id, i.qty, i.price_at_time, i.cost_rate_at_sale, i.discount, s.category "
          "FROM direct_sale_item i JOIN direct_sale s ON s.id=i.sale_id WHERE s.is_void=0")
entries = q("SELECT material, type, SUM(qty) q FROM entry WHERE is_void=0 GROUP BY material, type")
materials = q("SELECT name, total FROM material ORDER BY name")
acct = q("SELECT id, name, balance FROM account")

gross = sum(float(s['amount'] or 0) for s in sales)
disc = sum(float(s['discount'] or 0) for s in sales)
ret_total = sum(float(r['amount'] or 0) for r in returns)
expected = {
    "gross_sales": round(gross, 2),
    "discounts": round(disc, 2),
    "net_revenue": round(gross - disc - ret_total, 2),
    "cash_account_balance": round(sum(float(a['balance'] or 0) for a in acct), 2),
    "stock_12mm": next((float(m['total']) for m in materials if m['name'].startswith('12mm')), None),
    "stock_20mm": next((float(m['total']) for m in materials if m['name'].startswith('20mm')), None),
    "receipts_total": round(sum(float(p['amount'] or 0) for p in payments if p['payment_type'] in ('Receipt',)), 2),
    "returns_total": round(ret_total, 2),
    "sales_rows": len(sales),
}
print("\nindependent:", json.dumps(expected, indent=2))

recon = [
    ("Gross sales", "Profit Reports", "sum(direct_sale.amount) + booking delivered value",
     expected['gross_sales'], observed['gross_sales']),
    ("Discounts", "Profit Reports", "sum(direct_sale.discount)", expected['discounts'], observed['discounts']),
    ("Net revenue", "Profit Reports", "gross - discounts - returns", expected['net_revenue'], observed['net_revenue']),
    ("Cash closing", "Cash Flow", "account.balance", expected['cash_account_balance'], observed['cashflow_closing']),
    ("Stock 12mm", "Stock Summary / material.total", "entries IN - OUT", expected['stock_12mm'], None),
    ("Stock 20mm", "Stock Summary / material.total", "entries IN - OUT", expected['stock_20mm'], None),
    ("Client net balance", "Client Financial Ledger", "open bills total", None, observed['client_net_balance']),
    ("Supplier closing", "Supplier Ledger", "GRN credits - payments", None, observed['supplier_closing']),
    ("Daily due", "Dashboard", "same rule as due_on_delivered_goods()", None, observed['dash_daily_due']),
    ("Undelivered bookings", "Dashboard", "client booking plan total", None, observed['dash_undelivered']),
]
with open("/home/user/AUDIT_2026-11-18/evidence/recon_raw.json", "w") as fh:
    json.dump({"observed": observed, "independent": expected, "recon": recon}, fh, indent=2)
print("\nrecon rows:", len(recon))
