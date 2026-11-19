"""Build the round-2 deliverable CSVs from evidence/results.json."""
import csv
import json
import os

AUDIT = "/home/user/AUDIT_2026-11-18/r2"
RESULTS = f"{AUDIT}/evidence/results.json"
DATE = "2026-11-18"

rows = json.load(open(RESULTS))
by_id = {r["id"]: r for r in rows}

# ---------------------------------------------------------------- TEST_RESULTS
with open(f"{AUDIT}/TEST_RESULTS_{DATE}_R2.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["Test ID", "Module", "Page / route", "Scenario", "Expected", "Actual",
                "Verdict", "Severity if failed", "Evidence", "Note"])
    for r in rows:
        w.writerow([r["id"], r["module"], r["page"], r["scenario"], r["expected"], r["actual"],
                    r["verdict"], r["severity"] or "", r["evidence"] or "", r["note"] or ""])

# ---------------------------------------------------------------- BUG_REGISTER
FINDINGS = [
 dict(id="F-R2-01", severity="HIGH", status="NEW",
      title="Supplier payments taken through the Pay Supplier form are not allocated to GRN obligations, "
            "so the GRN pay screen keeps offering the full bill and the same money can be paid twice",
      module="Accounts / Payables / Pay GRN",
      pages="/add_supplier_payment (pay_supplier.html, supplier_ledger.html, payments.html), "
            "/accounts/pay_grn, /accounts/api/supplier_open_obligations/<supplier>, /accounts/api/pay_grn/<grn>",
      env="Isolated copy of AMSV5-2_FIXED on SQLite (86 tables, 618 routes); Admin session; no production data touched.",
      precond="GRN-1: 1,000 kg 12mm + 500 kg 20mm, material obligation 160,000. GRN-2: 400 kg, 44,000.",
      steps="1) Pay 60,000 from the Pay Supplier form against supplier R2-SUP-01.\n"
            "2) Read /api/supplier_balance/1 (100,000).\n"
            "3) Read /accounts/api/supplier_open_obligations/1 and /accounts/api/pay_grn/1 (MATERIAL:1 still 160,000).\n"
            "4) Pay MATERIAL:1 in full from the GRN pay screen.",
      expected="After step 1 the GRN pay screen offers only the 100,000 still owed; step 4 is refused or warns.",
      actual="Step 1: cash -60,000, supplier ledger 204,000 -> 144,000, but MATERIAL:1 still shows paid 0.00 / "
             "remaining 160,000 and the drop-down reads 'stock due 160,000.00'. Step 4 succeeded: cash -160,000 "
             "and the supplier balance fell from 100,000 to -60,000 (a credit the supplier never earned).",
      impact="The shop can pay 60,000 twice for one delivery note. The supplier ledger and cash stay internally "
             "consistent (so nothing looks broken), the money is simply gone until the supplier refunds it.",
      cause="grn_costing.supplier_obligation_paid_minor() only sees payments attributed to a GRN "
            "(supplier_payment.source_type='GRN_PAYMENT'); /add_supplier_payment writes a voucher with "
            "source_type/source_id NULL, and the report layer (/accounts/purchase_payables) nets it while the "
            "GRN screens do not.",
      tests="R2-P05, R2-A03, R2-A07"),
 dict(id="F-R2-02", severity="HIGH", status="NEW",
      title="Voiding a sale that already has a material return counts the returned goods twice: phantom stock "
            "and a client credit that is never reversed",
      module="Sales / Void + Returns",
      pages="/void_transaction/DirectSale/<id>, /add_material_return, /stock_summary, /financial_ledger/<id>",
      env="Isolated copy of AMSV5-2_FIXED; Admin session.",
      precond="Credit sale SB-SL-1004: 200 kg 20mm at 160 = 32,000; 10,000 received; 20 kg returned (3,200).",
      steps="1) Void the credit sale after the return.\n2) Read the 20mm stock, the stock entries and the client ledger.",
      expected="The 200 kg delivered comes back once: stock returns to the physical 500 kg, and the client is "
               "owed back only the 35,000 they actually paid (5,000 opening settled).",
      actual="Stock went 320 -> 520 kg: the voided OUT entry restores 200 while the return entry had already "
             "restored 20, so 520 instead of 500 kg. The return credit of 3,200 stayed on the client ledger: "
             "net -38,200 instead of -35,000. The P&L export then quotes earned revenue 35,800 while the net "
             "profit is computed from 39,000.",
      impact="Stock is overstated and the shop owes the customer 3,200 it has already given back in goods. The "
             "error is invisible on screen because every total is internally consistent.",
      cause="The void path reverses the sale's stock entry and its receivable, but never looks for material_return "
            "rows pointing at the sale (material_return.source_sale_id), so their stock and credit entries survive.",
      tests="R2-S09, R2-R06"),
 dict(id="F-R2-03", severity="HIGH", status="NEW",
      title="An expense recorded through the Accounts expense form cannot be voided or deleted: the request dies "
            "on a foreign-key constraint and the operator only sees a generic 'data error'",
      module="Accounts / Expenditures",
      pages="POST /accounts/transactions/<id>/void, POST /accounts/transactions/<id>/delete",
      env="Isolated copy of AMSV5-2_FIXED; Admin session.",
      precond="3,000 shop rent posted from /accounts/transactions/new (pay_target=personal_expense, category + payee).",
      steps="1) POST the void route for that voucher (with or without a reason).\n2) Read the account transaction and the cash balance.",
      expected="The voucher is reversed (cash back to where it was) and kept as a void document, like every other module.",
      actual="HTTP 200 with the flash 'The operation could not be completed due to a data error. No changes were "
             "saved'. Server log: sqlalchemy.exc.IntegrityError FOREIGN KEY constraint failed on "
             "DELETE FROM account_transaction WHERE id = ?. Cash unchanged, voucher still live.",
      impact="A wrong expense can only be corrected by editing the amount (which works), never removed; an expense "
             "posted to the wrong account or by the wrong user has no reversal path at all.",
      cause="cash_flow_svc.void_manual_cash_flow_entry() deletes the AccountTransaction (line 1367) before the "
            "CashFlowEntry that references it through cash_flow_entry.account_tx_id (line 1370).",
      tests="R2-C06"),
 dict(id="F-R2-04", severity="HIGH", status="NEW",
      title="GET /logs dies with an AttributeError for an anonymous visitor instead of redirecting to the login page",
      module="System / Logs & Activity (security)",
      pages="/logs, /logs/",
      env="Isolated copy of AMSV5-2_FIXED; no session.",
      precond="None.",
      steps="1) Request /logs without a cookie.\n2) Compare with /settings or /system_report.",
      expected="302 to /login?next=/logs like every other protected page.",
      actual="AttributeError: 'AnonymousUserMixin' object has no attribute 'role' — the request never reaches a "
             "redirect and the browser gets a 500 page.",
      impact="Broken page for anyone hitting a stale bookmark or a shared link; the login redirect that should "
             "guide them never appears. No data is exposed.",
      cause="app/blueprints/system/logs.py::_guard() reads current_user.role before any authentication check, and "
            "the routes carry no @login_required.",
      tests="R2-X09"),
 dict(id="F-R2-05", severity="HIGH", status="NEW",
      title="A newly created user with the default (all-zero) permissions is stuck in an infinite redirect loop",
      module="Settings / Users & roles",
      pages="POST /add_user, / (dashboard)",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="Admin creates a second user (role=user) and ticks nothing — the form stores every can_* flag as 0.",
      steps="1) Log in as the new user.\n2) Open / .",
      expected="A page that explains the account has no access yet, or a redirect to a page that does.",
      actual="302 to / for ever (browser: ERR_TOO_MANY_REDIRECTS). Granting one permission (can_view_dashboard) "
             "makes / return 200, so the loop is the 'no permission' branch of the dashboard guard.",
      impact="Every user must be created and then fixed in a second step; a half-configured account looks like a "
             "dead application to its owner.",
      cause="The dashboard guard redirects to index when the user lacks can_view_dashboard; index is the same page, "
            "so the guard re-fires with no terminal branch.",
      tests="R2-X10"),
 dict(id="F-R2-06", severity="HIGH", status="NEW",
      title="An under-collected counter sale to a free-text walk-in name creates a receivable that belongs to no "
            "client, so no ledger can show it and Current Payables reports zero",
      module="Sales / Receivables / Current Payables",
      pages="/add_direct_sale (category Cash, new client name), /current_payables, /financial_ledger, /system_report",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="Counter sale to 'R2 Walk-in' (not in the client master), 7,500 bill, 5,000 collected.",
      steps="1) Save the sale (flash: 'Rs. 2,500.00 shown as unpaid instead of rejecting it — collect it from the "
            "client ledger').\n2) Open /current_payables, the client list and the system report.",
      expected="The bill is either attached to a client record or the operator is told it cannot be chased.",
      actual="pending_bill SB-SL-1003 (3,000) exists with client_name 'R2 Walk-in' and no client row; "
             "/current_payables lists 0 clients and Rs. 0.00; the system report counts 3 unpaid bills. The flash "
             "sends the operator to a ledger that has no such client.",
      impact="Money owed by a walk-in can never be chased from the UI; the receivable exists only as a row in a "
             "table nobody reads. Any walk-in short payment is silently lost.",
      cause="The direct-sale writer creates a pending_bill for the shortfall but only creates/links a client master "
            "row on some paths; every ledger and payables view then joins on client master and drops the bill.",
      tests="R2-R08, R2-R11"),
 dict(id="F-R2-07", severity="MEDIUM", status="NEW",
      title="Cancelling a booking rewrites the booking total to zero and deletes its item lines, so the order "
            "content survives only in the ledger text",
      module="Bookings / Cancel",
      pages="POST /client_booking_cancel/<client_id>",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="Booking D: 100 kg 20mm at 150 = 15,000, paid in full in advance; bookings B and C undelivered.",
      steps="1) Cancel the client's remaining bookings.\n2) Inspect booking D and its booking_item rows.",
      expected="The cancellation is recorded without destroying what was ordered (or the original rows are archived).",
      actual="booking.amount 15,000 -> 0.00, is_void stays 0, and the booking_item rows are gone (1 -> 0). The "
             "activity log keeps an 'Edited Booking ... (1 field: amount) Rs 0' entry and the client ledger keeps "
             "'Booking Return/Cancel (R2 12mm Steel Bar)', so the material name survives but not the quantity or rate.",
      impact="A disputed cancellation cannot be reconstructed from the document; the money is right.",
      cause="The cancel plan rewrites the booking to its undelivered remainder (0) and drops the item lines instead "
            "of flagging the booking cancelled.",
      tests="R2-B09"),
 dict(id="F-R2-08", severity="MEDIUM", status="NEW",
      title="A non-numeric opening balance is accepted silently and stored as zero, with a success message",
      module="Masters / Clients",
      pages="POST /add_client",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="None.",
      steps="1) Create a client with opening_balance='abc'.\n2) Read the client row and the flash.",
      expected="Refused: an opening balance is money and cannot be skipped silently.",
      actual="Client created, opening_balance 0.0, flash 'Client Registered'. Nothing tells the operator the "
             "5,000/10,000 they typed was dropped.",
      impact="Opening balances are the only way to bring a running book into the system; a silently zeroed balance "
             "understates receivables from day one.",
      cause="The client form coerces with a numeric parse that falls back to 0 instead of raising.",
      tests="R2-M05"),
 dict(id="F-R2-09", severity="MEDIUM", status="NEW",
      title="The P&L workbook quotes a revenue figure that its own net profit is not computed from once an orphan "
            "return exists",
      module="Reports / P&L export",
      pages="GET /profit_reports/export/excel (Summary sheet)",
      env="Isolated copy of AMSV5-2_FIXED; after the return-against-voided-sale scenario.",
      precond="Same data as F-R2-02.",
      steps="1) Download the P&L workbook.\n2) Compare 'Earned Material Revenue (net of returns)' with "
            "'Net Profit / Loss' minus the COGS and expense lines.",
      expected="The Summary sheet adds up: revenue - COGS - operating expenses = net profit.",
      actual="Earned Material Revenue 35,800, COGS 25,468.75, Operating Expenses 3,500, reported net 10,031.25 — "
             "the sheet's own revenue line would give 6,831.25. The net is computed from 39,000; the 3,200 return "
             "is subtracted twice (once from revenue, once excluded as 'Unresolved Revenue Excluded From Net').",
      impact="Two revenue figures in one workbook; a reader who starts from the revenue line cannot reproduce the "
             "profit.",
      cause="The orphan return (F-R2-02) has no sale to reverse, so the export classifies it as unresolved revenue "
            "but the net-profit engine still uses the gross base.",
      tests="R2-R06"),
 dict(id="F-R2-10", severity="MEDIUM", status="NEW",
      title="No screen in the application reports the value of stock on hand",
      module="Inventory / Reporting",
      pages="/stock_summary, /material_ledger, /profit_reports",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="1,400 kg of 12mm bought in two batches (101.875 and 120.00 per kg landed).",
      steps="1) Look for a closing-stock or inventory-value figure on every inventory and report screen.",
      expected="A trading shop can read what its stock is worth at cost.",
      actual="Stock Summary shows quantities only; the material ledger shows movements; the P&L is COGS-only. The "
             "149,875 of landed inventory is nowhere on any screen (material.unit_price is a sale price and stays 0).",
      impact="Buying and pricing decisions run blind on inventory value; no balance-sheet-style check of stock "
             "against cash is possible.",
      cause="Only quantities are projected; the landed cost per grn_item is used for COGS at delivery time and is "
            "never aggregated into a stock valuation.",
      tests="R2-P03"),
 dict(id="F-R2-11", severity="MEDIUM", status="NEW",
      title="The dashboard's 'Total Outstanding — real dues across open bills' reads zero while three bills worth "
            "17,500 are open",
      module="Dashboard",
      pages="/ (Dashboard), /current_payables",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="AC01 has two open booking bills (11,500 + 3,000) and a 15,000 unallocated advance; a walk-in bill "
              "of 3,000 has no client.",
      steps="1) Read the Total Outstanding tile.\n2) Read /current_payables and the pending-bill table.",
      expected="The tile either shows the collectable amount or says plainly that it is a net ledger balance.",
      actual="Tile 0.00; /current_payables 0.00 / 0 clients; pending_bill holds 17,500 across 3 unpaid bills and "
             "the system report counts them ('3 Unpaid Bills').",
      impact="The owner sees 'nothing to collect' while 17,500 is open. Unallocated advances hide real bills and "
             "the ownerless walk-in bill is invisible everywhere.",
      cause="The tile is the sum of client ledger net balances (debit side only); advances are not applied to bills "
            "and bills without a client master are not in any ledger.",
      tests="R2-R11, R2-R08"),
 dict(id="F-R2-12", severity="LOW", status="NEW",
      title="Booking-delivery cost of goods is labelled 'Estimated Material Cost' even though the delivery line "
            "carries the frozen landed cost",
      module="Reports / P&L",
      pages="/profit_reports, /profit_reports/export/excel",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="100 kg of 12mm delivered against a booking, cost_rate_at_sale = 101.875 on the delivery line.",
      steps="1) Read the P&L export lines 'Actual Recorded Material Cost' and 'Estimated Material Cost'.",
      expected="Cost taken from a frozen cost_rate_at_sale counts as recorded, not estimated.",
      actual="15,281.25 recorded / 10,187.50 estimated: the 10,187.50 is exactly 100 x 101.875, i.e. the frozen cost. "
             "The page also says '2 estimated-cost rows'.",
      impact="Cosmetic but it undermines trust in the profit figure: a reader is warned that half the cost of goods "
             "is a guess when it is not.",
      cause="The estimator flag is set for lines whose source document is a booking delivery instead of being "
            "driven by the presence of cost_rate_at_sale.",
      tests="R2-P03, R2-R04"),
 dict(id="F-R2-13", severity="INFO", status="NEW",
      title="PDF exports need the optional requirements-pdf.txt; the shipped environment lacks it and the screen "
            "degrades with a clear message",
      module="Exports",
      pages="/profit_reports/export/pdf, ledger PDF buttons",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="requirements-lock.txt installed only.",
      steps="1) Download the P&L PDF.\n2) Install requirements-pdf.txt and repeat.",
      expected="Either the exports work out of the box or the deployment notes say they need the extra file.",
      actual="Before: 302 + 'PDF export is unavailable. Install requirements-pdf.txt and restart the app.' After "
             "installing: 200, application/pdf, 61,308 bytes starting %PDF. Excel and the ledger CSVs work either way.",
      impact="Deployment gap only; the app never pretends the file was produced.",
      cause="Optional dependency, correctly detected at runtime.",
      tests="R2-X06"),
 dict(id="F-R2-14", severity="INFO", status="NEW",
      title="Cancelling a booking is client-wide (LIFO), not per booking",
      module="Bookings / Cancel",
      pages="POST /client_booking_cancel/<client_id>",
      env="Isolated copy of AMSV5-2_FIXED.",
      precond="Client AC01 with three undelivered bookings (B, C, D).",
      steps="1) Post the cancel for client AC01.\n2) Count the cancelled bookings.",
      expected="Either one booking or all of them — as long as the screen says which.",
      actual="All three went at once: flash 'Total cancelled: 200.00 items, value 30000.00'.",
      impact="The page already previews 'Cancel Remaining Booking (LIFO)', so this is documented behaviour; it is "
             "recorded because a per-booking cancel does not exist.",
      cause="The route is client-scoped by design.",
      tests="R2-B07"),
]

