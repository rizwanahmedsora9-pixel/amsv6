"""S2 — purchases: GRN costing, charges, supplier payments, voids."""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L
from werkzeug.datastructures import MultiDict

c = H.client()
T = L.TODAY
print("== S2 purchases ==")
SUP1, SUP2 = "R2-SUP-01", "R2-SUP-02"
M12, M20 = "R2 12mm Steel Bar", "R2 20mm Steel Bar"

# ---------------------------------------------------------------- P-01 GRN-1
def grn_payload(bill, items, charges, note, include_charges=()):
    d = [("action", "add"), ("supplier", SUP1), ("manual_bill_no", bill), ("date", T),
         ("time", "09:00"), ("item_grid_present", "1"), ("allocation_basis_default", "VALUE"),
         ("charge_grid_present", "1"), ("charge_supplier_billing_present", "1"),
         ("bill_date", T), ("note", note)]
    for name, qty, price in items:
        d += [("grn_item_id[]", ""), ("mat_name[]", name), ("qty[]", str(qty)),
              ("price[]", str(price))]
    for key, ctype, label, amount, rec_type, rec_name, treat in charges:
        d += [("charge_id[]", ""), ("charge_type[]", ctype), ("charge_label[]", label),
              ("charge_amount[]", str(amount)), ("charge_row_key[]", key),
              ("charge_recipient_type[]", rec_type), ("charge_recipient[]", rec_name),
              ("charge_treatment[]", treat), ("charge_basis[]", "VALUE")]
        if key in include_charges:
            d.append(("charge_include_in_supplier[]", key))
    return MultiDict(d)


c.post("/grn", data=grn_payload(
    "R2-GRN-001",
    [(M12, 1000, 100), (M20, 500, 120)],
    [("row-freight", "VEHICLE_FARE", "Freight to site", 3000, "TRANSPORTER", "R2 Rana Transport", "INVENTORY"),
     ("row-loading", "LOADING", "Loading crew", 1000, "LABOUR", "R2 Loading crew", "EXPENSE")],
    "R2 GRN-1 credit purchase with capitalised freight and a separate loading expense"),
   follow_redirects=True)

grn1 = H.one("SELECT id, auto_bill_no, landed_cost_total, is_void FROM grn ORDER BY id DESC LIMIT 1")
items1 = H.q("SELECT mat_name, qty, price_at_time, landed_unit_cost FROM grn_item WHERE grn_id=? ORDER BY id",
             (grn1["id"],))
chg1 = H.q("SELECT charge_type, amount, cost_treatment, recipient_type, recipient_name, "
           "COALESCE(is_void,0) AS is_void FROM grn_charge WHERE grn_id=? ORDER BY id", (grn1["id"],))
import json as _json
obl = c.get("/accounts/api/supplier_open_obligations/1").get_json() or {}
pay1 = [(o["key"], o["original"], o["remaining"]) for o in obl.get("obligations", [])]
L.show("GRN-1", (grn1, items1, chg1, pay1))

i12 = next((x for x in items1 if x["mat_name"] == M12), {})
i20 = next((x for x in items1 if x["mat_name"] == M20), {})
ok_landed = (abs(float(i12.get("landed_unit_cost") or 0) - 101.875) < 0.02
             and abs(float(i20.get("landed_unit_cost") or 0) - 122.25) < 0.02)
L.check("R2-P01", "Purchases / GRN costing", "/grn",
        "GRN of 1,000x12mm @100 + 500x20mm @120, freight 3,000 capitalised, loading 1,000 booked as a separate expense",
        "freight is spread by value (101.875 and 122.25 landed), the loading expense is NOT capitalised, "
        "and the supplier bill is only the material value 160,000",
        f"items={[(x['mat_name'], x['qty'], x['landed_unit_cost']) for x in items1]}; "
        f"charges={[(x['charge_type'], x['amount'], x['cost_treatment']) for x in chg1]}; "
        f"open supplier obligations={pay1}",
        ok_landed and any(abs(x[1] - 160000) < 0.01 for x in pay1), "HIGH",
        evidence="supplier liability is exposed by accounts.supplier_open_obligations_api "
                 "(MATERIAL:<grn_id> rows), not by a pending_bill row")

L.check("R2-P02", "Purchases / Stock", "/stock_summary",
        "Stock after GRN-1", "1,000 kg of 12mm and 500 kg of 20mm on hand",
        f"12mm={L.stock(M12)} 20mm={L.stock(M20)}",
        L.stock(M12) == 1000 and L.stock(M20) == 500, "HIGH")

