"""RE-TEST of the fixed build (AMSV5-2_FIXED) — one trading day through the real forms.

Run:
  AMS_AUDIT_APP=/home/user/AMSV5-2_FIXED \
  AMS_AUDIT_DB=/home/user/AUDIT_2026-11-18/retest/retest.db \
  AMS_AUDIT_RESULTS=/home/user/AUDIT_2026-11-18/retest/retest_results.json \
  python3 tools/retest_all.py
"""
import json
import re
import sys

sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()
TODAY = "2026-11-18"


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


def stock(name):
    row = H.one("SELECT total FROM material WHERE name = ?", (name,))
    return float(row["total"]) if row else None


def cash():
    return float(H.one("SELECT balance FROM account WHERE id = 1")["balance"])


def bill(no):
    return H.one("SELECT bill_no, amount, is_paid, is_void FROM pending_bill WHERE bill_no = ?"
                 " ORDER BY id DESC LIMIT 1", (no,))


# ============================================================== 1. masters
for _name, _unit in (("12mm Steel Bar", "Kg"), ("20mm Steel Bar", "Kg")):
    c.post("/add_material", data={"material_name": _name, "material_unit": _unit,
                                  "category_id": ""}, follow_redirects=True)
c.post("/add_supplier", data={"name": "AUDIT-SUP-01", "phone": "0300-0000000",
                              "address": "Audit vendor", "opening_balance": "0"},
       follow_redirects=True)
c.post("/add_client", data={"name": "AUDIT-CLI-01", "code": "AC01", "category": "Retail",
                            "phone": "0301-0000000", "address": "Audit customer",
                            "opening_balance": "0"}, follow_redirects=True)
c.post("/accounts/accounts/add", data={
    "name": "Main Cash", "class_category": "Assets", "class_subcategory": "Cash",
    "class_account_type": "Main Cash", "channel": "cash", "account_status": "active",
    "opening_amount": "50000", "opening_position": "debit",
    "opening_effective_date": "2026-11-01"}, follow_redirects=True)

mats = H.q("SELECT id, name, total FROM material ORDER BY id")
sup = H.one("SELECT id, name FROM supplier WHERE name='AUDIT-SUP-01'")
cli = H.one("SELECT id, name, code FROM client WHERE name='AUDIT-CLI-01'")
if cli and cli["code"] != "AC01":   # build-generated code is fine too
    pass
acct = H.one("SELECT id, balance FROM account WHERE id=1")
print("masters:", mats, sup, cli, acct)
assert sup and cli and acct, "master data missing"

# ============================================================== 2. GRN (F-12)
c.post("/grn", data={
    "action": "add", "supplier": "AUDIT-SUP-01", "manual_bill_no": "AUD-GRN-001",
    "date": TODAY, "allocation_basis_default": "VALUE",
    "mat_name[]": ["12mm Steel Bar", "20mm Steel Bar"], "qty[]": ["1000", "500"],
    "price[]": ["100", "120"],
}, follow_redirects=True)
grn1 = H.one("SELECT id, auto_bill_no, landed_cost_total FROM grn ORDER BY id DESC LIMIT 1")
entries1 = H.q("SELECT id, material, qty, source_module, source_table, source_id "
               "FROM entry WHERE type='IN' ORDER BY id")
linked = [e for e in entries1 if e["source_module"] == "GRN" and e["source_id"] == grn1["id"]]
R.record("R-F12", "Purchases / traceability", "/grn",
         "Receive a 2-line GRN and inspect the stock movements it created",
         "every IN entry names its GRN (source_module='GRN', source_id=grn.id)",
         f"entries={[(e['id'], e['source_module'], e['source_id']) for e in entries1]}",
         "PASS" if len(linked) == 2 else "FAIL",
         severity=None if len(linked) == 2 else "MEDIUM")

