"""S7 — independent reconciliation: rebuild every headline figure from the database
and compare it with what the screens report."""
import io
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L

c = H.client()
print("== S7 reconciliation ==")


def s(sql, p=()):
    return L.f(H.scalar(sql, p))


# ---------------------------------------------------------------- independent figures
# booking revenue counts only the part actually delivered: delivered qty per booking
# item is read straight from booking_allocation, not from the app's own helper
booked_delivered = 0.0
for item in H.q("SELECT id, qty, price_at_time, COALESCE(discount,0) AS discount FROM booking_item"):
    got = L.f(H.scalar("SELECT SUM(qty) FROM booking_allocation WHERE booking_item_id=? "
                       "AND COALESCE(is_void,0)=0", (item["id"],)))
    booked_delivered += min(got, L.f(item["qty"])) * L.f(item["price_at_time"])
gross_sales = s("SELECT SUM(amount) FROM direct_sale WHERE COALESCE(is_void,0)=0") + booked_delivered
discounts = s("SELECT SUM(discount) FROM direct_sale WHERE COALESCE(is_void,0)=0") + \
    s("SELECT SUM(discount) FROM booking WHERE COALESCE(is_void,0)=0")
net_rev = gross_sales - discounts
cogs = s("""SELECT SUM(i.qty * i.cost_rate_at_sale) FROM direct_sale_item i
            JOIN direct_sale d ON d.id = i.sale_id WHERE COALESCE(d.is_void,0)=0""")
grn_expense_paid = s("SELECT SUM(amount) FROM account_transaction WHERE transaction_type='Expense' "
                     "AND COALESCE(is_void,0)=0")
grn_charges_expense = 1000.0        # the LOADING charge with treatment EXPENSE on GRN-1 (paid in S3)
opex_total = grn_expense_paid + grn_charges_expense
net_profit = net_rev - cogs - opex_total          # <- the figure the P&L must show

cash_total = s("SELECT SUM(balance) FROM account WHERE COALESCE(is_active,1)=1")
receivable = L.open_receivable()

# ---------------------------------------------------------------- page figures
st, pnl = L.page("/profit_reports")
p_gross = L.money_in(pnl, "Gross sales")
p_disc = L.money_in(pnl, "Discounts given")
p_net = L.money_in(pnl, "Net revenue")
p_profit = L.money_in(pnl, "Net Profit / Loss for the selected period")
p_cogs = None
for label in ("COGS + Direct Service Cost", "COGS — Cost of Goods Sold", "COGS"):
    p_cogs = L.money_in(pnl, label)
    if p_cogs is not None:
        break

rows = [
    ("R2-R01", "Gross sales", gross_sales, p_gross),
    ("R2-R02", "Discounts", discounts, p_disc),
    ("R2-R03", "Net revenue", net_rev, p_net),
    ("R2-R04", "COGS (frozen landed cost of every delivered line)", cogs, p_cogs),
    ("R2-R05", "Net profit", net_profit, p_profit),
]
for tid, label, mine, page in rows:
    ok = page is not None and abs(mine - page) < 0.5
    L.check(tid, "Reconciliation / P&L", "/profit_reports",
            f"Rebuild {label} from the database and compare with the P&L screen",
            f"the screen shows the database figure ({mine:,.2f})",
            f"database={mine:,.2f}; screen={page}; difference="
            f"{(page - mine) if page is not None else 'n/a'}",
            ok, "HIGH")

# ---------------------------------------------------------------- P&L export cross-check
r = c.get("/profit_reports/export/excel")
try:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.get_data()), data_only=True)
    ws = wb["Summary"]
    summary = {row[0]: row[1] for row in ws.iter_rows(values_only=True) if row and row[0]}
    bridge = {row[0]: row[1] for row in wb["COGS Bridge"].iter_rows(values_only=True) if row and row[0]}
    rev_line = summary.get("Earned Material Revenue (net of returns)")
    nr_line = summary.get("Net Profit / Loss")
    cogs_line = summary.get("COGS — Cost of Goods Sold")
    excl = summary.get("Unresolved Revenue Excluded From Net")
    L.show("export summary", {k: summary[k] for k in
                              ("Earned Material Revenue (net of returns)", "Sales (Money In)",
                               "COGS — Cost of Goods Sold", "Operating Expenses",
                               "Unresolved Revenue Excluded From Net", "Net Profit / Loss")})
    internal = (rev_line is not None and nr_line is not None
                and abs((rev_line - cogs_line - opex_total) - nr_line) < 0.5)
    L.check("R2-R06", "Reconciliation / P&L export", "/profit_reports/export/excel",
            "Read the exported P&L workbook and check it is internally consistent: revenue minus COGS "
            "minus operating expenses must equal the net profit it reports",
            "the workbook's own revenue line leads to its own net-profit line",
            f"revenue line={rev_line}; COGS={cogs_line}; opex={opex_total}; "
            f"unresolved-excluded line={excl}; reported net={nr_line}; "
            f"revenue-COGS-opex={None if rev_line is None else rev_line - cogs_line - opex_total}",
            internal, "MEDIUM",
            evidence=f"COGS bridge sheet: {bridge}",
            note="The return taken against a voided sale leaves the workbook quoting 35,800 of earned "
                 "revenue while the net profit is computed from 39,000.")
