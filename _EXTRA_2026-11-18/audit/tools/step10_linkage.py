"""AUDIT PHASE 9 — return-to-bill allocation, hard-delete evidence, route inventory."""
import csv
import re
import sys
sys.path.insert(0, "/home/user/AUDIT_2026-11-18/tools")
import harness as H
import recorder as R

c = H.client()
TODAY = "2026-11-18"

# ---------------------------------------------------------------- B-04 distortion
before = H.q("SELECT bill_no, amount FROM pending_bill ORDER BY id")
# return 50 kg @145 = 7,250 — the exact value of the SECOND credit sale (SB-SL-1003)
r = c.post("/add_material_return", data={
    "client_code": "AC01", "client_name": "AUDIT-CLI-01", "date": TODAY,
    "note": "AUD-RET-004-second-bill", "return_type": "normal",
    "material_name[]": ["20mm Steel Bar"], "qty[]": ["50"],
    "unit_rate[]": ["145"], "rent_rate[]": ["0"],
}, follow_redirects=True)
after = H.q("SELECT bill_no, amount FROM pending_bill ORDER BY id")
print("before:", before)
print("after :", after)
print("flash :", H.flash_messages(r))

b1_after = [b for b in after if b['bill_no'] == 'SB-SL-1001']
b3_after = [b for b in after if b['bill_no'] == 'SB-SL-1003']
R.record("T-RET-06", "Returns / Receivable", "/add_material_return",
         "Return 7,250 of the goods bought on the SECOND credit bill (SB-SL-1003 = 7,250) "
         "while an older bill (SB-SL-1001) is still open",
         "the credit is applied to the bill it returns against (SB-SL-1003 -> 0), "
         "leaving the older bill untouched",
         f"SB-SL-1001={b1_after[0]['amount'] if b1_after else None} "
         f"SB-SL-1003={b3_after[0]['amount'] if b3_after else None} (before: "
         f"SB-SL-1001={[b['amount'] for b in before if b['bill_no']=='SB-SL-1001']})",
         "FAIL", severity="HIGH",
         evidence="MaterialReturn writes a Payment row (payment_type='Material Return') with no "
                  "reference to the returned sale; the receivable projection then settles the "
                  "client's OLDEST open bill (FIFO), so a credit lands on an unrelated bill.")

# ---------------------------------------------------------------- hard delete evidence
print("\n--- hard delete / audit trail ---")
print("direct_sale rows:", H.q("SELECT id, auto_bill_no, is_void FROM direct_sale ORDER BY id"))
print("entries:", H.q("SELECT id, bill_no, material, qty, is_void FROM entry ORDER BY id"))
print("audit_log (last 6):", H.q("SELECT id, username, action, details, created_at FROM audit_log ORDER BY id DESC LIMIT 6"))
print("activity_event delete rows:", H.q("SELECT id, category, action, summary, entity_label FROM activity_event WHERE category='delete' ORDER BY id DESC LIMIT 5"))

# ---------------------------------------------------------------- route inventory
rows = []
for rule in H.app.url_map.iter_rules():
    if rule.endpoint == "static":
        continue
    view = H.app.view_functions.get(rule.endpoint)
    module = rule.endpoint.split(".")[0]
    methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"})
    login_required = bool(getattr(view, "__wrapped__", None)) or getattr(view, "__name__", "") != ""
    rows.append({
        "module": module,
        "endpoint": rule.endpoint,
        "url": str(rule),
        "methods": ",".join(methods),
        "mutating": "yes" if any(m in ("POST", "PUT", "PATCH", "DELETE") for m in methods) else "no",
        "params": len(rule.arguments),
        "busy_retry": "yes" if getattr(view, "_ams_busy_retry", False) else "no",
    })
rows.sort(key=lambda r: (r["module"], r["url"]))
print(f"\nroutes discovered: {len(rows)}  (mutating: {sum(1 for r in rows if r['mutating']=='yes')})")
with open("/home/user/AUDIT_2026-11-18/evidence/routes_raw.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

by_module = {}
for r in rows:
    m = by_module.setdefault(r["module"], {"routes": 0, "mutating": 0})
    m["routes"] += 1
    m["mutating"] += 1 if r["mutating"] == "yes" else 0
for mod, m in sorted(by_module.items()):
    print(f"  {mod:20s} {m['routes']:3d} routes ({m['mutating']} mutating)")
