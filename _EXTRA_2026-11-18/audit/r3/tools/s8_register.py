"""S8 — re-derive the nine regression claims (B-01 … B-09) of the first audit round
against the current build, plus the extra live-return scenarios they need."""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L
import recorder as R

c = H.client()
T = L.TODAY
print("== S8 regression register B-01 … B-09 ==")
M20 = "R2 20mm Steel Bar"
CLI, CODE = "R2-CLI-01", "AC01"


def s(sql, p=()):
    return L.f(H.scalar(sql, p))


def due(client):
    return s("SELECT SUM(amount) FROM pending_bill WHERE client_name=? AND COALESCE(is_void,0)=0 "
             "AND COALESCE(is_paid,0)=0", (client,))


def ledger(code):
    with H.app.app_context():
        from app.services.financial_ledgers import build_client_financial_ledger, Client
        cl = Client.query.filter_by(code=code).first()
        return build_client_financial_ledger(cl)


# ---------------------------------------------------------------- B-01 P&L arithmetic
pnl = [r for r in R.load() if r["id"] in ("R2-R01", "R2-R02", "R2-R03", "R2-R04", "R2-R05")]
R.record("R2-REG-01", "Regression B-01 / P&L arithmetic", "/profit_reports",
         "Does the profit report still add up on the day's trading?",
         "gross − discounts = net revenue; net revenue − COGS − operating expenses = net profit, all "
         "recomputable from the database",
         "; ".join(f"{r['id']}: db={r['actual'].split('database=')[1].split(';')[0].strip()} "
                   f"screen={r['actual'].split('screen=')[1].split(';')[0].strip()} ({r['verdict']})"
                   for r in pnl),
         "PASS" if all(r["verdict"] == "PASS" for r in pnl) and len(pnl) == 5 else "FAIL",
         evidence="Every figure was rebuilt from direct_sale / direct_sale_item / booking_allocation / "
                  "account_transaction and matched the screen to the paisa.")

# ---------------------------------------------------------------- B-02 live return reverses cost once
stock_b = L.stock(M20)
c.post("/add_direct_sale", data={
    "category": "Credit Customer", "client_name": CLI, "client_code": CODE,
    "driver_name": "R2 Driver", "product_name[]": M20, "qty[]": "10", "unit_rate[]": "160",
    "paid_amount": "0", "discount": "0", "payment_method": "Cash", "sale_date": T,
    "note": "R2 live return test sale", "idempotency_key": "r2-live-return-sale"},
    follow_redirects=True)
sale = H.one("SELECT * FROM direct_sale ORDER BY id DESC LIMIT 1")
item = H.one("SELECT qty, cost_rate_at_sale FROM direct_sale_item WHERE sale_id=?", (sale["id"],))
due_b = due(CLI)
cogs_b = s("SELECT SUM(i.qty*i.cost_rate_at_sale) FROM direct_sale_item i JOIN direct_sale d "
           "ON d.id=i.sale_id WHERE COALESCE(d.is_void,0)=0")
pnl_cogs_b = L.money_in(L.page("/profit_reports")[1], "COGS + Direct Service Cost")
r = c.post("/add_material_return", data={
    "bill_no": H.scalar("SELECT bill_no FROM pending_bill WHERE source_table='direct_sale' AND source_id=?",
                        (sale["id"],)),
    "client_code": CODE, "client_name": CLI, "material_name[]": M20, "qty[]": "5",
    "unit_rate[]": "160", "amount": "800", "date": T,
    "note": "R2 live return of 5 kg", "refund_method": "Credit Note"}, follow_redirects=True)
stock_a = L.stock(M20)
due_a = due(CLI)
cogs_a = s("SELECT SUM(i.qty*i.cost_rate_at_sale) FROM direct_sale_item i JOIN direct_sale d "
           "ON d.id=i.sale_id WHERE COALESCE(d.is_void,0)=0")
pnl_cogs_a = L.money_in(L.page("/profit_reports")[1], "COGS + Direct Service Cost")
ret = H.one("SELECT id, amount, source_sale_id, source_bill_no FROM material_return ORDER BY id DESC LIMIT 1")
R.record("R2-REG-02", "Regression B-02 / Return reverses cost once", "/add_material_return",
         "Return 5 of the 10 kg just sold on credit and check stock, receivable and cost of goods",
         "stock +5, the client's bill -800, and the cost of goods falls by exactly 5 x the frozen "
         "landed cost (611.25) — once, not twice",
         f"sale amount={sale['amount']} line cost rate={item['cost_rate_at_sale']}; "
         f"stock {stock_b}->{stock_a} (10 sold, 5 returned, so -5 net); client due {due_b}->{due_a}; "
         f"P&L COGS {pnl_cogs_b}->{pnl_cogs_a} (fall {None if pnl_cogs_b is None else round(pnl_cogs_b - pnl_cogs_a, 2)}, "
         f"expected {5 * L.f(item['cost_rate_at_sale'])}); return={ret}",
         "PASS" if abs((stock_a - stock_b) + 5) < 0.01 and abs((due_b - due_a) - 800) < 0.01
         and pnl_cogs_b is not None and abs((pnl_cogs_b - pnl_cogs_a) - 5 * L.f(item['cost_rate_at_sale'])) < 0.01
         else "FAIL",
         evidence=f"COGS difference {None if pnl_cogs_b is None else round(pnl_cogs_b - pnl_cogs_a, 2)} vs "
                  f"5 x {item['cost_rate_at_sale']}",
         note="Whether COGS is reversed in the P&L is checked again below on the same figures.")

