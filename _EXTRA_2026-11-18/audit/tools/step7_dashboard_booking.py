"""AUDIT PHASE 6 — undelivered booking vs delivered receivable, dashboard, integrity."""
import re
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()
TODAY = "2026-11-18"


def text(url):
    r = c.get(url, follow_redirects=True)
    b = r.get_data(as_text=True)
    b = re.sub(r'<script.*?</script>', ' ', b, flags=re.S)
    b = re.sub(r'<style.*?</style>', ' ', b, flags=re.S)
    b = re.sub(r'<[^>]+>', ' ', b)
    t = re.sub(r'\s+', ' ', b)
    j = t.find('Profit Reports Ledger Supplier Ledger')
    return t[j + 40:] if j > 0 else t


before_payables = text("/current_payables")
before_dash = text("/")

# ---------------------------------------------------------------- T-BOOK-03
# A booking that is NEVER delivered: 100 kg 20mm @150 = 15,000, no advance.
r = c.post("/add_booking", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01",
    "material_name[]": ["20mm Steel Bar"], "qty[]": ["100"], "unit_rate[]": ["150"],
    "amount": "15000", "paid_amount": "0", "discount": "0",
    "date": TODAY, "note": "AUD-UNDELIVERED-BOOK", "payment_method": "Cash",
}, follow_redirects=True)
bk = H.q("SELECT id, client_name, amount, paid_amount, note FROM booking ORDER BY id")
after_payables = text("/current_payables")
pb = H.q("SELECT client_name, bill_no, amount, transaction_type, source_table FROM pending_bill ORDER BY id")

m_before = re.search(r'Outstanding Payables Rs\. ([\d,]+\.\d\d)', before_payables)
m_after = re.search(r'Outstanding Payables Rs\. ([\d,]+\.\d\d)', after_payables)
print("payables before:", m_before.group(1) if m_before else "?", "-> after:", m_after.group(1) if m_after else "?")
print("bookings:", bk)
print("pending bills:", pb)

R.record("T-BOOK-03", "Bookings / Receivables", "/add_booking + /current_payables",
         "Undelivered booking 15,000 (no advance) — is it counted as an ordinary collectible receivable?",
         "Undelivered booking balance must stay a booking/advance figure, not a delivered receivable.",
         f"payables {m_before.group(1) if m_before else 0} -> {m_after.group(1) if m_after else 0}; pending={pb}",
         "INFO",  # policy answer, classified after reading the dashboard
         evidence=f"current_payables_before={m_before.group(1) if m_before else None} after={m_after.group(1) if m_after else None}")

# ---------------------------------------------------------------- dashboard numbers
dash = text("/")
print("\nDASHBOARD (body):")
print(dash[:1500])

# ---------------------------------------------------------------- financial ledger AC01
fin = text("/financial_ledger/2")
print("\nFINANCIAL LEDGER AC01:")
print(fin[:1600])

# ---------------------------------------------------------------- daily transactions
dt = text("/inventory/daily_transactions")
print("\nDAILY TRANSACTIONS (inventory):")
print(dt[:1200])

# ---------------------------------------------------------------- integrity / system report
sr = text("/system_report")
print("\nSYSTEM REPORT:")
print(sr[:1500])