except Exception as exc:
    L.check("R2-R06", "Reconciliation / P&L export", "/profit_reports/export/excel",
            "Read the exported P&L workbook", "workbook parses", f"error: {exc}", False, "MEDIUM")

# ---------------------------------------------------------------- cash
st, cf = L.page("/cash_flow")
cf_in = L.money_in(cf, "Total Received") or L.money_in(cf, "Money In")
cf_out = L.money_in(cf, "Spent") or L.money_in(cf, "Total Paid")
tx_in = s("SELECT SUM(amount) FROM account_transaction WHERE to_account_id IS NOT NULL "
          "AND COALESCE(is_void,0)=0 AND transaction_type <> 'Transfer'")
tx_out = s("SELECT SUM(amount) FROM account_transaction WHERE from_account_id IS NOT NULL "
           "AND COALESCE(is_void,0)=0 AND transaction_type <> 'Transfer'")
opening = s("SELECT SUM(opening_balance) FROM account WHERE COALESCE(is_active,1)=1")
L.check("R2-R07", "Reconciliation / Cash", "/cash_flow",
        "Compare the cash-flow screen totals with the account transactions and the closing cash",
        "money in, money out and closing cash all agree",
        f"screen in={cf_in} out={cf_out}; ledger in={tx_in} out={tx_out}; "
        f"openings={opening}; closing cash {cash_total}; assets - liabilities check: "
        f"{opening} + {tx_in} - {tx_out} = {opening + tx_in - tx_out}",
        abs((opening + tx_in - tx_out) - cash_total) < 0.5, "CRITICAL",
        evidence="account balances are rebuilt from opening + every non-void account_transaction")

# ---------------------------------------------------------------- receivables / payables
st, pay = L.page("/accounts/purchase_payables")
supplier_pay = L.money_in(pay, "Supplier payables")
total_pay = L.money_in(pay, "Total outstanding")
st2, cur = L.page("/current_payables")
client_due = L.money_in(cur, "Total Outstanding Payables")
st3, sysrep0 = L.page("/system_report")
unpaid_tile = None
import re as _re
m = _re.search(r"(\d+)\s+Unpaid Bills", sysrep0)
unpaid_tile = int(m.group(1)) if m else None

# every unpaid bill must belong to a client whose ledger can show it
orphans = []
for b in H.q("SELECT bill_no, client_name, client_code, amount FROM pending_bill "
             "WHERE COALESCE(is_paid,0)=0 AND COALESCE(is_void,0)=0"):
    owner = H.one("SELECT id, name FROM client WHERE lower(trim(name))=lower(trim(?)) "
                  "OR (code IS NOT NULL AND lower(trim(code))=lower(trim(?)))",
                  (b["client_name"] or "", b["client_code"] or ""))
    if not owner:
        orphans.append((b["bill_no"], b["client_name"], b["client_code"], b["amount"]))
L.check("R2-R08", "Reconciliation / Receivables", "/current_payables + /system_report",
        "Check that every unpaid bill has an owner that a client ledger can display",
        "each open bill belongs to an existing client, so the operator can chase it",
        f"open bills worth {receivable:,.2f}; bills with no client master row={orphans}; "
        f"/current_payables total={client_due}; system report 'Unpaid Bills' tile={unpaid_tile}",
        not orphans, "HIGH",
        evidence="A cash sale typed as a new walk-in name with too little money taken creates a "
                 "pending_bill row but no client record: the bill exists, the ledger cannot show it, "
                 "/current_payables lists 0 clients and the integrity report still counts the bill.")

# The supplier component must equal what the supplier ledgers actually say, and
# the report total must equal that component plus the unpaid third-party GRN
# charges.  Neither figure is hard-coded: the payments made by the scenarios
# change them (a cancelled GRN payment reopens the bill), so the check compares
# the page against the ledgers and the charge rows rather than a fixed number.
with H.app.app_context():
    from app.services.financial_ledgers import (
        build_supplier_financial_ledger, Supplier,
    )
    supplier_ledger_total = round(sum(
        max(0.0, float((build_supplier_financial_ledger(sup) or {}).get("closing_balance") or 0))
        for sup in Supplier.query.all()
    ), 2)
third_party_open = round(s(
    "SELECT SUM(c.amount) FROM grn_charge c JOIN grn g ON g.id=c.grn_id "
    "WHERE COALESCE(c.is_void,0)=0 AND COALESCE(g.is_void,0)=0 AND COALESCE(g.is_cancelled,0)=0 "
    "AND COALESCE(c.include_in_supplier,0)=0")
    - s("SELECT SUM(p.amount) FROM supplier_payment p JOIN grn_charge c ON c.id=p.grn_charge_id "
        "WHERE COALESCE(p.is_void,0)=0"), 2)