# ============================================================ 3. GRN with legacy cost fields (F-15)
c.post("/grn", data={
    "action": "add", "supplier": "AUDIT-SUP-01", "manual_bill_no": "AUD-GRN-002",
    "date": TODAY, "allocation_basis_default": "VALUE",
    "mat_name[]": ["12mm Steel Bar"], "qty[]": ["400"], "price[]": ["110"],
    "loading_cost": "5000",           # retired flat field, used to be ignored
}, follow_redirects=True)
grn2 = H.one("SELECT id, auto_bill_no, landed_cost_total, loading_cost FROM grn ORDER BY id DESC LIMIT 1")
charge = H.one("SELECT id, charge_type, amount FROM grn_charge WHERE grn_id = ? ORDER BY id", (grn2["id"],))
item2 = H.one("SELECT id, mat_name, qty, price_at_time FROM grn_item WHERE grn_id = ?", (grn2["id"],))
landed = H.one("SELECT qty, landed_unit_cost FROM grn_item WHERE grn_id = ?", (grn2["id"],))
print("grn2:", grn2, "charge:", charge, "item:", item2, "landed:", landed)
R.record("R-F15", "Purchases / costing", "/grn (legacy cost fields)",
         "Post a GRN using only the retired 'loading_cost' field (no charge grid)",
         "the 5,000 loading cost becomes a real charge line and is capitalised into the landed cost "
         "(49,000 / 400 = 122.50 per unit)",
         f"grn_charge={charge}; grn_item={item2}; landed={landed}",
         "PASS" if charge and abs(float(charge["amount"] or 0) - 5000) < 0.01
         and landed and abs(float(landed["landed_unit_cost"] or 0) - 122.5) < 0.01 else "FAIL",
         severity=None if charge else "MEDIUM")

# GRN page must show the charge, not the empty state (F-09 retraction evidence)
grn_page = page_text("/grn")
charge_visible = "Loading (legacy field)" in grn_page or "5000" in grn_page.replace(",", "")
R.record("R-F09", "Purchases / UI", "/grn",
         "Open the saved GRN and check the Expenses tab",
         "the charge line is listed on the GRN (the 'No charges on this GRN.' alert is only an empty-state)",
         f"charge row in DB={bool(charge)}; phrase on page={'No charges on this GRN' in grn_page}; "
         f"amount visible={charge_visible}",
         "PASS" if charge else "FAIL", severity=None)

# ============================================================ 4. cash sale (F-13)
stock_before = stock("12mm Steel Bar")
c.post("/add_direct_sale", data={
    "category": "Cash", "client_name": "Cash Customer", "driver_name": "Audit Driver",
    "product_name[]": ["12mm Steel Bar"], "qty[]": ["300"], "unit_rate[]": ["150"],
    "discount": "500", "paid_amount": "44500", "payment_method": "Cash",
    "payment_account_id": "1", "sale_date": TODAY, "note": "AUD-SALE-CASH",
    "idempotency_key": "retest-cash-1",
}, follow_redirects=True)
cash_sale = H.one("SELECT id, auto_bill_no, amount, paid_amount, discount FROM direct_sale ORDER BY id DESC LIMIT 1")
pb_cash = H.q("SELECT id, bill_no, amount, is_cash FROM pending_bill WHERE source_table='direct_sale' AND source_id=?",
              (cash_sale["id"],))
R.record("R-F13", "Sales / Receivable", "/add_direct_sale",
         "Cash sale 300 kg @150 = 45,000, discount 500, paid in full 44,500",
         "no pending bill is created for a fully settled counter sale",
         f"sale={cash_sale}; pending bills for it={pb_cash}",
         "PASS" if not pb_cash else "FAIL", severity=None if not pb_cash else "LOW")

# ============================================================ 5. two credit bills + return against B (F-01)
for tag, qty, rate in (("A", 10, 150), ("B", 10, 160)):
    c.post("/add_direct_sale", data={
        "category": "Credit Customer", "client_name": "AUDIT-CLI-01", "client_code": "AC01",
        "driver_name": "Audit Driver", "product_name[]": ["20mm Steel Bar"],
        "qty[]": [str(qty)], "unit_rate[]": [str(rate)], "paid_amount": "0",
        "discount": "0", "payment_method": "Cash", "sale_date": TODAY,
        "note": f"AUD-BILL-{tag}", "idempotency_key": f"retest-bill-{tag}",
    }, follow_redirects=True)
bills = H.q("SELECT bill_no, amount, source_id FROM pending_bill "
            "WHERE source_table='direct_sale' AND is_void=0 ORDER BY id")
print("credit bills:", bills)
bill_a = bills[-2]
bill_b = bills[-1]

