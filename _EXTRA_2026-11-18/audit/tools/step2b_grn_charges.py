"""AUDIT STEP 2b — GRN #2 with landed-cost charge lines (the real wizard fields)."""
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H

c = H.client()

def grn(supplier, bill, lines, charges=(), include_charges=True, date="2026-11-18"):
    """lines: [(material, qty, rate)] ; charges: [(type, label, amount, recipient_type, name, treatment)]"""
    form = {
        "action": "add", "supplier": supplier, "manual_bill_no": bill,
        "date": date, "allocation_basis_default": "VALUE",
        "mat_name[]": [l[0] for l in lines],
        "qty[]": [str(l[1]) for l in lines],
        "price[]": [str(l[2]) for l in lines],
    }
    if charges:
        form["charge_supplier_billing_present"] = "1"
        form["charge_type[]"] = [x[0] for x in charges]
        form["charge_label[]"] = [x[1] for x in charges]
        form["charge_amount[]"] = [str(x[2]) for x in charges]
        form["charge_recipient_type[]"] = [x[3] for x in charges]
        form["charge_recipient[]"] = [x[4] for x in charges]
        form["charge_treatment[]"] = [x[5] for x in charges]
        form["charge_basis[]"] = ["VALUE" for _ in charges]
        form["charge_row_key[]"] = [f"row-{i}" for i in range(len(charges))]
        if include_charges:
            form["charge_include_in_supplier[]"] = [f"row-{i}" for i in range(len(charges))]
    return c.post("/grn", data=form, follow_redirects=True)


# GRN 2: 400 kg 12mm @110 = 44,000  + transport 2,000 + loading 3,000 (both INVENTORY, billed to supplier)
r = grn("AUDIT-SUP-01", "AUD-GRN-002",
        [("12mm Steel Bar", 400, 110)],
        charges=[("TRANSPORT", "Audit transport", 2000, "TRANSPORTER", "Audit Truck", "INVENTORY"),
                 ("LOADING", "Audit loading", 3000, "LABOUR", "Audit Labour", "INVENTORY")])
print("GRN2:", H.brief(r))

print("\n--- grn headers ---")
for row in H.q("SELECT id, auto_bill_no, manual_bill_no, supplier_material_amount, capitalised_charge_total,"
               " landed_cost_total, expense_charge_total, include_charges_in_supplier FROM grn ORDER BY id"):
    print("  ", row)

print("\n--- grn charges ---")
for row in H.q("SELECT id, grn_id, charge_type, label, amount, cost_treatment, recipient_type,"
               " recipient_name, billing_party, is_void FROM grn_charge ORDER BY id"):
    print("  ", row)

print("\n--- grn items ---")
for row in H.q("SELECT id, grn_id, mat_name, qty, price_at_time, allocated_charges_minor,"
               " line_landed_cost_minor, landed_unit_cost FROM grn_item ORDER BY id"):
    print("  ", row)

print("\n--- stock after 2 GRNs ---")
for row in H.q("SELECT id, name, total, unit_price FROM material ORDER BY id"):
    print("  ", row)

print("\n--- supplier obligations (what the supplier is owed) ---")
for t in ("supplier_payment", "account_transaction", "pending_bill"):
    print(f"  {t}: {H.ledger_dump(t)}")
