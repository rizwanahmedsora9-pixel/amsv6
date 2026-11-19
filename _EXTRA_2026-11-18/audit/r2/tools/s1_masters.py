"""S1 — master data + charts. Every write goes through the app's own forms."""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L

c = H.client()
print("== S1 masters ==")

MATS = [("R2 12mm Steel Bar", "Kg"), ("R2 20mm Steel Bar", "Kg"), ("R2 Cement Bag", "Bag")]
for name, unit in MATS:
    c.post("/add_material", data={"material_name": name, "material_unit": unit, "category_id": ""},
           follow_redirects=True)
mats = H.q("SELECT id, name, unit, total, unit_price FROM material ORDER BY id")
L.show("materials", [(m["name"], m["unit"], m["total"]) for m in mats])
L.check("R2-M01", "Masters / Material", "/add_material",
        "Create the three materials the scenarios trade in",
        "all three rows exist with unit and zero stock",
        f"{[(m['name'], m['unit'], m['total']) for m in mats]}",
        len([m for m in mats if m["name"].startswith("R2 ")]) == 3, "LOW")

# duplicate material name must be refused (or at least not silently duplicated)
before = len(H.q("SELECT id FROM material"))
dup = c.post("/add_material", data={"material_name": "R2 12mm Steel Bar", "material_unit": "Kg",
                                    "category_id": ""}, follow_redirects=True)
after = len(H.q("SELECT id FROM material"))
L.check("R2-M02", "Masters / Material", "/add_material",
        "Re-submit an existing material name",
        "duplicate refused (no second row with the same name)",
        f"rows {before}->{after}; flash={H.flash_messages(dup)}",
        after == before, "LOW")

# ---- clients --------------------------------------------------------------
c.post("/add_client", data={"name": "R2-CLI-01", "code": "AC01", "category": "Retail",
                            "phone": "0301-0000001", "address": "Islamabad", "opening_balance": "0"},
       follow_redirects=True)
c.post("/add_client", data={"name": "R2-CLI-02", "code": "AC02", "category": "Wholesale",
                            "phone": "0301-0000002", "address": "Rawalpindi", "opening_balance": "5000"},
       follow_redirects=True)
clients = H.q("SELECT id, name, code, opening_balance FROM client ORDER BY id")
named = {x["name"]: x for x in clients}
c2 = named.get("R2-CLI-02")
st_ledger, ledger_txt = L.page(f"/financial_ledger/{c2['id']}") if c2 else (0, "")
ledger_has_open = bool(c2) and ("5,000" in ledger_txt or "5000" in ledger_txt)
L.show("clients", [(x["name"], x["code"], x["opening_balance"]) for x in clients])
L.check("R2-M03", "Masters / Client", "/add_client",
        "Create two clients; the second carries a 5,000 opening balance",
        "both exist and the 5,000 opening balance is carried into the client's financial ledger",
        f"clients={[(x['name'], x['code'], x['opening_balance']) for x in clients]}; "
        f"/financial_ledger/{c2['id'] if c2 else '?'} shows 5,000={ledger_has_open}",
        bool(named.get("R2-CLI-01")) and bool(c2) and ledger_has_open, "MEDIUM",
        evidence=f"client.opening_balance={c2['opening_balance'] if c2 else None}")

dup_code = c.post("/add_client", data={"name": "R2-CLI-03-dupcode", "code": "AC01",
                                       "category": "Retail", "phone": "03", "address": "x",
                                       "opening_balance": "0"}, follow_redirects=True)
c3 = [x for x in H.q("SELECT id, name, code FROM client") if x["name"] == "R2-CLI-03-dupcode"]
L.check("R2-M04", "Masters / Client", "/add_client",
        "Create a client reusing an existing code (AC01)",
        "duplicate client code refused — codes are the posting key for payments and returns",
        f"row created={c3}; flash={H.flash_messages(dup_code)}", not c3, "HIGH")

bad_open = c.post("/add_client", data={"name": "R2-CLI-BADOPEN", "code": "AC09", "category": "Retail",
                                       "phone": "03", "address": "x", "opening_balance": "abc"},
                  follow_redirects=True)
cbad = [x for x in H.q("SELECT id, name, opening_balance FROM client") if x["name"] == "R2-CLI-BADOPEN"]
L.check("R2-M05", "Masters / Client", "/add_client",
        "Submit a non-numeric opening balance",
        "refused with a validation message, nothing stored",
        f"row={cbad}; flash={H.flash_messages(bad_open)}", not cbad, "LOW")

# ---- suppliers ------------------------------------------------------------
for nm in ("R2-SUP-01", "R2-SUP-02"):
    c.post("/add_supplier", data={"name": nm, "phone": "0300-0000000", "address": "Vendor",
                                  "opening_balance": "0"}, follow_redirects=True)
