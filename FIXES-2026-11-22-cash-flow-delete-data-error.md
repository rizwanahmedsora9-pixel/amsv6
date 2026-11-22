# Cash Flow — "it did not delete" and the generic data-error flash

**Date:** 2026-11-22 · **Scope:** Cash Flow Master Data (categories, sub-categories,
parties) · **Follows:** `FIXES-2026-11-21-cash-flow-master-data.md`,
`FIXES-2026-11-22-cash-flow-master-data-runtime-actions.md`

## What was reported

> last PR we made did not delete and gave this error:
> **The operation could not be completed due to a data error. No changes were
> saved — please retry, and contact support if the problem persists.**

The reproduction asked for: create a category, post transactions in it, suspend
it, edit it, remove all of its entries to empty it, then delete it.

## What the reproduction found

The lifecycle was replayed against an isolated copy of the shop's own database
(`OLD DATABASES …/AMS_FULL_20261121-071449.amsdb`) through the real HTTP routes.
Two different defects, one per symptom:

### 1. "Remove all entries, then delete" can never work — by design

`void_entry` is a **void**, not a delete (`void_manual_cash_flow_entry`): the
money effect is reversed, but the `cash_flow_entry` row, its `category_id` /
`subcategory_id` and its whole `cash_flow_entry_audit` trail are kept on purpose
(documented in `void_purge.py`: history is never thrown away). Meanwhile
`_cf_category_is_used()` counts **every** entry row, voided or not.

So the sequence ended in exactly one place: a category whose remaining rows are
all voided, a disabled Delete button, and — if the button was forced — the
refusal *"This category is linked to: CashFlowEntry … Cannot delete"*. The
operator had **no** path that actually emptied a category.

Fixed by giving the operator the action they were describing:

* `purge_cf_category_entries()` (service) voids each still-live entry through
  the audited void path, deletes the `cash_flow_entry_audit` children, deletes
  the entry rows, and removes the `account_transaction` rows those entries
  owned (they are already reversed, so no balance moves).
* Route actions `purge_category_entries` / `purge_subcategory_entries`
  (`POST /cash_flow`), wrapped in one transaction: on any failure everything
  rolls back and a **readable** message is flashed.
* Master Data now shows **“Remove entries & delete”** next to the disabled
  Delete on a used row, with the number of transactions it will remove, and the
  help text explains that a voided row still counts as history.  The count is
  de-duplicated (`cf_category_entry_counts`): one row normally carries both
  `category_id` and `subcategory_id`, and adding the two `count()`s reported
  double the real number.

### 2. The genuine data-error flash: deleting a party that only a GRN uses

Sweeping every master row in the real database found exactly one row that
produced the sanitised flash instead of a refusal:

```
party 21 'Pioneer Labour' → IntegrityError: FOREIGN KEY constraint failed
                            [SQL: DELETE FROM cash_flow_party WHERE id = 21]
```

`cash_flow_party` is also the **recipient master** for GRN costs:

* `grn_charge.recipient_party_id   → cash_flow_party.id`
* `supplier_payment.recipient_party_id → cash_flow_party.id`

`delete_cf_party()` only counted `cash_flow_entry.party_id`, so a party used
only by a GRN charge (and the payment that settled it) looked unused: the panel
offered Delete, the ORM sent the DELETE, SQLite refused it, and
`app/hooks.py:_safe_flash_text` turned the driver text into
*"The operation could not be completed due to a data error…"*.

Fixed by refusing **before** the database is touched:

* `_cf_party_reference_counts()` counts GRN charges and supplier payments;
  `delete_cf_party()` refuses with
  *"This party is still used by 1 cash-flow transaction(s), 1 in GRN charges,
  1 in Supplier payments. Cannot delete — disable it instead, or remove those
  links first."*
* `cf_used_party_ids()` (which drives the panel) now includes those
  references, so the Delete button is correctly disabled for such a party.
* Category / sub-category refusals were reworded to be explicit about voids and
  to report how many rows block them.

No schema change, no migration.

## What the operator does now

| Want | Do this |
|---|---|
| Stop using a category but keep history | **Disable** (reversible via **Restore**) |
| Delete a category with no transactions | **Delete** (unchanged) |
| Delete a category that has transactions (voided or not) | **Remove entries & delete** — money is reversed per entry, the entry rows and their ledger rows go, then the category and its unused sub-categories go |
| Delete one sub-category that has transactions | same button on the sub-category row |
| Delete a party a GRN/cost uses | **Disable**, or remove the GRN link first — Delete now says so |

Deletions re-baseline the boot health snapshot (`note_db_shrink`), so a
deliberate cleanup is not mistaken for data loss at the next restart.

## Verification

* Replayed the reported lifecycle on a copy of the real database: category
  created → 2 transactions → suspended → renamed → one row voided →
  Delete refused with a readable message → **Remove entries & delete** → the
  category, its sub-category, both entry rows, their audit rows and both ledger
  rows are gone; the cash account balance is exactly the opening figure.
* GRN-linked party delete: readable refusal, party retained, no data-error flash.
* `tests/test_cash_flow_master_data.py` — 5 new regression tests
  (void-does-not-make-a-category-deletable; the full lifecycle through the purge
  action incl. audit/ledger/balance assertions; the GRN-linked party; the
  sub-category purge; the panel offering the action). **61 passed.**
* Full suite: **91 passed, 5 skipped.**
