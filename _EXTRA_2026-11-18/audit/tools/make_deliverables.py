"""Generate the audit CSV deliverables from the recorded evidence."""
import csv
import json
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H

OUT = "/home/user/AUDIT_2026-11-18"
DATE = "2026-11-18"

# ------------------------------------------------------------------ tests
tests = []
with open(f"{OUT}/evidence/results_phases_1_6.json") as fh:
    tests.extend(json.load(fh))
with open(f"{OUT}/evidence/results.json") as fh:
    tests.extend(json.load(fh))
print("recorded tests:", len(tests))

def verdict_of(tid):
    for e in tests:
        if e["id"] == tid:
            return e["verdict"]
    return "NOT TESTED"

with open(f"{OUT}/TEST_RESULTS_{DATE}.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["Test ID", "Area", "Route / Screen", "Scenario", "Expected result",
                "Actual result", "Verdict", "Severity if fail", "Evidence", "Note", "Audit date"])
    for e in tests:
        w.writerow([e["id"], e["module"], e["page"], e["scenario"], e["expected"],
                    e["actual"], e["verdict"], e.get("severity") or "", e.get("evidence") or "",
                    e.get("note") or "", "2026-11-18"])

# ------------------------------------------------------------------ bug register
bugs = [
 # id, title, module, severity, category, location, steps, expected, actual, evidence, impact, root cause, recommendation, status, test
 ("F-01", "Material-return credit settles the client's OLDEST open bill, not the bill the goods came from",
  "Sales / Returns / Receivable", "HIGH", "Accounting defect",
  "POST /add_material_return -> payment(payment_type='Material Return'); receivable projection app/services/financial_ledgers.py",
  "1) Credit sale A 10x150 = 1,500 (SB-SL-1004). 2) Credit sale B 10x160 = 1,600 (SB-SL-1005). "
  "3) POST /add_material_return 10 kg @160 = 1,600 for the same client.",
  "The 1,600 credit note reduces bill B (-> 0) and leaves bill A at 1,500.",
  "Bill B stayed 1,600 and bill A stayed 1,500; an unrelated older bill (SB-SL-1001) absorbed the whole 1,600 (5,722 -> 4,122).",
  "pending_bill before/after captured in evidence/results.json (T-RET-06); T-RET-05 shows the same FIFO behaviour.",
  "Per-invoice balances are wrong after any return: one customer bill reads as paid while the returned invoice reads as unpaid. "
  "Customer statements, 'unpaid bills' lists and dunning all point at the wrong document.",
  "MaterialReturn creates a Payment row with no reference to the sale/bill it returns; the ledger projection allocates "
  "receipts and credit notes oldest-first (FIFO) across the client's open bills.",
  "Store the returned sale_id (and line) on material_return and settle that bill first; only spill the remainder FIFO. "
  "Show the settlement target in the return form before saving.", "CONFIRMED", "T-RET-05, T-RET-06"),

 ("F-02", "Dashboard 'Daily Due Amount' drops the value of a delivered booking",
  "Dashboard / Receivables", "HIGH", "Reporting defect",
  "app/services/financial_ledgers.py::due_on_delivered_goods (lines ~1281-1291); tile templates/index.html:50",
  "Client AC01: booking 16,000 with 5,000 advance, fully delivered; plus two undelivered bookings (15,000 each).",
  "Collectable due includes the delivered booking: delivered charge 44,000/47,100 - money in = 29,000/31,322.",
  "Dashboard showed Rs 13,000 (later Rs 15,322): the delivered booking contributed 0 to 'charge' because "
  "max(0, booking.amount - TOTAL undelivered for the client) = max(0, 16,000 - 30,000) = 0, while its 5,000 advance "
  "still counted as money already in.",
  "due_on_delivered_goods() returned delivered=13,000 with by_client {'2': {'charge': 28,000, 'paid': 15,000}} while the "
  "client ledger showed 44,000 due and 11,000 of delivered booking still owed.",
  "The home screen understates the money collectable today by the full value of every delivered-but-unpaid booking of any "
  "client that also holds an undelivered booking (16,000 short in this scenario).",
  "A client-level 'undelivered' figure is subtracted per booking row instead of a per-booking one; "
  "the clamp then swallows delivered value.",
  "Compute delivered value per booking (booked - delivered for THAT booking) and clamp per booking, not against the "
  "client's total undelivered balance.", "CONFIRMED", "F-DUE-01"),

 ("F-03", "Dashboard 'Undelivered Bookings' tile multiplies the client's undelivered value by the number of bookings",
  "Dashboard", "HIGH", "Reporting defect",
  "app/services/financial_ledgers.py::due_on_delivered_goods (undelivered_total += per booking)",
  "Three bookings for one client (16,000 delivered, 15,000 + 15,000 undelivered = 30,000).",
  "Tile shows Rs 30,000 - what the client ledger shows.",
  "Tile showed Rs 90,000 (3 x 30,000).",
  "'Undelivered Bookings Rs. 90,000' on / vs 'Undelivered Booking 30,000.00' on /client_ledger/2 for the same client.",
  "A headline dashboard figure is 3x too high for a 3-booking client and grows linearly with the number of bookings - "
  "it is not the value of undelivered goods at all.",
  "_booking_undelivered_value(client_name) returns the client's TOTAL undelivered value and is accumulated once per "
  "booking row.",
  "Accumulate the client's undelivered value once per client (or per booking), never once per booking with a "
  "client-level value.", "CONFIRMED", "F-UND-01"),

 ("F-04", "'Total Outstanding' / 'Current Payables' counts undelivered booking balances that the client ledger excludes",
  "Dashboard / Client ledger / Current Payables", "HIGH", "Accounting-policy inconsistency (cross-screen)",
  "app/services/financial_ledgers.py::build_current_payables; /current_payables; /client_ledger/<id>; dashboard tile",
  "Client AC01 with 30,000 of undelivered bookings and 18,222 of real dues.",
  "One answer for 'how much do these customers owe', consistent everywhere.",
  "Client ledger: Net Balance 18,222 (+ 30,000 shown separately as not in the net). Current Payables / Total Outstanding: "
  "48,222. Dashboard daily due: 15,322. Three figures for the same question on the same day.",
  "18,222 + 30,000 = 48,222 exactly; /current_payables caption says 'including partial payments, returns, credits and "
  "adjustments' with no mention of undelivered bookings.",
  "Working-capital reporting overstates receivables by the whole undelivered booking book (30,000 = 163% of the real "
  "18,222 in this scenario). Chasing customers for goods never delivered becomes possible.",
  "build_current_payables sums PendingBill/ledger balances without excluding rows whose transaction_type is 'Booking' and "
  "whose goods are undelivered, while the client ledger applies the booking plan.",
  "Decide the policy (recommended: undelivered bookings are not receivables) and apply the client ledger's booking plan "
  "inside build_current_payables, or add a clearly separated 'booking commitments' column on every screen that shows it.",
  "CONFIRMED (policy ambiguity + inconsistency)", "T-BOOK-03"),

 ("F-05", "Daily Breakdown 'Payments' tile includes material-return credit notes",
  "Inventory / Daily Breakdown", "HIGH", "Reporting defect",
  "/inventory/daily_transactions tile 'Payments'",
  "Returns totalling 13,100 (credit notes, no cash) plus receipts of 10,778 on the same day.",
  "The money tile shows cash actually received (10,778) or labels the credit notes separately.",
  "Tile showed Rs 23,878 = 10,778 receipts + 13,100 return credits, labelled simply 'Payments'.",
  "Captured text: 'Stock In Qty 1,990.00 ... Payments Rs. 23,878.00 Included'.",
  "The day's collection figure is overstated by 121% in this scenario whenever returns happen; a shop reconciling the "
  "drawer against this screen will hunt for a cash shortage.",
  "The tile sums Payment rows without applying the cash-receipt filter used by the dashboard (/ already has "
  "_is_cash_receipt()).",
  "Reuse the dashboard's cash_receipt_components() filter and show return credits in their own column.",
  "CONFIRMED", "T-DAY-01"),

 ("F-06", "No CSRF protection: a money-moving POST is accepted with no token",
  "Security / all POST forms", "HIGH", "Security defect",
  "global (Flask app); /add_payment, /add_direct_sale, /add_material_return, /grn, /accounts/pay_grn/save, /void_transaction/*",
  "POST /add_payment with client_code, amount and method but no CSRF token field at all, using the logged-in session.",
  "Rejected - 400/403 'CSRF token missing or incorrect'.",
  "Accepted (HTTP 200, redirect to the payments page) and a Rs 1 receipt was created; it reduced the client's oldest "
  "open bill from 15,200 to 15,199 and moved the cash account from 49,500 to 49,501.",
  "Payment id 4, note empty, amount 1; /financial_ledger/2 Total Paid/Credit rose by 1. Forms do render a hidden "
  "_csrf_token field, which suggests the intent exists but nothing validates it.",
  "Any page the victim visits can silently create sales, receipts, returns, payments to suppliers or delete bills in the "
  "victim's name. On a shop LAN or an internet-facing deployment this is a direct path to fabricated financial records.",
  "No CSRFProtect/Flask-WTF initialisation and no before_request token check; the templated hidden field is decorative.",
  "Enable Flask-WTF CSRFProtect app-wide, validate on every mutating route, and exempt only documented API endpoints "
  "that carry their own token.", "CONFIRMED", "T-CSRF-01"),

 ("F-07", "'Void transaction' performs a hard DELETE of the posted document",
  "Sales / Bills", "HIGH", "Data safety / audit trail",
  "POST /void_transaction/<type>/<id> -> app/blueprints/sales/_bills_void_transaction.py",
  "Void the credit sale SB-SL-1003 (50 kg 20mm, Rs 7,250) via /void_transaction/DirectSale/4.",
  "Document retained and flagged void (is_void=1) with a reversal record, or an explicit delete that keeps a full copy "
  "of the document.",
  "Flash said 'DirectSale deleted'; the direct_sale row, its items, its stock entry (entry id 8) and its pending bill "
  "were physically removed. Only a text line survives: audit_log 'transaction.delete.DirectSale id=4, reason=audit void test' "
  "and activity_event 'Deleted DirectSale \"SB-SL-1003\" Rs 7,250'.",
  "SELECT * FROM direct_sale WHERE id=4 -> empty; entry id 8 missing from the sequence; stock restored 280 -> 330 correctly.",
  "Paper trail destroyed: the customer's bill number, its line detail and its cost basis no longer exist; the figures can "
  "never be re-derived or re-printed. The model already has is_void columns that are bypassed. Two different actions "
  "(void, delete) resolve to the same destructive call, with no confirmation step and no reason field requirement.",
  "The void route is wired to the delete service instead of a status flip plus reversal entries.",
  "Make void set is_void with reversal entries and keep the row; reserve hard delete for pre-posting drafts and require "
  "a typed confirmation plus a mandatory reason.", "CONFIRMED", "T-VOID-01"),

 ("F-08", "A cash sale cannot be returned: the return form requires a registered client",
  "Sales / Returns", "MEDIUM", "Functional gap",
  "POST /add_material_return (validation)",
  "Return 20 kg of a sale that was recorded under 'Cash Customer' (no client master).",
  "Either the return is accepted against the walk-in bill, or the UI explains that a client master is required and "
  "offers to create one.",
  "Rejected: 'Select a valid active client from the list.' There is no route for the shop's most common transaction.",
  "Flash message captured in evidence/results.json (T-RET-01, T-RET-02); the direct-sale form happily accepts "
  "'Cash Customer'.",
  "Walk-in returns are pushed outside the system - stock stays in the ledger as sold and the refund is either not "
  "recorded or recorded as a fake client sale.",
  "Return validation resolves client_code against the Client master, while cash sales intentionally store a free-text "
  "'Cash Customer'.",
  "Support a 'walk-in / cash customer' pseudo-client for returns, or make the cash-sale form create a one-off client "
  "record.", "CONFIRMED", "T-RET-01, T-RET-02"),

 ("F-09", "GRN flash says 'No charges on this GRN' after charges were saved and capitalised",
  "Purchases / GRN", "MEDIUM", "Message defect (misleading)",
  "POST /grn with charge_*[] grid",
  "GRN 2: 400 x 12mm @110 = 44,000 plus 5,000 freight capitalised into the item cost.",
  "Success message reflecting the saved charges.",
  "Charges were saved (grn_charge row, landed cost 49,000 -> 122.50/unit) but the flash read "
  "'No charges on this GRN'.",
  "evidence/results.json (T-GRN-02); material landed cost in grn_costing is 122.50 and the sale COGS used 122.50.",
  "The operator is told the opposite of what happened and will re-enter the charges, double-capitalising them.",
  "The flash is built from the legacy loading_cost/freight_cost/other_expense form fields, which are ignored by the "
  "charge-grid path.",
  "Derive the message from the number of grn_charge rows actually written.", "CONFIRMED", "T-GRN-02"),

 ("F-10", "Integrity Audit raises a false positive on its own booking-delivery flow and misses the real inconsistencies",
  "System / Integrity Audit", "MEDIUM", "Diagnostic quality",
  "/system_report",
  "Run the audit with one delivered booking and no missing documents.",
  "No sync issues on a clean, correctly-booked dataset.",
  "'Missing Pending Bill - Entry #7: Bill SB-SL-1002 for AUDIT-CLI-01 (AC01) is missing from Pending Bills' - but "
  "SB-SL-1002 is the booking-delivery sale, which is by design carried by SB-BK-1000.",
  "system_report text captured; the same run reported 'No stock discrepancies found' while the dashboard was "
  "simultaneously reporting 90,000 of undelivered bookings and 15,322 of due against a true 30,000/31,322.",
  "The one screen meant to catch bookkeeping errors cries wolf on normal data and stays silent on the three real "
  "receivable inconsistencies (F-02, F-03, F-04). Operators learn to ignore it.",
  "The check compares every DirectSale with category 'Booking Delivery' against pending_bill.source_table='direct_sale' "
  "without allowing the booking's own bill; it has no cross-report assertions at all.",
  "Allow booking-delivery sales to be covered by their booking bill, and add reconciliation assertions "
  "(ledger net vs payables, dashboard tiles vs ledger) to the audit.", "CONFIRMED", "T-INT-01"),

 ("F-11", "Booking-delivery sales store amount 0 and no cost rate - revenue and cost exist only in the booking",
  "Sales / Bookings", "MEDIUM", "Data-quality / audit-trail",
  "POST /add_direct_sale (category='Booking Delivery') -> direct_sale / direct_sale_item",
  "Deliver 100 x 12mm against booking SB-BK-1000 (16,000, 5,000 advance).",
  "The delivery document carries the value delivered (11,000) and its FIFO cost, and closes the booking receivable.",
  "DirectSale amount 0.00, item price 0.00, cost_rate_at_sale NULL; the booking's 11,000 receivable stayed open; the "
  "profit report shows '1 estimated-cost row'.",
  "direct_sale id 3 amount 0.0; direct_sale_item id 3 price_at_time 0.0, cost_rate_at_sale None; pending_bill SB-BK-1000 "
  "still 11,000 after full delivery.",
  "Delivered value and cost are not attached to the delivery document, so any per-bill margin, per-document cost audit "
  "or re-costing exercise is impossible for booked sales; the profit report has to guess one side of the entry.",
  "The booking-delivery branch books a zero-value sale and leaves valuation to the booking and to a live cost estimate.",
  "Write the delivered value and the period FIFO cost onto the delivery sale (and its item), and settle the booking "
  "receivable on delivery.", "CONFIRMED", "T-BOOK-02"),

 ("F-12", "Purchase (GRN) stock movements carry no source linkage",
  "Inventory / traceability", "MEDIUM", "Traceability gap",
  "entry rows written by POST /grn",
  "Inspect every stock movement's source columns.",
  "Every stock movement names the document that created it (module/table/id) like the sales entries do.",
  "4 of 9 entries have source_module/source_table/source_id = NULL: the 3 GRN receipt lines and 1 other. "
  "Sales and return entries are linked.",
  "SELECT COUNT(*) FROM entry WHERE source_module IS NULL -> 4.",
  "Stock cannot be traced back to the receiving document from the ledger, which weakens any 'where did this stock come "
  "from' investigation and blocks automated re-costing.",
  "The GRN save path still writes bare Entry rows instead of the linked-entry helper used by sales/returns.",
  "Use the same linked-entry service for GRN receipts.", "CONFIRMED", "T-INT-02"),

 ("F-13", "A fully paid cash sale still creates a zero-amount pending bill",
  "Sales / Receivable", "LOW", "Data hygiene",
  "POST /add_direct_sale (category='Cash', paid in full)",
  "Cash sale 300 kg @150 = 45,000 with 500 discount, paid 44,500 in full.",
  "No receivable row is created for a sale settled at the counter.",
  "pending_bill row SB-SL-1000, amount 0.0, is_paid 1, is_cash 1 exists and appears in bill lists with a zero balance.",
  "pending_bill id 1: bill_no SB-SL-1000 amount 0.0 is_paid 1.",
  "Noise in bill lists, exports and any downstream 'all bills' consumer; harmless to the totals because it is flagged paid.",
  "The sale writer always mirrors the source document into pending_bill regardless of settlement.",
  "Skip the pending_bill row when the balance is zero and the sale is cash-settled (or keep it but filter it out of "
  "customer-facing lists).", "CONFIRMED", "T-SALE-01"),

 ("F-14", "Dashboard 'Daily Cash Received' label does not say it excludes separately-posted client receipts",
  "Dashboard", "LOW", "Presentation / policy clarity",
  "templates/index.html:40-58, cash_receipt_components()",
  "Day with 44,500 of cash sales, a 5,000 booking advance and a 10,000 client receipt.",
  "A tile labelled 'Daily Cash Received' equals the cash the shop took today (60,278 in the ledger).",
  "Tile shows 49,500 with a sub-caption '(plus Rs. 10,000 receipts = Rs. 59,500 in the cash ledger)'; the number is "
  "trading cash only and coincides with the closing balance, which invites the wrong reading.",
  "Captured dashboard text; /cash_flow shows Total Received 60,278 for the same day.",
  "Two plausible but different 'cash today' figures on adjacent screens; the shop can mis-bank and mis-reconcile.",
  "A deliberate product decision (documented in the code comment) is not carried into the visible label.",
  "Rename to 'Cash on today's trading documents' or show both figures in one tile with explicit sub-labels.",
  "CONFIRMED", "T-DAY-02"),

 ("F-15", "Legacy GRN cost fields are collected and silently ignored",
  "Purchases / GRN", "INFO", "Dead configuration / policy ambiguity",
  "app/services/grn_costing.py::sync_charges_from_form",
  "Post a GRN with loading_cost / freight_cost / other_expense only (no charge_*[] grid).",
  "Either the fields feed the landed cost or the form does not offer them.",
  "The values are parsed and discarded; only charge_*[] rows reach grn_charge and the landed cost.",
  "T-GRN-01/02 behaviour; see grn_costing.py CHARGE_TYPES handling.",
  "An operator (or an old integration/import CSV) using the legacy fields understates landed cost and overstates "
  "margin without any error.",
  "Two cost-entry mechanisms coexist; the legacy one was replaced but left enabled.",
  "Remove the legacy fields or translate them into charge rows on save.", "CONFIRMED (code reading + test)", "T-GRN-01"),
]