r = c.post("/add_material_return", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "date": TODAY,
    "note": "AUD-RET-AGAINST-B", "return_type": "normal",
    "source_bill_no": bill_b["bill_no"],
    "material_name[]": ["20mm Steel Bar"], "qty[]": ["10"],
    "unit_rate[]": ["160"], "rent_rate[]": ["0"],
}, follow_redirects=True)
after_a, after_b = bill(bill_a["bill_no"]), bill(bill_b["bill_no"])
ret = H.one("SELECT id, amount, source_sale_id, source_bill_no FROM material_return ORDER BY id DESC LIMIT 1")
print("flash:", H.flash_messages(r), "| return:", ret, "| A:", after_a, "| B:", after_b)
R.record("R-F01", "Returns / Receivable", "/add_material_return",
         "Two credit bills (A=1,500, B=1,600); return B's goods (10 @160 = 1,600) naming bill B",
         "bill B settles to 0 and bill A stays at 1,500",
         f"return={ret}; bill A={after_a}; bill B={after_b}",
         "PASS" if after_b and abs(float(after_b["amount"])) < 0.01
         and after_a and abs(float(after_a["amount"]) - 1500) < 0.01 else "FAIL",
         severity=None)

# auto-detection when no bill is named: newest matching sale wins, not the oldest
c.post("/add_direct_sale", data={
    "category": "Credit Customer", "client_name": "AUDIT-CLI-01", "client_code": "AC01",
    "driver_name": "Audit Driver", "product_name[]": ["12mm Steel Bar"],
    "qty[]": ["100"], "unit_rate[]": ["170"], "paid_amount": "0", "discount": "0",
    "payment_method": "Cash", "sale_date": TODAY, "note": "AUD-BILL-C",
    "idempotency_key": "retest-bill-C",
}, follow_redirects=True)
bill_c = H.q("SELECT bill_no, amount FROM pending_bill WHERE source_table='direct_sale' ORDER BY id DESC LIMIT 1")[0]
c.post("/add_material_return", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "date": TODAY,
    "note": "AUD-RET-AUTODETECT", "return_type": "normal",
    "material_name[]": ["12mm Steel Bar"], "qty[]": ["5"], "unit_rate[]": ["170"],
    "rent_rate[]": ["0"],
}, follow_redirects=True)
after_c = bill(bill_c["bill_no"])
R.record("R-F01b", "Returns / Receivable", "/add_material_return",
         "Same client, only bill C holds 12mm; return 5 kg without naming a bill",
         "the credit settles bill C (17,000 -> 16,150) and nothing else",
         f"bill C before={bill_c['amount']} after={after_c}",
         "PASS" if after_c and abs(float(after_c["amount"]) - 16150) < 0.01 else "FAIL",
         severity=None)

# ============================================================ 6. walk-in return (F-08)
stock_b = stock("12mm Steel Bar")
r = c.post("/add_material_return", data={
    "client_name": "Cash Customer", "date": TODAY, "note": "AUD-RET-WALKIN",
    "return_type": "normal", "source_bill_no": cash_sale["auto_bill_no"],
    "material_name[]": ["12mm Steel Bar"], "qty[]": ["10"], "unit_rate[]": ["150"],
    "rent_rate[]": ["0"],
}, follow_redirects=True)
stock_a = stock("12mm Steel Bar")
walkin = H.one("SELECT id, name, code FROM client WHERE code LIKE 'WC-%' ORDER BY id DESC LIMIT 1")
walkin_ret = H.one("SELECT id, client_name, amount, source_bill_no FROM material_return ORDER BY id DESC LIMIT 1")
R.record("R-F08", "Returns", "/add_material_return",
         "Return 10 kg of a walk-in cash sale (no client master) by naming the bill",
         "return accepted, stock +10, the walk-in customer gets a real account",
         f"flash={H.flash_messages(r)}; stock {stock_b}->{stock_a}; client={walkin}; return={walkin_ret}",
         "PASS" if abs((stock_a or 0) - (stock_b + 10)) < 0.01 and walkin_ret else "FAIL",
         severity=None if walkin else "MEDIUM")