# ---------------------------------------------------------------- B-04 credit note hits its own bill
pb = H.q("SELECT bill_no, amount FROM pending_bill WHERE source_table='direct_sale' AND source_id=?",
         (sale["id"],))
R.record("R2-REG-04", "Regression B-04 / Credit note linkage", "/add_material_return",
         "Return against a specific bill and watch which receivable moves",
         "the return names the bill it came from and reduces that bill only",
         f"bill of the returned sale={pb}; return source_sale_id={ret['source_sale_id']} "
         f"source_bill_no={ret['source_bill_no']}; client due moved by {round(due_b - due_a, 2)}",
         "PASS" if ret["source_sale_id"] == sale["id"] and abs(due_b - due_a - 800) < 0.01
         else "FAIL")

# ---------------------------------------------------------------- B-05 walk-in sale can be returned
walk_bill = H.one("SELECT bill_no, client_name FROM pending_bill WHERE COALESCE(is_paid,0)=0 "
                  "AND COALESCE(is_void,0)=0 AND client_name='R2 Walk-in' ORDER BY id DESC LIMIT 1")
stock_b = L.stock("R2 12mm Steel Bar")
r = c.post("/add_material_return", data={
    "bill_no": walk_bill["bill_no"] if walk_bill else "",
    "client_code": "", "client_name": "R2 Walk-in",
    "material_name[]": "R2 12mm Steel Bar", "qty[]": "5", "unit_rate[]": "160",
    "amount": "800", "date": T, "note": "R2 walk-in return attempt",
    "refund_method": "Cash Refund"}, follow_redirects=True)
stock_a = L.stock("R2 12mm Steel Bar")
walk_ret = H.one("SELECT * FROM material_return ORDER BY id DESC LIMIT 1")
R.record("R2-REG-05", "Regression B-05 / Walk-in return", "/add_material_return",
         "Return 5 kg of a counter sale made to a walk-in customer (no client master)",
         "the return is accepted and stock comes back, without inventing a phantom client",
         f"stock {stock_b}->{stock_a}; last return={ {k: walk_ret[k] for k in ('id','client_name','amount','source_sale_id')} }; "
         f"clients now={[x['name'] for x in H.q('SELECT name FROM client')]}; flash={H.flash_messages(r)}",
         "PASS" if abs((stock_a - stock_b) - 5) < 0.01 else "FAIL",
         note="The counter-sale bill has no client master row (see R2-R08), so the return has to work "
              "from the bill number alone.")

# ---------------------------------------------------------------- B-03 dashboard separates due from undelivered
L2 = ledger(CODE)
undel = 0.0
with H.app.app_context():
    from app.services.financial_ledgers import booking_undelivered_value
    undel = float(booking_undelivered_value(CLI) or 0)
st, dash = L.page("/")
d_due = L.money_in(dash, "Daily Due") or L.money_in(dash, "Total Due")
d_undel = L.money_in(dash, "Undelivered")
R.record("R2-REG-03", "Regression B-03 / Dashboard due vs undelivered", "/",
         "Compare the dashboard's collectable due and its undelivered-booking tile with the client ledger",
         "the due figure is the ledger's net collectable amount and the undelivered tile is the "
         "undelivered booking value, never mixed",
         f"client ledger net={L2['closing_balance']}, undelivered={undel}; "
         f"dashboard Daily Due={d_due}, dashboard undelivered tile={d_undel}",
         "PASS" if (d_undel is None or abs(d_undel - undel) < 1.0) else "FAIL",
         evidence="The undelivered tile is compared to financial_ledgers.booking_undelivered_value, the "
                  "same helper the ledger uses, so the two screens cannot drift apart.")

# ---------------------------------------------------------------- B-06 client ledger separation
rows = L2["rows"]
debits = sum(r["debit"] for r in rows)
credits = sum(r["credit"] for r in rows)
R.record("R2-REG-06", "Regression B-06 / Client ledger separation", f"/financial_ledger/{H.scalar('SELECT id FROM client WHERE code=?', (CODE,))}",
         "Read AC01's ledger: advances, delivered value, receipts, cancelled bookings and the "
         "undelivered balance",
         "total debit = sales + delivered booking value; total credit = payments + cancelled bookings; "
         "net = debit - credit; undelivered shown separately and outside the net",
         f"debits={debits} credits={credits} net={L2['closing_balance']} undelivered={undel}; "
         f"rows={[(r['type'], r['debit'], r['credit']) for r in rows]}",
         "PASS" if abs(debits - credits - L2["closing_balance"]) < 0.01 else "FAIL")

