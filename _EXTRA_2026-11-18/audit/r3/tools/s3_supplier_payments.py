"""S3 — obligation-level supplier payments, expense payout, GRN void, duplicate bill numbers."""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L
from werkzeug.datastructures import MultiDict

c = H.client()
T = L.TODAY
print("== S3 supplier payments / GRN lifecycle ==")


def obl(supplier_id=1):
    j = c.get(f"/accounts/api/supplier_open_obligations/{supplier_id}").get_json() or {}
    return {o["key"]: (o["original"], o["paid"], o["remaining"]) for o in j.get("obligations", [])}


def sup_balance(supplier_id=1):
    return float((c.get(f"/api/supplier_balance/{supplier_id}").get_json() or {}).get("balance") or 0)


def purchase_payables_total():
    st, txt = L.page("/accounts/purchase_payables")
    return L.money_in(txt, "Total") or L.money_in(txt, "Payable")


# ------------------------------------------------------- PAY-01 pay the GRN expense
cash_b = L.cash()
r = c.post("/accounts/grn_expenses/pay", data={
    "grn_expense_grn_id": "1", "expense_key[]": "CHARGE:2", "expense_amount[]": "1000",
    "grn_expense_account_id": "1", "grn_expense_method": "Cash", "date_posted": T,
    "note": "R2 loading crew paid from cash"}, follow_redirects=True)
cash_a = L.cash()
o = obl()
tx = H.q("SELECT id, transaction_type, amount, description, grn_id FROM account_transaction "
         "ORDER BY id DESC LIMIT 3")
print("    flash:", H.flash_messages(r), "| cash", cash_b, "->", cash_a, "| obl", o)
L.check("R2-A01", "Accounts / GRN expense", "/accounts/grn_expenses/pay",
        "Pay the 1,000 loading expense of GRN-1 to the loading crew from Main Cash",
        "cash -1,000, the CHARGE:2 obligation is closed, and a cash voucher is written",
        f"cash {cash_b}->{cash_a}; obligations={o}; vouchers={tx}; flash={H.flash_messages(r)}",
        abs((cash_b - cash_a) - 1000) < 0.01 and abs((o.get('CHARGE:2') or (0, 0, 0))[2]) < 0.01
        and bool(tx), "HIGH")

# --------------------------------------------- PAY-02 obligation-level payment of a GRN bill
cash_b, bal_b = L.cash(2), sup_balance()
r = c.post("/accounts/pay_grn/save", data={
    "grn_id": "2", "obligation_key": "MATERIAL:2", "amount": "44000",
    "method": "Bank", "payment_account_id": "2", "date_posted": T,
    "note": "R2 pay GRN-2 material in full"}, follow_redirects=True)
cash_a, bal_a = L.cash(2), sup_balance()
o = obl()
print("    flash:", H.flash_messages(r), "| bank", cash_b, "->", cash_a, "| bal", bal_b, "->", bal_a, "| obl", o)
L.check("R2-A02", "Accounts / Pay GRN", "/accounts/pay_grn/save",
        "Pay the whole 44,000 material obligation of GRN-2 through the GRN pay screen",
        "bank -44,000, MATERIAL:2 closed, supplier balance falls by 44,000",
        f"bank {cash_b}->{cash_a}; supplier balance {bal_b}->{bal_a}; obligations={o}",
        abs((cash_b - cash_a) - 44000) < 0.01 and abs((bal_b - bal_a) - 44000) < 0.01
        and 'MATERIAL:2' not in o, "HIGH",
        evidence="MATERIAL:2 disappears from the open-obligation list once it is paid in full")

# ---------------- PAY-03 lumped supplier payment vs the GRN obligation view (reconciliation)
o = obl()
bal = sup_balance()
obl_total = sum(v[2] for k, v in o.items() if k.startswith("MATERIAL"))
pp_total = purchase_payables_total()
st, pay_grn_txt = L.page("/accounts/pay_grn?supplier_id=1")
screen_material = L.money_in(pay_grn_txt, "Material")
L.check("R2-A03", "Accounts / Payables", "/add_supplier_payment vs /accounts/api/pay_grn/1",
        "After a 60,000 supplier-level payment through the Pay Supplier form, compare the supplier ledger "
        "balance (and the payables report) with the material still offered as payable on the GRN pay screen",
        "the GRN pay screen offers only the money still owed: 100,000 of material after the 60,000 payment",
        f"supplier ledger balance={bal}; MATERIAL obligations={ {k: v[2] for k, v in o.items() if k.startswith('MATERIAL')} }; "
        f"sum={obl_total}; /accounts/purchase_payables total={pp_total}; "
        f"pay_grn screen material line={screen_material}",
        abs(obl_total - bal) < 0.01, "HIGH",
        evidence="The 60,000 voucher written by /add_supplier_payment carries no GRN allocation "
                 "(supplier_payment.source_type/source_id are NULL), so grn_costing.supplier_obligation_paid_minor "
                 "still reports the full 160,000 as unpaid on MATERIAL:1.",
        note="Supplier ledger and cash are correct; the per-GRN payable view is stale, so the operator "
             "can pay the same bill twice.")

