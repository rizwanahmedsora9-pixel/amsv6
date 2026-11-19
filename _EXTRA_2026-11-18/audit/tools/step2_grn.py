"""AUDIT STEP 2 — GRN (purchase receiving) with landed costs."""
import json
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H

c = H.client()
TODAY = "2026-11-18"

form = {
    "action": "add",
    "supplier": "AUDIT-SUP-01",
    "manual_bill_no": "AUD-GRN-001",
    "date": TODAY,
    "note": "Audit purchase 2 lines + charges",
    "loading_cost": "2000",
    "freight_cost": "3000",
    "other_expense": "1000",
    "adjustment_amount": "0",
    "discount": "0",
    "tax_percent": "0",
    "tax_amount": "0",
    "allocation_basis_default": "VALUE",
    "mat_name[]": ["12mm Steel Bar", "20mm Steel Bar"],
    "qty[]": ["1000", "500"],
    "price[]": ["100", "120"],
}
r = c.post("/grn", data=form, follow_redirects=True)
print("GRN POST:", H.brief(r))

print("\n--- grn header ---")
for row in H.q("SELECT id, auto_bill_no, supplier, supplier_material_amount, capitalised_charge_total,"
               " landed_cost_total, expense_charge_total, include_charges_in_supplier, paid_amount,"
               " payment_type, is_void FROM grn"):
    print("  ", row)

print("\n--- grn items (landed cost allocation) ---")
for row in H.q("SELECT id, mat_name, qty, price_at_time, base_rate_minor, allocated_charges_minor,"
               " line_landed_cost_minor, landed_unit_cost, allocation_basis FROM grn_item"):
    print("  ", row)

print("\n--- stock ---")
for row in H.q("SELECT id, name, total, unit_price FROM material ORDER BY id"):
    print("  ", row)

print("\n--- supplier payable sources ---")
print("  supplier_payment rows:", H.ledger_dump("supplier_payment"))
for t in ("grn_charge", "account_transaction", "entry", "pending_bill", "payment"):
    print(f"  {t}: {H.ledger_dump(t)} rows")

print("\n--- grn charges ---")
if "grn_charge" in H.tables():
    for row in H.q("SELECT * FROM grn_charge"):
        print("  ", row)
