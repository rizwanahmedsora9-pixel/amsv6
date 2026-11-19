"""AUDIT PHASE 5 — read the business screens and compare with the ledger rows."""
import re
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()


def text(url):
    body = c.get(url, follow_redirects=True).get_data(as_text=True)
    body = re.sub(r'<script.*?</script>', ' ', body, flags=re.S)
    body = re.sub(r'<style.*?</style>', ' ', body, flags=re.S)
    body = re.sub(r'<[^>]+>', ' ', body)
    return re.sub(r'\s+', ' ', body)


def find(txt, label, n=90):
    i = txt.find(label)
    return txt[i:i + n].strip() if i >= 0 else f"<{label} not found>"


# ---------------------------------------------------------------- dashboard
dash = text("/")
print("DASHBOARD cash/due:")
for label in ("Cash", "Due", "Receivable", "Today", "Booking", "Undelivered"):
    hit = find(dash, label, 120)
    if not hit.startswith("<"):
        print("  ", hit[:120])

# ---------------------------------------------------------------- client ledger
led = text("/client_ledger?client_code=AC01")
print("\nCLIENT LEDGER (AC01):")
for label in ("Opening", "Booking", "Receipt", "Return", "Balance", "Outstanding", "Delivered"):
    hit = find(led, label, 130)
    if not hit.startswith("<"):
        print("  ", hit[:130])

# ---------------------------------------------------------------- supplier ledger
sup = text("/supplier_ledger?supplier=AUDIT-SUP-01")
print("\nSUPPLIER LEDGER:")
for label in ("Purchase", "Payment", "Balance", "Payable", "GRN"):
    hit = find(sup, label, 130)
    if not hit.startswith("<"):
        print("  ", hit[:130])

# ---------------------------------------------------------------- stock
st = text("/inventory/stock")
print("\nSTOCK SCREEN:")
for label in ("12mm Steel Bar", "20mm Steel Bar", "Total", "Value"):
    hit = find(st, label, 160)
    if not hit.startswith("<"):
        print("  ", hit[:160])

# ---------------------------------------------------------------- daily transactions
dt = text("/daily_transactions")
print("\nDAILY TRANSACTIONS:")
for label in ("Cash", "Receipt", "Return", "Total"):
    hit = find(dt, label, 140)
    if not hit.startswith("<"):
        print("  ", hit[:140])

# ---------------------------------------------------------------- integrity audit
for url in ("/integrity_audit", "/reconciliation/integrity", "/system_report"):
    r = c.get(url, follow_redirects=True)
    if r.status_code == 200:
        t = text(url)
        print(f"\nINTEGRITY ({url}):", find(t, "Integrity", 200)[:200])
        break

# ---------------------------------------------------------------- cash flow
cf = text("/cash_flow")
print("\nCASH FLOW:")
for label in ("Opening", "Closing", "Receipt", "Payment"):
    hit = find(cf, label, 130)
    if not hit.startswith("<"):
        print("  ", hit[:130])

print("\n--- ledger truth (for the report tables) ---")
print("cash account:", H.q("SELECT name, balance FROM account"))
print("pending bills:", H.q("SELECT client_name, bill_no, amount, is_paid, transaction_type FROM pending_bill ORDER BY id"))
print("payments:", H.q("SELECT id, amount, payment_type, method FROM payment ORDER BY id"))
print("supplier payments:", H.q("SELECT id, amount, payment_kind FROM supplier_payment"))