sups = H.q("SELECT id, name FROM supplier ORDER BY id")
L.check("R2-M06", "Masters / Supplier", "/add_supplier",
        "Create two suppliers", "both supplier rows exist",
        f"{[(s['name']) for s in sups]}", len(sups) == 2, "LOW")

# ---- accounts (chart) -----------------------------------------------------
c.post("/accounts/accounts/add", data={
    "name": "R2 Main Cash", "class_category": "Assets", "class_subcategory": "Cash",
    "class_account_type": "Main Cash", "channel": "cash", "account_status": "active",
    "opening_amount": "100000", "opening_position": "debit",
    "opening_effective_date": "2026-11-01"}, follow_redirects=True)
bank_resp = c.post("/accounts/accounts/add", data={
    "name": "R2 Bank", "class_category": "Assets", "class_subcategory": "Bank",
    "class_account_type": "Operating Bank", "channel": "bank", "account_status": "active",
    "bank_name": "Meezan", "account_holder_name": "Ahmed Cement",
    "account_number": "0101-0000001", "branch_code": "0101",
    "opening_amount": "50000", "opening_position": "debit",
    "opening_effective_date": "2026-11-01"}, follow_redirects=True)
L.show("bank flash", H.flash_messages(bank_resp))

# an invalid chart triple must be refused server-side (this is the guard that
# stopped an earlier attempt at Assets/Bank/Bank)
bad = c.post("/accounts/accounts/add", data={
    "name": "R2 Bad Account", "class_category": "Assets", "class_subcategory": "Bank",
    "class_account_type": "Bank", "channel": "bank", "opening_amount": "1",
    "bank_name": "x", "account_holder_name": "y"}, follow_redirects=True)
bad_row = H.one("SELECT id FROM account WHERE name='R2 Bad Account'")
L.check("R2-M07b", "Accounts / Chart", "/accounts/accounts/add",
        "Submit an account whose category/subcategory/type combination is not in the chart",
        "refused with a validation message and nothing stored",
        f"row={bad_row}; flash={H.flash_messages(bad)}", not bad_row, "LOW")
accts = H.q("SELECT id, name, balance FROM account ORDER BY id")
L.show("accounts", accts)
cash_id = accts[0]["id"] if accts else None
bank_id = accts[1]["id"] if len(accts) > 1 else None
L.check("R2-M07", "Accounts / Chart", "/accounts/accounts/add",
        "Open a Main Cash (100,000) and a Bank (50,000) account",
        "both accounts exist with the opening balance postable",
        f"{accts}", len(accts) == 2 and L.f(accts[0]['balance']) == 100000, "HIGH")

# ---- expense meta (needed by the accounts expense form) -------------------
st, cat = L.post_json(c, "/cash_flow/meta/categories", {"name": "R2 Shop Rent", "direction": "out"})
st2, sub = L.post_json(c, "/cash_flow/meta/categories",
                       {"name": "R2 Rent Sub", "direction": "out", "parent_id": (cat or {}).get("category", {}).get("id")})
st3, party = L.post_json(c, "/cash_flow/meta/parties", {"name": "R2 Landlord", "party_type": "person"})
exp_cat = (cat or {}).get("category", {}).get("id")
exp_party = (party or {}).get("party", {}).get("id")
L.show("expense meta", (st, cat, st3, party))
L.check("R2-M08", "Accounts / Cash-flow meta", "/cash_flow/meta/categories",
        "Quick-add an expense category and a payee",
        "both created and their ids returned for the expense form",
        f"category={cat}; party={party}", bool(exp_cat) and bool(exp_party), "MEDIUM")

# ---- delivery person ------------------------------------------------------
c.post("/delivery_persons/add", data={"name": "R2 Driver", "phone": "0300-0000009",
                                      "note": "audit driver"}, follow_redirects=True)
drv = H.one("SELECT id, name FROM delivery_person ORDER BY id DESC LIMIT 1")
L.check("R2-M09", "Masters / Delivery", "/delivery_persons/add",
        "Create a delivery person", "driver row exists and can be attached to sales",
        f"{drv}", bool(drv), "LOW")

# ---- service (billable extra) --------------------------------------------
c.post("/services/add", data={"service_name": "R2 Delivery Charge", "service_unit": "Trip",
                              "service_code": "R2SVC"}, follow_redirects=True)
svc = H.one("SELECT id, name FROM service ORDER BY id DESC LIMIT 1") if \
    H.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name='service'") else None
L.check("R2-M10", "Masters / Services", "/services/add",
        "Create a chargeable service used for delivery rent lines",
        "service row exists", f"{svc}", bool(svc), "LOW")

print("S1 done")
