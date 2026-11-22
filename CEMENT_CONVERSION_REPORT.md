# Cement Bag → Open Cement (KG) Conversion — Final Report

Branch: `arena/5df7c646-amsv6` (base `5ab617b`)
Scope: internal inventory conversion only. No purchase, no GRN, no sale, no expense,
no supplier payable/receivable, no cash flow, no new P&L account.

---

## 1. Summary

A complete, database-backed Cement Bag → Open Cement conversion system was added to the
existing AMS ERP. A reserved, system-managed **Open Cement** category always exists and
cannot be renamed, disabled or deleted. Open Cement items are ordinary material rows
created through Inventory → Add Material, each carrying an explicit `source_material_id`
link to the bagged cement item it is produced from.

A conversion moves stock internally: the bagged item goes **OUT** in bags, the Open Cement
item goes **IN** in kilograms, both entries sharing one conversion reference. Cost is
inherited from the original GRN (Rs. 1,400 / 50 kg bag → Rs. 28/kg); the operator is never
asked for a cost. Open Cement is sold through the existing Sales module in KG (or by keg),
lost through the existing Lost Ledger, and appears in P&L exactly once. Nothing is ever
hard-deleted; reversal is guarded by downstream consumption.

**Test result: 127 passed, 5 skipped, 0 failed** (`pytest -q`, 406 s). Baseline before the
work was 91 passed, 5 skipped. `tests/test_cement_conversion.py` alone is 35 passed.

---

## 2. Where it lives

| Concern | File |
| --- | --- |
| Category helpers, reserved-category protection, item rules | `app/services/open_cement.py` |
| Conversion service (preview, create, reverse, history, detail) | `app/services/cement_conversion.py` |
| Conversion screen / history / detail / export / reverse routes | `blueprints/inventory/cement_conversion.py` |
| Screen templates | `templates/cement_conversion.html`, `templates/cement_conversion_detail.html` |
| Kept intact: Lost Ledger | `blueprints/inventory/lost_ledger.py`, `templates/lost_ledger.html` |
| Sale sheet keg helper | `templates/direct_sales.html`, `templates/_direct_sale_edit_modal.html` |
| Server-side keg → KG resolution | `app/blueprints/sales/_common.py::resolve_open_cement_item` |
| GRN exclusion | `app/blueprints/ops/grn.py`, `app/services/grn_edit.py`, `blueprints/import_export/sheets_master_supply.py` |
| Costing / P&L | `app/services/profit_loss.py`, `app/services/stock_adjustments.py` |
| Migration | `app/migrations/0018_cement_bag_to_open_cement.sql` |
| Tests (A–J + extras) | `tests/test_cement_conversion.py` |

Navigation: sidebar **Lost Ledger → Convert Cement Bags to Open Cement**
(`app/services/sidebar_nav.py`, `can_view_stock`). Lost Ledger itself is unchanged.

---

## 3. Data model

Reused tables; only additive columns and two new tables.

`models/catalog.py` — `Material`:
- `source_material_id` (FK → material.id, indexed) — explicit link to the bagged item.
- `bag_weight_kg` (Float) — optional per-item default bag weight.

`models/stock.py`:
- `CementConversion` — header: `reference`, `conversion_date`, `source_material_id`,
  `dest_material_id`, `bags_converted`, `weight_per_bag_kg`, `kg_produced`,
  `source_cost_per_bag`, `source_cost_per_kg`, `total_transferred_cost`,
  `source_stock_after`, `dest_stock_after`, `note`, `status`, `idempotency_key`,
  `created_by`, `source_entry_id`, `dest_entry_id`, `reversal_entry_id`.
- `CementConversionLayer` — one FIFO layer per conversion: `kg_qty`, `kg_unit_cost`,
  `source_bags`, `source_rate`, `source_stock_after`, `dest_stock_after`.
- `StockAdjustment.conversion_reference` — Lost Ledger link back to the conversion.