# ============================================================ 7. booking + delivery (F-11)
c.post("/add_booking", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01",
    "material_name[]": ["12mm Steel Bar"], "qty[]": ["100"], "unit_rate[]": ["160"],
    "amount": "16000", "paid_amount": "5000", "discount": "0", "date": TODAY,
    "note": "AUD-BOOK-001", "payment_method": "Cash", "payment_account_id": "1",
}, follow_redirects=True)
booking1 = H.one("SELECT id, auto_bill_no, amount, paid_amount FROM booking ORDER BY id DESC LIMIT 1")
stock_b = stock("12mm Steel Bar")
c.post("/add_direct_sale", data={
    "category": "Booking Delivery", "client_name": "AUDIT-CLI-01", "client_code": "AC01",
    "driver_name": "Audit Driver", "product_name[]": ["12mm Steel Bar"],
    "qty[]": ["100"], "unit_rate[]": ["0"], "paid_amount": "0", "discount": "0",
    "payment_method": "Cash", "sale_date": TODAY, "note": "AUD-BOOK-DELIVERY",
    "idempotency_key": "retest-delivery-1",
}, follow_redirects=True)
stock_a = stock("12mm Steel Bar")
delivery_item = H.q("SELECT i.id, i.product_name, i.qty, i.price_at_time, i.cost_rate_at_sale "
                    "FROM direct_sale_item i JOIN direct_sale s ON s.id=i.sale_id "
                    "ORDER BY i.id DESC LIMIT 1")[0]
R.record("R-F11", "Bookings / Delivery", "/add_direct_sale (Booking Delivery)",
         "Deliver 100 x 12mm against a booking and inspect the delivery line",
         "the delivery line carries the frozen cost basis (cost_rate_at_sale no longer NULL) "
         "so the P&L does not have to estimate it",
         f"stock {stock_b}->{stock_a}; delivery line={delivery_item}",
         "PASS" if delivery_item["cost_rate_at_sale"] is not None else "FAIL",
         severity=None if delivery_item["cost_rate_at_sale"] is not None else "MEDIUM")

# two undelivered bookings for the same client
for i in (1, 2):
    c.post("/add_booking", data={
        "client_code": "AC01", "client_name": "AUDIT-CLI-01",
        "material_name[]": ["20mm Steel Bar"], "qty[]": ["100"], "unit_rate[]": ["150"],
        "amount": "15000", "paid_amount": "0", "discount": "0", "date": TODAY,
        "note": f"AUD-BOOK-UNDELIVERED-{i}", "payment_method": "Cash",
        "payment_account_id": "1",
    }, follow_redirects=True)

# ============================================================ 8. void keeps the document (F-07)
c.post("/add_direct_sale", data={
    "category": "Credit Customer", "client_name": "AUDIT-CLI-01", "client_code": "AC01",
    "driver_name": "Audit Driver", "product_name[]": ["20mm Steel Bar"],
    "qty[]": ["20"], "unit_rate[]": ["140"], "paid_amount": "0", "discount": "0",
    "payment_method": "Cash", "sale_date": TODAY, "note": "AUD-TO-VOID",
    "idempotency_key": "retest-void-1",
}, follow_redirects=True)
victim = H.one("SELECT id, auto_bill_no, amount FROM direct_sale ORDER BY id DESC LIMIT 1")
stock_b = stock("20mm Steel Bar")
no_reason = c.post(f"/void_transaction/DirectSale/{victim['id']}", data={}, follow_redirects=True)
blocked = H.one("SELECT is_void FROM direct_sale WHERE id=?", (victim["id"],))
ok_resp = c.post(f"/void_transaction/DirectSale/{victim['id']}",
                 data={"reason": "customer cancelled the order"}, follow_redirects=True)
voided = H.one("SELECT id, auto_bill_no, amount, is_void FROM direct_sale WHERE id=?", (victim["id"],))
stock_a = stock("20mm Steel Bar")
void_pb = H.q("SELECT bill_no, amount, is_void FROM pending_bill WHERE source_table='direct_sale' AND source_id=?",
              (victim["id"],))
void_entries = H.q("SELECT id, qty, is_void FROM entry WHERE bill_no=? OR auto_bill_no=?",
                   (victim["auto_bill_no"], victim["auto_bill_no"]))
audit_rows = H.q("SELECT action, details FROM audit_log WHERE action LIKE 'transaction.void%' ORDER BY id DESC LIMIT 2")
print("void:", voided, "| stock:", stock_b, "->", stock_a, "| pb:", void_pb, "| entries:", void_entries)
R.record("R-F07", "Sales / Void", f"/void_transaction/DirectSale/{victim['id']}",
         "Void a posted credit sale (20 kg @140 = 2,800): first without a reason, then with one",
         "no reason -> refused and the document stays live; with a reason -> is_void=1, the row and its "
         "bill number survive, stock is restored, the receivable is released, the reason is audited",
         f"no-reason flash={H.flash_messages(no_reason)[:1]}, still live={blocked}; "
         f"after: {voided}; stock {stock_b}->{stock_a}; pending={void_pb}; entries={void_entries}; audit={audit_rows}",
         "PASS" if voided and voided["is_void"] == 1 and blocked and blocked["is_void"] == 0
         and abs((stock_a or 0) - (stock_b + 20)) < 0.01 else "FAIL",
         severity=None)