with open(f"{AUDIT}/BUG_REGISTER_{DATE}_R2.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["Bug ID", "Title", "Severity", "Status", "Module", "Pages / routes", "Environment",
                "Preconditions / test data", "Reproduction steps", "Expected result", "Actual result (evidence)",
                "Impact", "Suspected cause", "Verifying tests"])
    for f in FINDINGS:
        w.writerow([f["id"], f["title"], f["severity"], f["status"], f["module"], f["pages"], f["env"],
                    f["precond"], f["steps"], f["expected"], f["actual"], f["impact"], f["cause"], f["tests"]])

# ---------------------------------------------------------------- FINANCIAL_RECONCILIATION
def num(text, key):
    if key not in text:
        return ""
    part = text.split(key, 1)[1]
    return part.split(";")[0].strip()


recon = []
for tid, label, screen_src in [
    ("R2-R01", "Gross sales (P&L screen vs database)", "screen"),
    ("R2-R02", "Discounts given", "screen"),
    ("R2-R03", "Net revenue", "screen"),
    ("R2-R04", "COGS (frozen landed cost of delivered lines)", "screen"),
    ("R2-R05", "Net profit", "screen"),
]:
    r = by_id[tid]
    recon.append([tid, label, num(r["actual"], "database="), num(r["actual"], "screen="),
                  "0.00", r["verdict"], r["actual"]])