# ---------------------------------------------------------------- P-03 GRN-2 (same material, new price)
c.post("/grn", data=grn_payload(
    "R2-GRN-002", [(M12, 400, 110)],
    [("row-freight2", "VEHICLE_FARE", "Freight second trip", 4000, "TRANSPORTER", "R2 Rana Transport", "INVENTORY")],
    "R2 GRN-2 second batch of 12mm at a higher price"), follow_redirects=True)
grn2 = H.one("SELECT id, landed_cost_total FROM grn ORDER BY id DESC LIMIT 1")
item2 = H.one("SELECT qty, price_at_time, landed_unit_cost FROM grn_item WHERE grn_id=?", (grn2["id"],))
mat12 = H.one("SELECT total, unit_price FROM material WHERE name=?", (M12,))
# landed cost of both batches, and the deliveries that will price COGS from them
inv_val = 1000 * 101.875 + 400 * 120.0        # 149,875 landed for 1,400 kg
avg_expect = inv_val / 1400                    # 107.05 per kg
L.check("R2-P03", "Purchases / Costing", "/grn",
        "Buy the same material twice at different landed costs (101.875 then 120.00 per kg)",
        "both batches keep their own landed unit cost, so a later delivery can be costed exactly",
        f"12mm on hand={mat12['total']} kg; landed inventory value={inv_val} (avg {avg_expect:.2f}/kg); "
        f"material.unit_price (master sale price, not a cost)={mat12['unit_price']}",
        abs(float(mat12["total"]) - 1400) < 0.01
        and abs(float(item2["landed_unit_cost"]) - 120.0) < 0.01, "HIGH",
        note="There is no screen anywhere in the app that reports the 149,875 of inventory on hand — "
             "recorded separately as a reporting gap.")

# ---------------------------------------------------------------- P-04 expense separation
import json as _json
paygrn = c.get("/accounts/api/pay_grn/1").get_json() or {}
mat = paygrn.get("material") or {}
charges = paygrn.get("recipient_charges") or []
loading = next((x for x in charges if "Loading" in (x.get("label") or "")), None)
L.check("R2-P04", "Purchases / GRN expenses", "/accounts/api/pay_grn/1",
        "Inspect what GRN-1 still owes after booking a separate 1,000 loading expense",
        "the supplier is owed the material only (160,000) and the loading 1,000 is offered to its own "
        "recipient (the loading crew), never inside the supplier balance",
        f"material payable={mat.get('remaining')}; recipient charges={charges}",
        abs(float(mat.get("remaining") or 0) - 160000) < 0.01
        and loading and abs(float(loading.get("remaining") or 0) - 1000) < 0.01, "HIGH")

# ---------------------------------------------------------------- P-05 supplier part payment
cash_b = L.cash()
bal_before = float((c.get("/api/supplier_balance/1").get_json() or {}).get("balance") or 0)
pay = c.post("/add_supplier_payment", data={
    "supplier_id": "1", "amount": "60000", "date": T, "method": "Cash",
    "payment_account_id": "1", "note": "R2 part payment GRN-1"}, follow_redirects=True)
cash_a = L.cash()
bal_after = float((c.get("/api/supplier_balance/1").get_json() or {}).get("balance") or 0)
obl_after = [(o["key"], o["original"], o["paid"], o["remaining"])
             for o in (c.get("/accounts/api/supplier_open_obligations/1").get_json() or {}).get("obligations", [])]
sp = H.q("SELECT id, supplier_id, amount, method, is_void FROM supplier_payment ORDER BY id DESC LIMIT 2")
L.check("R2-P05", "Purchases / Payables", "/add_supplier_payment",
        "Pay 60,000 against the 160,000 material obligation of GRN-1 in cash",
        "cash falls by exactly 60,000, the supplier balance falls by 60,000 and the MATERIAL:1 "
        "obligation shows paid 60,000 / remaining 100,000",
        f"cash {cash_b}->{cash_a}; supplier balance {bal_before}->{bal_after}; "
        f"obligations={obl_after}; vouchers={sp}; flash={H.flash_messages(pay)}",
        abs((cash_b - cash_a) - 60000) < 0.01 and abs((bal_before - bal_after) - 60000) < 0.01
        and any(k == "MATERIAL:1" and abs(r - 100000) < 0.01 for k, _o, _p, r in obl_after), "HIGH")

print("S2 done")
