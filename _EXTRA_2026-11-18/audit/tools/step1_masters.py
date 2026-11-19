"""AUDIT STEP 1 — master data + cash account, created ONLY through the app's own routes."""
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H

c = H.client()
print("logged in as Admin\n")

def show(label, resp):
    b = H.brief(resp)
    print(f"{label:38s} HTTP {b['status']}  {b['messages']}")
    return resp

# ---- materials -------------------------------------------------------------
for name, unit in (("12mm Steel Bar", "Kg"), ("20mm Steel Bar", "Kg")):
    show(f"add_material {name}", c.post("/add_material", data={
        "material_name": name, "material_unit": unit, "category_id": "",
    }))

# ---- supplier & client -----------------------------------------------------
show("add_supplier", c.post("/add_supplier", data={
    "name": "AUDIT-SUP-01", "phone": "0300-0000000", "address": "Audit vendor",
    "opening_balance": "0",
}))
show("add_client", c.post("/add_client", data={
    "name": "AUDIT-CLI-01", "code": "AC01", "category": "Retail",
    "phone": "0301-0000000", "address": "Audit customer", "opening_balance": "0",
}))

# ---- cash account (minimal attempt; the app tells us what it needs) --------
show("add_account (minimal)", c.post("/accounts/add", data={
    "name": "AUDIT Cash", "channel": "cash", "opening_amount": "0",
    "opening_position": "debit",
}))

print("\n--- masters created ---")
print("materials:", H.q("SELECT id, name, code, unit FROM material ORDER BY id"))
print("suppliers:", H.q("SELECT id, name FROM supplier"))
print("clients  :", H.q("SELECT id, code, name, category FROM client ORDER BY id"))
print("accounts :", H.q("SELECT id, name, channel, category, account_type, balance FROM account ORDER BY id"))