r7 = by_id["R2-R07"]
recon.append(["R2-R07", "Cash: opening + receipts - payments = account balances",
              num(r7["actual"], "= ", ), num(r7["actual"], "closing cash "),
              "0.00", r7["verdict"], r7["actual"]])
r8 = by_id["R2-R08"]
recon.append(["R2-R08", "Open bills vs client masters (ownership)",
              num(r8["actual"], "open bills worth "), num(r8["actual"], "/current_payables total="),
              "3,000.00", r8["verdict"], r8["actual"]])
r9 = by_id["R2-R09"]
recon.append(["R2-R09", "Stock: material.total vs IN - OUT entries", "movement history",
              "material.total", "0.00", r9["verdict"], r9["actual"]])
r9b = by_id["R2-R09b"]
recon.append(["R2-R09b", "Payables: report total = supplier material + recipient charges",
              num(r9b["actual"], "report total="), num(r9b["actual"], "supplier payables="),
              "+7,000.00 (recipient charges)", r9b["verdict"], r9b["actual"]])
r11 = by_id["R2-R11"]
recon.append(["R2-R11", "Dashboard outstanding/undelivered vs ledgers",
              num(r11["actual"], "dashboard Total Outstanding="),
              num(r11["actual"], "ledger debit balances sum="), "17,500.00 (open bills)", r11["verdict"],
              r11["actual"]])
