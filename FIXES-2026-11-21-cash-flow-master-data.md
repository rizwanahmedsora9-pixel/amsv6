# Cash Flow master data — edit, delete and suspend for categories

**Date:** 2026-11-21 · **Branch:** `arena/79d3728d-amsv6` · **Status:** fixed, tested

The Cash Flow screen owns three user-managed masters — categories,
sub-categories and parties. *Master Data Management* offered Save, Disable /
Restore and Delete on each row. Six defects made those three verbs disagree
with each other; all are fixed and covered by
`tests/test_cash_flow_master_data.py` (47 tests, driven through the real HTTP
routes).

## What was wrong

| Symptom | Cause | Now |
|---|---|---|
| Suspending a category was pointless: recording an entry whose *typed* category name matched a disabled row silently set `is_active = True`. | `_cf_resolve_category` fell through the "active only" lookup into `save_cf_category(name)`, which revives any inactive row it matches. The sub-category path did the same. | A disabled name is refused with *"Category 'X' is disabled. Restore it or pick another category."* Nothing is written. `save_cf_category` only revives when the caller explicitly asks (`revive=True`). |
| Same for a disabled **sub**-category typed on a new entry. | Same fall-through. | Refused with the same wording; the sub-category stays suspended. |
| Adding an existing category through the Add form silently rewrote its **direction** (Received ⇄ Spent) and its notes, and un-suspended it. | `save_cf_category` treated any name match as "update". | Add is strict: an existing active row is reported (*"already exists — edit or disable it"*), a disabled one is reported (*"…but is disabled. Restore it"*). The explicit **Restore** button is the only way back. Same for sub-categories and parties. |
| The Delete button was *enabled* for a category used only through a sub-category, and pressing it was refused. | `cf_used_category_ids()` counted only `cash_flow_entry.category_id`; the shop's normal rows point at the sub-category. | The helper now includes categories reached through a used sub-category, so the row renders a disabled Delete with the reason. |
| Deleting an unused category ran a bulk `DELETE` on its sub-categories while the relationship was possibly loaded. | `CashFlowSubcategory.query…delete(synchronize_session=False)` detached children the session still held. Works on SQLite by luck; on Postgres the parent delete hits the live FK rows. | Children are deleted through the session, one row each, then the parent. |
| A POST without its `id` field reached `db.session.get(Model, None)` on every master-data action → `SAWarning: fully NULL primary key identity…`, scheduled to become an error. | No id guard. | `_cf_get_id` / `_cf_lookup` treat a blank id as "not found" and flash *"Category not found."* instead of querying with `NULL`. |

## The rules the panel now enforces

* **Edit** renames in place. Every `cash_flow_entry` keeps pointing at the same
  row and shows the new name; a rename can never steal another master's name
  (case-insensitive, including a *disabled* one), and a bogus direction leaves
  the stored one untouched.
* **Delete** is offered only when no entry uses the master — directly, through
  a sub-category, or even by a voided row. Deleting an unused category removes
  its unused sub-categories with it.
* **Disable** (suspend) works on used masters too, is lossless (name,
  direction and notes survive) and reversible through **Restore**. A disabled
  master is not offered for new entries, and never gets revived implicitly — not
  by the Add form, not by an entry that types its name, and not by the JSON
  quick-add the Accounts expense form uses.

The JSON quick-add endpoints (`/cash_flow/meta/categories|subcategories|parties`)
keep their idempotent "make sure this exists" contract via `reuse_active=True`:
reusing an active master returns it untouched (`created: false`), while a
disabled one is still a `400`.

## Note on an intentional stop

`scripts`/data entry that types the name of a *disabled* category now gets a
message instead of a silently resurrected master. That is deliberate — the
suspension is the shop's decision. The route to use a retired category again is
**Restore** in Master Data Management.

---

## Does a suspend in Cash Flow reach the Accounts expense form in real time?

**Yes — both the dropdown and the server.** Accounts keeps no copy of these
masters; it reads the same rows on every request.

* `blueprints/accounts/dashboard.py` builds the expense form from
  `CashFlowCategory.query.filter(is_active == True, direction in ('out','both'))`
  and `CashFlowSubcategory` joined to an `is_active` parent — so a category's
  *sub-categories* vanish with it. Particles: `CashFlowParty.is_active`.
* The template renders those lists straight into
  `#expense_category_id`, `#expense_subcategory_id`, `#expense_party_id`
  (`templates/accounts/dashboard.html`), with JS narrowing sub-categories to the
  chosen category. Edit → the next page load shows the new name; Suspend /
  Restore / Delete → the option is gone / back / gone, same id.
* The POST `/accounts/transactions/new` path calls
  `save_manual_cash_flow_entry(..., create_missing=False)`, which re-validates
  server-side. A stale tab that submits a since-suspended id is refused
  (`Selected category is missing or inactive.` /
  `Sub-category 'X' is disabled. Restore it or pick another sub-category.` /
  the party requirement) and **no entry, no account movement, no balance change**
  is written. Covered by four tests.

### One thing that had to be protected

Suspension must retire a master for **new** entries only — never freeze history.
`update_manual_cash_flow_entry` re-resolves the classification with
`create_missing=False`, so without care, correcting the description of an old
expense would be refused the moment its category was suspended.
`_cf_resolve_category` / `_cf_resolve_subcategory` now take
`allow_inactive_id`: the entry's **own** current category/sub-category stays
accepted even if suspended; any *other* suspended master still needs a Restore.
The edit also leaves the masters suspended (an edit is not a restore).

## Blast radius of the change

| Surface | Status |
|---|---|
| Only caller of the master-data helpers outside the service | `app/blueprints/reports/cash.py` (already updated) |
| `save_manual_cash_flow_entry` callers | Cash Flow page (`create_missing=True`) and Accounts expense (`False`); validated above |
| Accounts edit path | `create_missing=False` + own-master allowance; tested |
| Entry lifecycle | record / edit / void / restore balances verified end-to-end |
| Reporting pages | `/cash_flow`, `/cash_flow_differences`, `/financial_tracker`, `/daily_reconciliation`, `/profit_reports`, CSV + PDF export all render 200 |
| Direction rules | unchanged — an income category is still refused on a Spent row |
| Public signatures | `save_cf_*` gained optional `revive` / `reuse_active` (defaults preserve caller behaviour); everything else unchanged |
| Database schema | **no migration** — no column or index added or changed |
| Full suite | **84 passed, 5 skipped** (was 30 / 5 before this work) |