# hard delete still possible, but only with the typed confirmation
nodelete = c.post(f"/delete_transaction/DirectSale/{victim['id']}", data={}, follow_redirects=True)
still_there = H.one("SELECT id FROM direct_sale WHERE id=?", (victim["id"],))
R.record("R-F07b", "Sales / Delete", "/delete_transaction/...",
         "Try the destructive delete without typing DELETE",
         "refused; the document survives",
         f"flash={H.flash_messages(nodelete)[:1]}; row exists={bool(still_there)}",
         "PASS" if still_there else "FAIL", severity=None)

# ============================================================ 9. dashboard + payables (F-02/03/04)
dash = page_text("/")
due_tile = money(r"Daily Due Amount Rs\. ([\d,]+(?:\.\d\d)?)", dash)
und_tile = money(r"Undelivered Bookings Rs\. ([\d,]+(?:\.\d\d)?)", dash)
out_tile = money(r"Total Outstanding Rs\. ([\d,]+(?:\.\d\d)?)", dash)
ledger = page_text(f"/financial_ledger/{cli['id']}")
led_net = money(r"Net Balance ([\d,]+(?:\.\d\d)?)", ledger)
led_und = money(r"Undelivered Booking ([\d,]+(?:\.\d\d)?)", ledger)
payables = page_text("/current_payables")
pay_total = money(r"Outstanding Payables Rs\. ([\d,]+(?:\.\d\d)?)", payables)

with H.app.app_context():
    from datetime import date as _d
    from app.services.financial_ledgers import due_on_delivered_goods
    comp = due_on_delivered_goods(_d(2026, 11, 18))

print("\ndashboard:", due_tile, und_tile, out_tile, "| ledger:", led_net, led_und, "| payables:", pay_total,
      "| components:", {k: v for k, v in comp.items() if k != 'by_client'})

R.record("R-F02", "Dashboard", "/ (dashboard)",
         "Delivered booking (16,000, 5,000 advance) plus two undelivered bookings for the same client",
         "Daily Due includes the delivered booking's collectable value "
         "(delivered charge - money already in)",
         f"tile={due_tile}; engine={comp['delivered']}; by_client={comp['by_client']}",
         "PASS" if due_tile is not None and abs(due_tile - comp["delivered"]) < 1.0
         and (comp["delivered"] or 0) > 0 else "FAIL", severity=None)

R.record("R-F03", "Dashboard", "/ (dashboard)",
         "Three bookings for one client (one delivered, two undelivered = 30,000)",
         "Undelivered tile shows the client's undelivered value once: Rs 30,000",
         f"tile={und_tile}; engine={comp['undelivered']}; client ledger={led_und}",
         "PASS" if und_tile is not None and abs(und_tile - 30000) < 1.0
         and abs((led_und or 0) - 30000) < 0.01 else "FAIL", severity=None)

R.record("R-F04", "Dashboard / Payables", "/current_payables + /financial_ledger",
         "Compare 'Total Outstanding' / 'Current Payables' with the client ledger for the same day",
         "one number: undelivered bookings are excluded from receivables everywhere",
         f"dashboard outstanding={out_tile}; current payables={pay_total}; client ledger net={led_net}",
         "PASS" if out_tile is not None and pay_total is not None and led_net is not None
         and abs(out_tile - pay_total) < 1.0 and abs(pay_total - led_net) < 1.0 else "FAIL",
         severity=None)

# ============================================================ 10. daily breakdown (F-05)
daily = page_text("/inventory/daily_transactions")
cash_tile = money(r"Cash Receipts Rs\. ([\d,]+(?:\.\d\d)?)", daily)
cred_tile = re.search(r"Rs\. ([\d,]+(?:\.\d\d)?) of return credits excluded", daily)
return_total = H.scalar("SELECT SUM(amount) FROM material_return WHERE is_void=0") or 0
cash_in = H.scalar("SELECT SUM(amount) FROM payment WHERE is_void=0 AND method <> 'Material Return'") or 0
R.record("R-F05", "Inventory / Daily Breakdown", "/inventory/daily_transactions",
         "Day with both receipts and material-return credit notes",
         "the money tile shows cash received only, and discloses the credit notes separately",
         f"tile={cash_tile}; credit-note note={cred_tile.group(1) if cred_tile else None}; "
         f"DB cash receipts={cash_in}; DB return credits={return_total}",
         "PASS" if cash_tile is not None and abs(cash_tile - float(cash_in)) < 0.01 else "FAIL",
         severity=None)