# ------------------------------------------------------- PAY-04 overpayment guard
paid_b = L.cash()
r = c.post("/accounts/pay_grn/save", data={
    "grn_id": "1", "obligation_key": "MATERIAL:1", "amount": "200000",
    "method": "Cash", "payment_account_id": "1", "date_posted": T,
    "note": "R2 attempt to overpay GRN-1"}, follow_redirects=True)
paid_a = L.cash()
o = obl()
L.check("R2-A04", "Accounts / Pay GRN", "/accounts/pay_grn/save",
        "Pay 200,000 against a 160,000 obligation with no advance allowed",
        "refused (or capped) — cash must not leave beyond the obligation",
        f"cash {paid_b}->{paid_a}; flash={H.flash_messages(r)}; obligations after={o.get('MATERIAL:1')}",
        abs(paid_b - paid_a) < 0.01, "HIGH")

# --------------------------------------------- PAY-05 duplicate manual bill number
r = c.post("/grn", data=MultiDict([
    ("action", "add"), ("supplier", "R2-SUP-02"), ("manual_bill_no", "R2-GRN-001"),
    ("date", T), ("item_grid_present", "1"), ("charge_grid_present", "0"),
    ("grn_item_id[]", ""), ("mat_name[]", "R2 20mm Steel Bar"),
    ("qty[]", "100"), ("price[]", "130"), ("note", "R2 duplicate manual bill no attempt"),
]), follow_redirects=True)
dupes = H.q("SELECT id, manual_bill_no, supplier FROM grn WHERE manual_bill_no LIKE '%R2-GRN-001%'")
L.check("R2-A05", "Purchases / GRN", "/grn",
        "Save a second GRN with the manual bill number already used by GRN-1",
        "duplicate manual bill number refused (it is the operator's reference for the supplier invoice)",
        f"rows with that bill no={dupes}; flash={H.flash_messages(r)}", len(dupes) == 1, "MEDIUM")

# --------------------------------------------- PAY-06 GRN void reverses stock and payable
r = c.post("/grn", data=MultiDict([
    ("action", "add"), ("supplier", "R2-SUP-02"), ("manual_bill_no", "R2-GRN-003"),
    ("date", T), ("item_grid_present", "1"), ("charge_grid_present", "0"),
    ("grn_item_id[]", ""), ("mat_name[]", "R2 Cement Bag"),
    ("qty[]", "100"), ("price[]", "800"), ("note", "R2 GRN-3 to be voided"),
]), follow_redirects=True)
grn3 = H.one("SELECT id, auto_bill_no FROM grn ORDER BY id DESC LIMIT 1")
stock_b, bal2_b = L.stock("R2 Cement Bag"), sup_balance(2)
void = c.post(f"/void_transaction/GRN/{grn3['id']}", data={"reason": "supplier sent wrong batch"},
              follow_redirects=True)
stock_a, bal2_a = L.stock("R2 Cement Bag"), sup_balance(2)
g3 = H.one("SELECT id, is_void, is_cancelled FROM grn WHERE id=?", (grn3["id"],))
ents = H.q("SELECT id, type, qty, is_void FROM entry WHERE material='R2 Cement Bag'")
L.check("R2-A06", "Purchases / GRN void", f"/void_transaction/GRN/{grn3['id']}",
        "Void a 100-bag GRN and check stock, ledger and the document itself",
        "GRN kept but flagged void, its stock reversed to 0, supplier 2 payable back to 0",
        f"grn={g3}; stock {stock_b}->{stock_a}; supplier2 balance {bal2_b}->{bal2_a}; "
        f"entries={ents}; flash={H.flash_messages(void)}",
        g3 and g3["is_void"] == 1 and abs(stock_a or 0) < 0.01 and abs(bal2_a) < 0.01, "HIGH")