`models/__init__.py` star-imports both model modules, so `CementConversion` /
`CementConversionLayer` are available from `models`.

---

## 4. Migration 0018 — additive, idempotent, backward compatible

`app/migrations/0018_cement_bag_to_open_cement.sql` adds the two columns and two tables and
creates indexes. It is pure `ADD COLUMN` / `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF
NOT EXISTS`; no `DROP`, no `DELETE`, no table rebuild. `auto_migrate.run_sql_migrations()`
skips statements that already exist ("object already present") and blocks destructive SQL
unless `MIGRATIONS_ALLOW_DESTRUCTIVE=1`.

`test_migration_0018_upgrades_an_existing_database_copy` rewinds a real database (drops the
new columns/tables and the migration-history row, keeping every existing row), re-runs the
migration, asserts the schema is restored, then runs it a second time and asserts it changes
nothing. Existing rows survive.

---

## 5. Reserved "Open Cement" category

`ensure_open_cement_category()` is idempotent (one row, `is_active=True`), runs at boot from
`app/__init__.py` and from the materials screen. `protect_open_cement_category()` refuses
rename and disable/toggle. The category wipe in
`app/blueprints/misc/_wipe_delete_selected_data.py` preserves it and only re-categorises
non-open items. No brand names are hard-coded anywhere in Python; `OPEN_CEMENT_CATEGORY_NAME`
is the only literal.

---

## 6. Open Cement items and the explicit source link

Add/Edit Material (`app/blueprints/misc/materials.py`, `templates/materials.html`):
- Choosing the Open Cement category forces unit **KG** and reveals a required
  **Linked Bagged Cement Item** select.
- The link is validated server-side: it must exist and must not itself be Open Cement.
- The link is by **id**, never by name matching, so renaming a bagged item keeps the
  relationship (rename cascades `Entry.material`/`GRNItem.mat_name` as before).
- `merge_materials` refuses to merge a bagged item that is linked to Open Cement, so the
  link can never be left dangling.
- Edit follows the category, including a category move onto/off Open Cement.

---

## 7. GRN exclusion — backend only

Open Cement is excluded in three independent server-side places:
1. `app/blueprints/ops/grn.py` — the GRN material list query and the POST handler.
2. `app/services/grn_edit.py::sync_items_from_form` — the GRN edit sync path.
3. `blueprints/import_export/sheets_master_supply.py` — the GRN/Excel import.

A manipulated API/form submission naming an Open Cement item is rejected with a flash and no
`GRN`/`GRNItem` row is created (`test_b_grn_post_rejects_open_cement`). Historical GRNs in
other categories are untouched (`test_b_historical_grn_rows_are_untouched`).

---

## 8. Conversion screen and form

Route `/inventory/cement_conversion`, title **"Cement Bag to Open Cement Conversion"**.
Fields: Conversion Date, Source Cement Item, Source Available Bag Stock (live), Destination
Open Cement Item, Conversion Qty (bags), Weight per Bag (default 50 kg), Total Open Cement
Produced (auto), Original Purchase Cost (auto), Total Transferred Cost (auto), Remarks, Save.

Selecting a source immediately shows its available bags and only the Open Cement items linked
to it. The preview endpoint `GET /inventory/cement_conversion/preview` recomputes everything
server-side (available stock as of the date, kg produced, cost per bag/kg, total cost,
sufficient-stock flag).

---

## 9. Conversion service — validation, atomicity, costing

`create_conversion(...)` validates, in order: source exists and is not Open Cement;
destination exists, is Open Cement, and is linked to that source (cross-brand / no-link
rejected); bags > 0 and not over available stock as of the date; weight per bag > 0.
Idempotency key (≤64 chars) returns the existing conversion on resubmit.

Everything runs inside one write transaction (`begin_write_transaction` on SQLite) with a
serialising lock, so two simultaneous conversions cannot both consume the same bags. Any
failure rolls the whole thing back — no partial OUT/IN.