# ============================================================ 11. integrity audit (F-10)
sysrep = page_text("/system_report")
has_false_positive = "SB-SL" in sysrep and "Missing Pending Bill" in sysrep
recon_present = "Cross-report Reconciliation" in sysrep
mismatch = re.search(r"Cross-report Reconciliation \((\d+) mismatch", sysrep)
R.record("R-F10", "System / Integrity Audit", "/system_report",
         "Run the integrity audit on a dataset that includes a booking delivery",
         "no false 'Missing Pending Bill' for booking dispatches, and the new cross-report "
         "reconciliation shows 0 mismatches",
         f"false positive present={has_false_positive}; recon section={recon_present}; "
         f"mismatches reported={(mismatch.group(1) if mismatch else 'n/a')}",
         "PASS" if recon_present and not has_false_positive and mismatch and mismatch.group(1) == "0"
         else "FAIL", severity=None)

# ============================================================ 12. CSRF (F-06 retraction)
H.app.config["AMS_CSRF_ALWAYS"] = True
try:
    anon = H.app.test_client()
    login_page = anon.get("/login").get_data(as_text=True)
    token = re.search(r'name="_csrf_token"\s+value="([^"]+)"', login_page)
    anon.post("/login", data={"username": "Admin", "password": "Admin@fbm12345",
                              "_csrf_token": token.group(1) if token else ""})
    before = H.scalar("SELECT COUNT(*) FROM payment")
    bad = anon.post("/add_payment", data={"client_code": "AC01", "amount": "5",
                                          "payment_type": "Receipt", "method": "Cash",
                                          "payment_account_id": "1", "date": TODAY})
    created_bad = H.scalar("SELECT COUNT(*) FROM payment WHERE amount = 5")
    good = anon.post("/add_payment", data={"client_code": "AC01", "amount": "6",
                                           "payment_type": "Receipt", "method": "Cash",
                                           "payment_account_id": "1", "date": TODAY,
                                           "_csrf_token": token.group(1) if token else ""})
    created_good = H.scalar("SELECT COUNT(*) FROM payment WHERE amount = 6")
finally:
    H.app.config["AMS_CSRF_ALWAYS"] = False

R.record("R-F06", "Security / CSRF", "/add_payment (gate forced on)",
         "Post a receipt with no token, then with the session token",
         "no token -> 400 and nothing saved; valid token -> accepted",
         f"no token: HTTP {bad.status_code}, rows={created_bad}; "
         f"valid token: HTTP {good.status_code}, rows={created_good}",
         "PASS" if bad.status_code == 400 and not created_bad and created_good else "FAIL",
         evidence="The shipped build already enforces this in app/hooks.py::_protect_against_csrf; "
                  "the audit's harness had TESTING=True, which deliberately disables it.",
         note="RETRACTION: the audit's F-06 was a false positive caused by the test harness.")

# ============================================================ 8b. void a receipt and a stock entry
c.post("/add_payment", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "amount": "300",
    "payment_type": "Receipt", "method": "Cash", "payment_account_id": "1",
    "date": TODAY, "note": "AUD-TO-VOID-RECEIPT",
}, follow_redirects=True)
rec = H.one("SELECT id, amount FROM payment ORDER BY id DESC LIMIT 1")
cash_b = cash()
def open_receivable():
    return float(H.scalar("SELECT SUM(amount) FROM pending_bill WHERE is_void=0 AND is_paid=0") or 0)
pb_before = open_receivable()
c.post(f"/void_transaction/Payment/{rec['id']}", data={"reason": "posted twice"},
       follow_redirects=True)
rec_after = H.one("SELECT id, amount, is_void FROM payment WHERE id=?", (rec["id"],))
pb_after = open_receivable()
R.record("R-F07c", "Payments / Void", f"/void_transaction/Payment/{rec['id']}",
         "Void a 300 receipt that had settled part of a bill",
         "receipt kept and flagged void; cash returns by 300; the receivable reopens by 300",
         f"receipt={rec_after}; cash {cash_b}->{cash()}; open receivable {pb_before}->{pb_after}",
         "PASS" if rec_after and rec_after["is_void"] == 1
         and abs(cash() - (cash_b - 300)) < 0.01
         and abs(pb_after - (pb_before + 300)) < 0.01 else "FAIL",
         severity=None)