L.check("R2-R09b", "Reconciliation / Payables", "/accounts/purchase_payables",
        "Split the payables report between supplier payables and unpaid GRN expenses owed to third parties",
        "supplier payables = what the supplier ledgers say is still owed; the report total = that plus the "
        "unpaid third-party GRN charges",
        f"report total={total_pay}; supplier payables on the page={supplier_pay}; "
        f"supplier ledger balances={supplier_ledger_total}; unpaid third-party charges={third_party_open}",
        total_pay is not None and supplier_pay is not None
        and abs(supplier_pay - supplier_ledger_total) < 1.0
        and abs(total_pay - (supplier_pay + third_party_open)) < 1.0, "HIGH")

# ---------------------------------------------------------------- stock
st, stock_page = L.page("/stock_summary")
mismatch = []
for m in H.q("SELECT name, total FROM material ORDER BY id"):
    ins = s("SELECT SUM(qty) FROM entry WHERE material=? AND type='IN' AND COALESCE(is_void,0)=0", (m["name"],))
    outs = s("SELECT SUM(qty) FROM entry WHERE material=? AND type='OUT' AND COALESCE(is_void,0)=0", (m["name"],))
    if abs((ins - outs) - L.f(m["total"])) > 0.01:
        mismatch.append((m["name"], L.f(m["total"]), ins - outs))
L.check("R2-R09", "Reconciliation / Stock", "/stock_summary",
        "Rebuild every material balance from its IN/OUT movements and compare with material.total",
        "each stock figure equals its own movement history",
        f"mismatches={mismatch}; material totals={[(m['name'], m['total']) for m in H.q('SELECT name,total FROM material')]}",
        not mismatch, "CRITICAL")

# ---------------------------------------------------------------- system report
st, sysrep = L.page("/system_report")
integrity_ok = "integrity" not in sysrep.lower() or "ok" in sysrep.lower()
bad_words = [w for w in ("1 MISMATCH", "2 MISMATCH", "3 MISMATCH", "MISMATCHES)", "FAILED", "BROKEN")
             if w in sysrep.upper() and "0 MISMATCHES" not in sysrep.upper()]
L.check("R2-R10", "Reconciliation / System report", "/system_report",
        "Run the built-in integrity audit after the whole scenario chain",
        "no mismatch, no broken link, no orphan row reported",
        f"status={st}; alarming words on the page={bad_words}; "
        f"tiles: {_re.findall(r'(\d+)\s+(?:Sync Issues|Stock Discrepancies|Unpaid Bills|Zero-Amount Bills)', sysrep)}; "
        f"excerpt={sysrep[sysrep.find('Logout'):][:400]}",
        st == 200 and not bad_words, "CRITICAL")

# ---------------------------------------------------------------- dashboard
st, dash = L.page("/")
d_out = L.money_in(dash, "Net Client Dues") or L.money_in(dash, "Total Outstanding")
d_und = L.money_in(dash, "Undelivered Bookings")
d_cash = L.money_in(dash, "Cash on Today")
with H.app.app_context():
    import app.services.financial_ledgers as FL
    nets = [float(FL.build_client_financial_ledger(cl)["closing_balance"] or 0)
            for cl in FL.Client.query.all()]
ledger_pos = round(sum(n for n in nets if n > 0), 2)
undel_total = 0.0
with H.app.app_context():
    from app.services.financial_ledgers import booking_undelivered_value
    undel_total = sum(float(booking_undelivered_value(x["name"]) or 0) for x in H.q("SELECT name FROM client"))
open_bills_on_page = L.money_in(dash, "open bills total")
L.check("R2-R11", "Reconciliation / Dashboard", "/",
        "Compare the dashboard's client-dues and undelivered tiles with the ledgers and the open bills",
        "the dues tile equals the sum of the client balances that are in debit, the undelivered tile equals "
        "the undelivered value the ledgers compute, and the open bills the tile nets out are stated on the "
        "same card, so nothing unowned or netted-away is hidden",
        f"dashboard net client dues={d_out} (ledger debit balances sum={ledger_pos}); "
        f"dashboard Undelivered Bookings={d_und} (computed {undel_total}); "
        f"open bills stated on the dashboard={open_bills_on_page} (pending_bill says {receivable}); "
        f"cash-on-today-trading tile={d_cash}",
        (d_out is not None and abs(d_out - ledger_pos) < 1.0)
        and (d_und is None or abs(d_und - undel_total) < 1.0)
        and open_bills_on_page is not None
        and abs(open_bills_on_page - receivable) < 1.0, "HIGH",
        evidence=f"client ledger debit balances {ledger_pos:,.2f} vs {receivable:,.2f} of open bills: the "
                 f"difference is advances a client has already paid, which the card now states instead of "
                 f"claiming the tile is 'real dues across open bills'.")

print("S7 done")
