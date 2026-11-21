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