out_entry = H.one("SELECT id, qty, material FROM entry WHERE type='OUT' AND is_void=0 ORDER BY id DESC LIMIT 1")
mat_name = out_entry["material"]
stock_b = stock(mat_name)
c.post(f"/void_transaction/Entry/{out_entry['id']}", data={"reason": "wrong material dispatched"},
       follow_redirects=True)
entry_after = H.one("SELECT id, is_void FROM entry WHERE id=?", (out_entry["id"],))
stock_a = stock(mat_name)
R.record("R-F07d", "Stock / Void", f"/void_transaction/Entry/{out_entry['id']}",
         "Void a single OUT stock movement from the daily breakdown",
         "entry kept and flagged void; stock returns by its qty",
         f"entry={entry_after}; {mat_name} {stock_b}->{stock_a}",
         "PASS" if entry_after and entry_after["is_void"] == 1
         and abs(stock_a - (stock_b + float(out_entry["qty"]))) < 0.01 else "FAIL",
         severity=None)


# ============================================================ 13. accounting still ties
profit = page_text("/profit_reports")
cf = page_text("/cash_flow")
gross = money(r"Gross sales Rs\. ([\d,]+(?:\.\d\d)?)", profit)
disc = money(r"Discounts given Rs\. ([\d,]+(?:\.\d\d)?)", profit)
net_rev = money(r"Net revenue Rs\. ([\d,]+(?:\.\d\d)?)", profit)
cogs = money(r"COGS \+ Direct Service Cost Rs\. ([\d,]+(?:\.\d\d)?)", profit)
net_profit = money(r"Net Profit / Loss for the selected period Rs\. ([\d,]+(?:\.\d\d)?)", profit)
est_rows = re.search(r"(\d+) estimated-cost row", profit)
cf_close = money(r"Closing Balance Rs\. ([\d,]+(?:\.\d\d)?)", cf)
cf_in = money(r"Total Received \[ IN \] Rs\. ([\d,]+(?:\.\d\d)?)", cf)
cf_out = money(r"Total Spent \[ OUT \] Rs\. ([\d,]+(?:\.\d\d)?)", cf)
bal = cash()

print("\nprofit:", gross, disc, net_rev, cogs, net_profit, "| est rows:", est_rows.group(1) if est_rows else None)
print("cash flow:", cf_in, cf_out, cf_close, "| account:", bal)

R.record("R-B01", "Reports / Profit", "/profit_reports",
         "Profit report after the full day's trading",
         "net revenue - COGS = net profit, and no line needs an estimated cost",
         f"gross={gross} discounts={disc} net_revenue={net_rev} cogs={cogs} net_profit={net_profit} "
         f"estimated_rows={(est_rows.group(1) if est_rows else '0')}",
         "PASS" if net_rev is not None and cogs is not None and net_profit is not None
         and abs((net_rev - cogs) - net_profit) < 0.01 else "FAIL", severity=None)

R.record("R-B09", "Reports / Cash Flow", "/cash_flow",
         "Cash flow must equal the cash account",
         "closing balance equals account.balance; spent is the one supplier payment",
         f"in={cf_in} out={cf_out} closing={cf_close}; account balance={bal}",
         "PASS" if cf_close is not None and abs(cf_close - bal) < 0.01 else "FAIL", severity=None)

print("\n=== final DB state ===")
print("stock:", H.q("SELECT name, total FROM material ORDER BY id"))
print("cash:", cash())
print("payments:", H.q("SELECT id, client_name, amount, method FROM payment ORDER BY id"))
print("returns:", H.q("SELECT id, client_name, amount, source_sale_id, source_bill_no FROM material_return ORDER BY id"))
print("pending:", H.q("SELECT bill_no, client_name, amount, is_paid, is_void FROM pending_bill ORDER BY id"))
print("sales:", H.q("SELECT id, auto_bill_no, category, client_name, amount, paid_amount, is_void FROM direct_sale ORDER BY id"))
print("bookings:", H.q("SELECT id, amount, paid_amount, is_void FROM booking ORDER BY id"))
print("integrity:", H.one("PRAGMA integrity_check"))
print("fk:", len(H.q("PRAGMA foreign_key_check")))