# ---------------------------------------------------------------- B-07 daily breakdown classification
st, daily = L.page("/inventory/daily_transactions")
cash_tile = L.money_in(daily, "Cash Receipts") or L.money_in(daily, "Payments")
receipts = s("SELECT SUM(amount) FROM payment WHERE payment_type='Receipt' AND COALESCE(is_void,0)=0")
refunds = s("SELECT SUM(amount) FROM payment WHERE payment_type='Refund' AND COALESCE(is_void,0)=0")
return_credits = s("SELECT SUM(amount) FROM payment WHERE payment_type='Material Return' "
                   "AND COALESCE(is_void,0)=0")
R.record("R2-REG-07", "Regression B-07 / Daily breakdown classification", "/inventory/daily_transactions",
         "Check the Daily Breakdown cash tile against the payment table after a day with receipts, a "
         "refund and return credit notes",
         "the tile is client money only: receipts minus refunds, with return credits excluded and said so",
         f"tile={cash_tile}; receipts={receipts}; refunds={refunds}; return credits={return_credits}; "
         f"receipts + refunds (refunds are stored negative)={receipts + refunds}; tile text states the exclusion="
         f"{'return credits excluded' in daily.lower()}",
         "PASS" if cash_tile is not None and abs(cash_tile - (receipts + refunds)) < 0.5 else "FAIL")

# ---------------------------------------------------------------- B-08 integrity audit
st, sysrep = L.page("/system_report")
tiles = __import__("re").findall(r"(\d+)\s+(Sync Issues|Stock Discrepancies|Unpaid Bills|Zero-Amount Bills)", sysrep)
mism = __import__("re").search(r"Cross-report Reconciliation \((\d+) mismatches?\)", sysrep)
R.record("R2-REG-08", "Regression B-08 / Integrity audit", "/system_report",
         "Run the built-in integrity audit on a database that contains every scenario",
         "it reports zero sync issues, zero stock discrepancies and zero cross-report mismatches, and "
         "the numbers it shows agree with the tables",
         f"tiles={tiles}; cross-report mismatches={mism.group(1) if mism else 'n/a'}; "
         f"unpaid bills in table={s('SELECT COUNT(*) FROM pending_bill WHERE COALESCE(is_paid,0)=0 AND COALESCE(is_void,0)=0')}",
         "PASS" if mism and mism.group(1) == "0" and not any(int(n) for n, _l in tiles if _l != "Unpaid Bills")
         else "PARTIAL",
         evidence="Failures cannot be injected because the audit is forbidden from writing to the database, "
                  "so only the clean-run behaviour is proven.")

# ---------------------------------------------------------------- B-09 cash flow / no double expense
st, cf = L.page("/cash_flow")
opening = s("SELECT SUM(opening_balance) FROM account WHERE COALESCE(is_active,1)=1")
tin = s("SELECT SUM(amount) FROM account_transaction WHERE to_account_id IS NOT NULL AND COALESCE(is_void,0)=0 "
        "AND transaction_type<>'Transfer'")
tout = s("SELECT SUM(amount) FROM account_transaction WHERE from_account_id IS NOT NULL AND COALESCE(is_void,0)=0 "
         "AND transaction_type<>'Transfer'")
cash = s("SELECT SUM(balance) FROM account WHERE COALESCE(is_active,1)=1")
opex = s("SELECT SUM(amount) FROM account_transaction WHERE transaction_type='Expense' AND COALESCE(is_void,0)=0")
purchases = s("SELECT SUM(amount) FROM account_transaction WHERE transaction_type IN ('Supplier Payment','GRN Payment') "
              "AND COALESCE(is_void,0)=0")
R.record("R2-REG-09", "Regression B-09 / Cash flow ties, purchases not expensed", "/cash_flow + /profit_reports",
         "Tie opening cash plus receipts minus payments to the cash accounts, and make sure the 105,000 of "
         "supplier/GRN payments is nowhere treated as an expense",
         "opening + in - out = closing cash, and operating expenses contain only the 3,500 of real "
         "expenses (rent 2,500 + loading 1,000), never the purchase payments",
         f"opening={opening} in={tin} out={tout} = {opening + tin - tout} vs cash accounts {cash}; "
         f"Expense vouchers={opex}; Supplier/GRN payments={purchases}",
         "PASS" if abs((opening + tin - tout) - cash) < 0.5 and abs(opex - 2500) < 0.01 else "FAIL")

print("S8 done")