Costing is inherited, never asked: the source's FIFO lot rate as of the conversion date
(Rs. 1,400/bag ÷ 50 kg = Rs. 28/kg). No supplier payable, no cash movement, no new purchase,
no artificial P&L.

---

## 10. Stock movements and FIFO traceability

Two linked `Entry` rows share `source_module='inventory'`, `source_table='cement_conversion'`,
`source_id`, and `source_bill_no` = the conversion reference:
- source `OUT` in bags, `client_category='Cement Conversion'`;
- destination `IN` in kg.

`Material.total` is rebuilt from non-void entries. A `CementConversionLayer` row carries the
kg quantity and the inherited per-kg rate, and `profit_loss._Projection` exposes it as a dated
synthetic FIFO lot (`cement_conversion_layer_id`). GRN quantities are never altered. Total
inventory value is preserved: value leaves the bagged item and enters the open item at the
same rate.

---

## 11. Open Cement sales — KG and keg

Existing Sales is reused unchanged in behaviour. Open Cement stock is in KG and the deduction
is always KG. The sale sheet (and the saved-bill edit modal) shows a keg helper on Open
Cement rows only: `keg_qty[]` × `keg_kg[]` (default 50) → KG.

Server-side, `resolve_open_cement_item` derives the KG quantity for Open Cement lines in both
`/add_direct_sale` and `/edit_bill/DirectSale/<id>`; a keg helper on a non-Open-Cement row is
ignored, so bagged sales stay in bags. Normal price/discount/payment/customer/credit/invoice
flow, stock deduction, FIFO cost at sale (`cost_rate_at_sale`), customer ledger, cash flow,
P&L and invoices/reports all behave exactly as before. Bagged cement sales are unaffected.

---

## 12. Lost Ledger

Reused as-is. Any Open Cement item can be selected; the loss is deducted in KG and recorded in
the Lost Ledger with `conversion_reference` pointing at the conversion whose FIFO layer
supplied the cost. `create_stock_adjustment` refuses a LOSS greater than available Open
Cement stock and derives the cost from the same FIFO lot (`estimate_adjustment_conversion_reference`).
The loss appears in P&L exactly once (`loss_rows == 1`), with no double counting.

---

## 13. Conversion history, report, reversal, audit

`/inventory/cement_conversion` history table columns: Conversion ID, Date, Source,
Destination, Bags, Source Unit, KG Produced, Dest Unit, Weight per Bag, Cost per Bag,
Total Transferred Cost, Remaining Source Stock, User, Remarks, Status. Filters: date range,
source, destination, free-text ID. CSV export at `/inventory/cement_conversion/export`.
Detail view at `/inventory/cement_conversion/<id>` shows header, both entries, FIFO layers,
remaining kg, reversal entry and the accounting audit trail.

Reversal (`POST .../reverse`): if no downstream consumption exists, the source OUT is voided,
the destination IN is kept, and a compensating destination OUT is added
(`transaction_type='Conversion Reversal'`); status becomes `REVERSED`. If stock has already
been sold or lost, reversal is refused. Nothing is ever hard-deleted. Every create/reverse
writes an `AccountingAuditLog` row (`entity_type='CementConversion'`).

---

## 14. Security, permissions, concurrency, idempotency

- All conversion endpoints map to `can_view_stock` in `ENDPOINT_PERMISSION_MAP`
  (`app/services/constants.py`); a user without it gets a redirect/403
  (`test_j_permission_denied_for_unauthorized_role`).
- CSRF is enforced by the existing hook; `test_j_csrf_token_required` forces
  `AMS_CSRF_ALWAYS` and proves a token-less POST is refused while the same payload with the
  session token succeeds.
- Back-date policy: `authorise_edit` blocks a `restrict_backdated_edit` account from creating
  a conversion older than the 90-day window, and writes an override audit when allowed
  (`test_j_backdate_restricted_user_cannot_convert_old_date`).