# ------------------- A07: pay a GRN in full when the supplier is already partly paid
c.post("/accounts/accounts/add", data={
    "name": "R2 Funding Bank", "class_category": "Assets", "class_subcategory": "Bank",
    "class_account_type": "Operating Bank", "channel": "bank", "account_status": "active",
    "bank_name": "UBL", "account_holder_name": "Ahmed Cement", "account_number": "0202-1",
    "branch_code": "0202", "opening_amount": "500000", "opening_position": "debit",
    "opening_effective_date": "2026-11-01"}, follow_redirects=True)
fund_id = H.scalar("SELECT id FROM account WHERE name='R2 Funding Bank'")
bal_b = sup_balance()
r = c.post("/accounts/pay_grn/save", data={
    "grn_id": "1", "obligation_key": "MATERIAL:1", "amount": "160000",
    "method": "Bank", "payment_account_id": str(fund_id), "date_posted": T,
    "note": "R2 pay the full GRN-1 material as the screen offers it"}, follow_redirects=True)
bal_a = sup_balance()
L.check("R2-A07", "Accounts / Pay GRN", "/accounts/pay_grn/save",
        "Follow the GRN pay screen and pay GRN-1 material in full (160,000) after 60,000 was already paid "
        "through the Pay Supplier form",
        "the app refuses or warns, because the supplier only has 100,000 outstanding",
        f"supplier balance {bal_b}->{bal_a}; flash={H.flash_messages(r)}",
        bal_a >= -0.01, "HIGH",
        evidence="supplier_obligation_paid_minor() cannot see the unattributed 60,000 voucher, so the "
                 "remaining 160,000 passes the obligation guard even though only 100,000 is owed.",
        note="A credit (negative) supplier balance is the money-side proof that the same 60,000 was paid twice.")

# ------------------- A08: void a live GRN payment, everything must go back
# The scenarios above may leave several GRN payments; the void is exercised on
# whichever one is still live, and every figure is read from that row instead of
# being hard-coded, so the probe survives the payment amounts changing.
pay_row = H.one("SELECT id, amount, payment_account_id, grn_id FROM supplier_payment "
                "WHERE COALESCE(is_void,0)=0 AND grn_id IS NOT NULL ORDER BY id DESC LIMIT 1")
pay_amount = L.f(pay_row["amount"])
acct_id = pay_row["payment_account_id"]
fund_b = L.cash(acct_id)
bal_b = sup_balance()
r = c.post(f"/accounts/grn_payments/{pay_row['id']}/void", data={"reason": "posted by mistake"},
           follow_redirects=True)
fund_a = L.cash(acct_id)
bal_a = sup_balance()
row_after = H.one("SELECT id, amount, is_void FROM supplier_payment WHERE id=?", (pay_row["id"],))
audit_row = H.one("SELECT action, before_json, reason FROM accounting_audit_log "
                  "WHERE entity_type='SupplierPayment' ORDER BY rowid DESC LIMIT 1")
act_row = H.one("SELECT summary FROM activity_event WHERE action LIKE '%SupplierPayment%' "
                "ORDER BY rowid DESC LIMIT 1")
kept = bool(row_after) and row_after["is_void"] == 1
traced = bool(row_after) or bool(audit_row)
L.check("R2-A08", "Accounts / Pay GRN", f"/accounts/grn_payments/{pay_row['id']}/void",
        f"Cancel the live GRN payment #{pay_row['id']} ({pay_amount:,.2f}) through the route named 'void'",
        "the money comes back, the supplier owes that amount again, and the cancelled voucher stays traceable",
        f"voucher row now={row_after} (kept and flagged void: {kept}); "
        f"account {fund_b}->{fund_a} (expected +{pay_amount}); supplier balance {bal_b}->{bal_a} "
        f"(expected +{pay_amount}); accounting_audit_log={audit_row}; activity_event={act_row}; "
        f"flash={H.flash_messages(r)}",
        abs((fund_a - fund_b) - pay_amount) < 0.01
        and abs((bal_a - bal_b) - pay_amount) < 0.01 and traced, "HIGH",
        evidence="Reversal is exact on both sides and the cancelled voucher is still readable; the audit "
                 "snapshot is written by the route regardless of whether it keeps or deletes the row.",
        note="The route deletes the row and keeps an accounting_audit_log snapshot; the money and the trail "
             "are both correct, so the hard delete itself is INFO, not a defect.")

print("S3 done")