c8 = by_id["R2-C08"]
recon.append(["R2-C08", "Every account balance rebuilt from its own transactions",
              "account.balance", "opening + in - out", "0.00", c8["verdict"], c8["actual"]])
for tid in ("R2-A01", "R2-A02", "R2-P01", "R2-REG-02", "R2-S06", "R2-S07"):
    r = by_id.get(tid)
    if r:
        recon.append([tid, r["scenario"][:90], "", "", "", r["verdict"], r["actual"]])

with open(f"{AUDIT}/FINANCIAL_RECONCILIATION_{DATE}_R2.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["Check ID", "What is being reconciled", "Source A", "Source B", "Difference",
                "Verdict", "Evidence"])
    w.writerows(recon)

# ---------------------------------------------------------------- ROUTE_INVENTORY
src = f"{AUDIT}/evidence/routes_all.csv"
if os.path.exists(src):
    with open(src) as fh:
        body = fh.read()
    with open(f"{AUDIT}/ROUTE_INVENTORY_{DATE}_R2.csv", "w") as fh:
        fh.write(body)

print("deliverable CSVs written")
print("tests:", len(rows), "| findings:", len(FINDINGS),
      "| severities:", {s: sum(1 for f in FINDINGS if f["severity"] == s)
                        for s in ("HIGH", "MEDIUM", "LOW", "INFO")})