- Idempotent duplicate submit (`test_j_duplicate_submit_is_idempotent`), unlinked destination
  refused (`test_j_save_rejects_unlinked_destination`), insufficient stock refused with full
  rollback (`test_j_insufficient_stock_is_refused_and_atomic`), invalid destination leaves no
  partial conversion (`test_j_rollback_on_invalid_destination_leaves_no_partial_conversion`),
  and two simultaneous HTTP clients produce exactly one conversion
  (`test_j_concurrent_conversions_are_serialised`).

---

## 15. Acceptance evidence A–J

| | Acceptance | Test(s) |
| --- | --- | --- |
| A | Category idempotency / reserved | `test_a_open_cement_category_is_idempotent_and_reserved`, `test_a_open_cement_category_created_on_boot` |
| B | GRN exclusion incl. API | `test_b_grn_material_list_excludes_open_cement`, `test_b_grn_post_rejects_open_cement`, `test_b_grn_edit_sync_rejects_open_cement`, `test_b_historical_grn_rows_are_untouched` |
| C | 100 bags → 99 + 50 kg, GRN stays 100 | `test_c_conversion_1_bag_leaves_99_bags_and_50_kg`, `test_c_preview_reports_cost_and_stock`, `test_c_rejects_over_stock`, `test_c_rejects_negative_and_zero`, `test_c_rejects_cross_brand_and_unlinked_destination` |
| D | Sale 45 kg → 5 kg | `test_d_sale_of_45kg_leaves_5kg`, `test_d_open_cement_sale_does_not_touch_bagged_sales`, `test_d_keg_helper_*`, `test_d_edit_sale_keg_helper_derives_kg`, `test_d_edit_modal_offers_keg_helper_for_saved_open_cement_sale` |
| E | Loss 5 kg → 0, P&L once | `test_e_loss_of_5kg_empties_stock_and_posts_pnl_once`, `test_e_loss_cannot_exceed_available_open_cement` |
| F | Reconciliation 1,260 + 140 + 0 = 1,400 | `test_f_reconciliation_sold_plus_loss_plus_remaining` |
| G | Rates / layer traceability | `test_g_multiple_grn_rates_keep_layer_traceability` |
| H | Brands independent | `test_h_brands_are_independent` |
| I | Full regression | whole suite: **127 passed, 5 skipped** |
| J | Security / concurrency | `test_j_*` (duplicate, simultaneous, insufficient stock, unauthorized, invalid destination, atomic rollback, CSRF, back-date) |

Also covered: migration upgrade of an existing DB copy, category visibility/filtering, KG +
source link on the item, and keg handling on add and edit.

End-to-end smoke on an isolated database confirmed: conversion `SB-CC-1000`, 100 bags →
5,000 kg at Rs. 28/kg = Rs. 140,000; bag stock 100→0, open 0→5,000; OUT 100 / IN 5,000
sharing one conversion id; sale 45 kg leaves 4,955 kg; loss 5 kg costs Rs. 140; P&L revenue
1,800, material cost 1,260, other losses 140, profit 400, `formula_difference` 0.0.

---

## 16. Deployment and residual notes

- Deploy is a normal boot: `ensure_open_cement_category()` runs at startup and migration 0018
  applies itself. No manual SQL, no drop/recreate.
- Backward compatible: existing databases gain two nullable columns and two new tables; all
  historical GRNs, entries, adjustments, sales and reports are untouched (regression suite
  green).
- `full_db_sync/engine.py` `_LEGACY_DEFAULTS` was extended with `material.source_material_id`,
  `material.bag_weight_kg` and `stock_adjustment.conversion_reference` so a legacy production
  snapshot still uploads.
- Decision-ledger category tests were updated for the additional reserved category (5 groups,
  alphabetical, Uncategorised last).
- Residual risk: none known. Open Cement stock is only ever created by a conversion, so it can
  never be purchased or received against a GRN; the reserved category cannot be renamed or
  deleted through the UI; and no code path can hard-delete a conversion.