with open(f"{OUT}/BUG_REGISTER_{DATE}.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["Bug ID", "Title", "Module", "Severity", "Category", "Location (route / file)",
                "Reproduction steps", "Expected", "Actual", "Evidence", "Business / financial impact",
                "Root cause", "Recommended remedy (not applied - audit only)", "Status", "Test ID"])
    for b in bugs:
        w.writerow(list(b))
print("bugs written:", len(bugs))

# ------------------------------------------------------------------ regression recheck
regress = [
 ("B-01", "Profit report arithmetic on the day's trading", "PASS",
  "Gross 86,200 - discounts 500 = net revenue 85,700; COGS 61,600 (FIFO on landed cost, including the 5,000 capitalised "
  "freight); net profit 24,100 = 85,700 - 61,600; margin 28.12%. Recomputed independently from grn_item landed rates and "
  "sale lines: identical to the cent.", "T-REP-01"),
 ("B-02", "Return reverses cost and stock exactly once", "PASS",
  "20 kg @140 return reversed exactly 2,400 of COGS, restored 20 kg to stock (300 -> 320) and did not touch cash.", "T-RET-03"),
 ("B-03", "Dashboard separates undelivered booking balances from collectable due", "FAIL",
  "Daily Due is 16,000 short (13,000 shown vs 29,000 due) because a delivered booking is clamped against the client's "
  "total undelivered value; the Undelivered Bookings tile reads 90,000 against a true 30,000. See F-02, F-03.", "F-DUE-01, F-UND-01"),
 ("B-04", "Credit note links to the original bill and does not distort unrelated receivables", "FAIL",
  "No link exists. A 1,600 return for bill B was posted to bill A (5,722 -> 4,122); B stayed 1,600. See F-01.", "T-RET-05, T-RET-06"),
 ("B-05", "Cash (walk-in) sale can be returned", "FAIL",
  "Blocked by client validation: 'Select a valid active client from the list.' See F-08.", "T-RET-01, T-RET-02"),
 ("B-06", "Client ledger shows advance, delivered value, receipt and undelivered balance without double counting",
  "PARTIAL",
  "The client ledger itself is right: Total Due (Debit) 47,100 = credit sales + delivered booking value; Total "
  "Paid/Credit 28,878 = advances + receipts + return credits; net 18,222; undelivered 30,000 shown separately and "
  "excluded from the net. But Current Payables (48,222) and the dashboard disagree with it - see F-04.", "T-BOOK-03, F-04"),
 ("B-07", "Daily Breakdown classifies receipts, cash sales and return credits correctly", "FAIL",
  "The Payments tile counts 13,100 of return credit notes as payments (23,878 vs 10,778 cash). See F-05.", "T-DAY-01"),
 ("B-08", "Integrity Audit reports relevant inconsistencies instead of a false success", "PARTIAL",
  "It does report (does not claim clean) but its only finding is a false positive on the app's own booking-delivery "
  "flow, and it misses the three real receivable inconsistencies. See F-10.", "T-INT-01"),
 ("B-09", "Cash flow ties to the cash ledger and the supplier payment is not expensed twice", "PASS",
  "Opening 50,000 + received 60,278 - spent 60,000 = closing 50,278, equal to the cash account balance; the 60,000 GRN "
  "payment appears once, as a payment, and never as an expense - COGS comes only from landed GRN cost.", "T-REP-02"),
]
with open(f"{OUT}/REGRESSION_RECHECK_{DATE}.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["Bug ID", "Claim under recheck", "Verdict", "Evidence", "Test / finding"])
    w.writerows(regress)

# ------------------------------------------------------------------ financial reconciliation
recon = [
 ("Gross sales", "Profit Reports", "9,2,100 sale documents + delivered booking value - 13,100 returns", 79_000, 79_000, 0,
  "PASS", "Gross sales are stated net of returns, as the earlier 2,800-return snapshot also showed (86,200 = 89,000 - 2,800)."),
 ("Discounts", "Profit Reports", "sum(direct_sale.discount)", 500, 500, 0, "PASS", ""),
 ("Net revenue", "Profit Reports", "gross - discounts", 78_500, 78_500, 0, "PASS", ""),
 ("COGS", "Profit Reports", "FIFO landed cost of items sold - cost of items returned (computed from grn_item landed rates)",
  55_600, 55_600, 0, "PASS", "Includes the 5,000 capitalised freight at 122.50/unit and the 10,000 estimated cost of the "
  "booking delivery (F-11)."),
 ("Net profit", "Profit Reports", "net revenue - COGS", 22_900, 22_900, 0, "PASS", ""),
 ("Money received", "Profit Reports", "sale cash + advances + receipts (cash only)", 60_278, 60_278, 0, "PASS",
  "Includes the Rs 1 CSRF probe receipt and the 777 duplicate-test receipt; return credits correctly excluded."),
 ("Cash closing", "Cash Flow / Accounts", "account.balance", 50_278, 50_278, 0, "PASS",
  "Opening 50,000 + 60,278 in - 60,000 out."),
 ("Total received (IN)", "Cash Flow", "sum of cash receipts into the account", 60_278, 60_278, 0, "PASS",
  "Return credits are not in the cash account - correct."),
 ("Total spent (OUT)", "Cash Flow", "supplier payment", 60_000, 60_000, 0, "PASS", "Single payment, no double count (B-09)."),
 ("Supplier closing", "Supplier Ledger", "GRN credits 209,000 - payments 60,000", 149_000, 149_000, 0, "PASS",
  "GRNs never self-pay: paid_amount forced 0, bill_ref SB-GRN-1000/1001."),
 ("Stock in / out / net", "Stock Summary", "entries IN 1,990 - OUT 620 = 1,370 (unit-balance check)",
  1_370, 1_370, 0, "PASS", "Matches material.total: 12mm 1,000 + 20mm 370."),
 ("Customer receivable", "Client Ledger (AC01)", "credit sale 28,000 + 1,500 + 1,600 + delivered booking 16,000 - advances "
  "5,000 - receipts 10,778 - returns 13,100", 18_222, 18_222, 0, "PASS", "The ledger is internally correct."),
 ("Customer receivable", "Current Payables / Total Outstanding", "same client, excluding undelivered bookings",
  18_222, 48_222, +30_000, "FAIL", "F-04 - the whole undelivered booking book is counted as a receivable."),
 ("Collectable due today", "Dashboard 'Daily Due Amount'", "delivered charge 47,100 - money already in 15,778",
  31_322, 15_322, -16_000, "FAIL", "F-02 - exactly the delivered booking value is missing."),
 ("Undelivered bookings", "Dashboard tile", "client booking plan total", 30_000, 90_000, +60_000, "FAIL",
  "F-03 - client value counted once per booking."),
 ("Day's payments", "Daily Breakdown tile", "cash receipts only", 10_778, 23_878, +13_100, "FAIL",
  "F-05 - return credit notes counted as payments."),
 ("Day's cash received", "Dashboard tile", "cash in the ledger", 60_278, 49_500, -10_778, "INFO",
  "F-14 - trading cash only, labelled without the qualifier; the sub-caption does disclose it."),
]
with open(f"{OUT}/FINANCIAL_RECONCILIATION_{DATE}.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["#", "Figure", "Screen", "Independent expectation (A)", "Basis", "Observed (B)", "Variance (B-A)",
                "Verdict", "Note"])
    for i, r in enumerate(recon, 1):
        w.writerow([i, r[0], r[1], r[3], r[2], r[4], f"{r[5]:+,}", r[6], r[7]])

# ------------------------------------------------------------------ route inventory with coverage
tested = {
 "/": "T-DAY-02, F-DUE-01, F-UND-01", "/login": "T-AUTH-01 (anonymous redirect)",
 "/accounts/accounts/add": "T-MASTER-03", "/add_client": "T-MASTER-01", "/add_material": "T-MASTER-02",
 "/grn": "T-GRN-01, T-GRN-02, F-09, F-15", "/add_direct_sale": "T-SALE-01..04, T-BOOK-02, F-11, F-13",
 "/add_payment": "T-PAY-01..03, T-CSRF-01", "/add_material_return": "T-RET-01..06, F-01, F-08",
 "/add_booking": "T-BOOK-01, T-BOOK-03", "/void_transaction/<string:type>/<int:id>": "T-VOID-01, F-07",
 "/accounts/pay_grn/save": "T-PAY-01", "/accounts/api/pay_grn/<int:grn_id>": "T-PAY-01 (helper read)",
 "/profit_reports": "T-REP-01, B-01", "/cash_flow": "T-REP-02, B-09", "/current_payables": "F-04",
 "/client_ledger/<int:client_id>": "T-BOOK-03, B-06", "/supplier_ledger/<int:supplier_id>": "T-REP-03",
 "/inventory/stock_summary": "T-REP-04", "/inventory/daily_transactions": "T-DAY-01, F-05",
 "/system_report": "T-INT-01, F-10", "/financial_ledger/<int:client_id>": "T-BOOK-03",
 "/material_ledger/<int:material_id>": "read for reconciliation",
}
with open(f"{OUT}/evidence/routes_raw.csv") as fh:
    routes = list(csv.DictReader(fh))
with open(f"{OUT}/ROUTE_INVENTORY_{DATE}.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["#", "Module", "Endpoint", "URL rule", "Methods", "Mutating", "URL parameters",
                "Tested in this audit", "Evidence / test ID", "Coverage note"])
    for i, r in enumerate(routes, 1):
        url = r["url"]
        hit = tested.get(url)
        note = ""
        if hit:
            note = "exercised end-to-end through the real UI route"
        elif r["mutating"] == "yes":
            note = "NOT EXERCISED - destructive/administrative; deferred (see report part 10)"
        w.writerow([i, r["module"], r["endpoint"], url, r["methods"], r["mutating"], r["params"],
                    "yes" if hit else "no", hit or "", note])
print("routes written:", len(routes))
print("tested routes:", len(tested))
