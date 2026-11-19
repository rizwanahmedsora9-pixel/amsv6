# Changes applied (and the two findings retracted)

Status: **applied to the workspace source tree.** Nothing in `APPLICATION_AUDIT/` and no
database was modified. Each item below names the file, the change, and the test that proves
it. Where a change was made I also record what *not* to change, because two findings from
the audit phase turned out to rest on an invalid comparison and have been withdrawn.

Ordered as they were applied: the delete path first (the owner's binding requirement),
then the destructive button, then the read/perf work.

---

## 1. `app/services/void_rebuild.py` — `hard_delete_payment`

**Section: Deleting a payment / transaction delete → Payment**

**Defect.** Deleting a payment reversed its cash and ledger entries but left every bill
that payment had settled closed at zero. The customer's debt vanished with the payment.

Measured before the fix (credit sale 5,000 → pay 5,000 → delete the payment):

| | expected | actual |
|---|---|---|
| open receivable | 5,000.00 | **0.00** |
| pending bill | `(1, 'MB NO.SALE-B', 5000.0, is_paid=0)` | `(1, 'MB NO.SALE-B', 0.0, is_paid=1)` |

**Change.** Added `_reopen_bills_settled_by_payment()` and `_reopen_one_pending_bill()`
below `hard_delete_payment` (`void_rebuild.py:415`), and a call to the former inside
`hard_delete_payment` immediately after the accounting reversal. The reopened amount is
**not** the payment's own value — it is recomputed from the source document
(`amount − discount − paid-at-source`) by re-running the same
`_sync_direct_sale_pending_bill` / `_sync_booking_pending_bill` builders the sale and
booking paths use, so an overpayment or a split payment cannot fabricate a debt.

**Proof.** `DELETE_TESTS/deltest4.py` (Case B) — now `open_receivable = 5000.0`,
`cash = 0.0`, bill `(1, 'MB NO.SALE-B', 5000.0, is_paid=0)` → **CORRECT**.
Regression: `DELETE_TESTS/delete_suite.py` 6/6 clean, `delete_seq.py` sequence 1 ends
byte-identical to the pre-sale state.

**Not changed, deliberately.** `_apply_settlement_to_pending_bills_for_client` already
clamps with `pb.amount = max(0.0, amount - settle)`, so the replay itself never writes a
negative bill. The negative intermediate I saw during a probe was my own probe setting
`amount` directly, not the application.

---

## 2. `app/blueprints/misc/extra.py` — the Auto-Fix route (was lines 41–52)

**Section: System Report → Auto-Fix Issues**

**Defect (AUDIT-039).** The route ran `_rebuild_direct_sale_pending_bills()`, which ignores
payments and recreates receivables from `sale.amount − discount − paid_amount`. One click
re-billed money already collected, and each click appended another copy of the table.
A 1,000 sale, fully paid, became "1,000 owed and 1,000 received".

**Change.** The route is kept but is now **read-only**. It reports (a) direct sales with no
pending-bill row and (b) materials whose `total` disagrees with the sum of their entry
movements, using the same aggregation `_rebuild_material_totals` would write — but only
comparing, never writing. The route no longer imports or calls either rebuild.
`templates/system_report.html:9` — the "Auto-Fix Issues" link/button is removed.

**Proof.** `DELETE_TESTS/autofix_check.py`: sale 1,000 fully paid, click the route 4× →
`pending_bill` count, open amount, paid count, cash and payment count all unchanged.
On the full 4,000-row database: `GET /fix_system_issues` → HTTP 200, `pending_bill`
4000 rows / 25,500,242.00 and `entry` 4138 rows / `material.total` 519,034.00 identical
before and after, flash reads *"Diagnosis complete — no data was changed, and no drift was
found."*

This route was previously excluded from the report sweep as a "KNOWN MUTATOR". It no
longer is, and it now returns 200.

---

## 3. `app/services/schema.py` — expression indexes for the client-name predicate

**Section: schema bootstrap → `_PERFORMANCE_INDEXES`**

**Defect (AUDIT-040, cost half).** Every receivables / ledger / settlement query filters on

```sql
lower(trim(coalesce(client_name, ''))) = ?
```

A plain index on `client_name` cannot serve that predicate, so each settlement pass fell
back to a full `SCAN pending_bill` — the measured 608,741-object, 10,417-statement loop per
payment.

**Change.** Four additive expression indexes appended to `_PERFORMANCE_INDEXES`:

```sql
CREATE INDEX IF NOT EXISTS ix_pending_bill_client_name_norm
  ON pending_bill(lower(trim(coalesce(client_name, ''))));
-- and the same shape on direct_sale, booking, payment
```

They are additive, create-if-not-exists, and reversible with `DROP INDEX`. No data change.

**Proof.** On the restored 4,000-row database:

| | query plan | time |
|---|---|---|
| index dropped (old behaviour) | `SCAN pending_bill` | 0.80 ms |
| index present | `SEARCH pending_bill USING INDEX ix_pending_bill_client_name_norm (<expr>=?)` | 0.11 ms |

The scan is gone; the gain grows with table size.

---

## 4. `app/blueprints/reports/_profit_profit_reports.py` — empty-period message

**Section: Reports → Profit & Loss**

**Problem.** A period with no sales or purchases renders 0.00 in every cell, which reads as
a broken report rather than a correct answer. This was previously listed as a defect
("P&L all zeros") and then retracted as *expected*; the retraction stands. Only the
presentation changed.

**Change.** In `profit_reports()` (line 147), when `row_count` and `material_row_count` are
both zero, flash an informational message naming the period and stating that every figure
is correctly zero, inviting a date-range change. No figure is altered.

---

## 5. `app/services/billing.py` — `get_next_bill_no()` lock ordering

**Section: bill-number allocation**

**Observed (AUDIT-040, contention half).** `get_next_bill_no()` takes the write lock with
`INSERT OR IGNORE INTO bill_counter` *before* calling `find_bill_conflict()`, which imports
and can trigger `_auto_repair_orphaned_entries()`. The allocator therefore holds the
database write lock while a repair path may write and roll back beneath it, and
`utils/audit.py`'s second connection only sets `busy_timeout=1500`, so a slow rebuild turns
into `database is locked`.

**Status: not changed.** The safe reordering needs the conflict check hoisted above the
lock and the collision loop re-validated after taking it. That is a behavioural change to
the uniqueness guarantee that the whole manual-bill contract rests on, and it cannot be
proven by the read-only checks used here. Recommendation: leave the ordering, and instead
give `utils/audit.py`'s connection the same `busy_timeout` as the app engine. Recorded, not
applied.

---

## 6. Two findings retracted after measurement

**6a. "Adjustments/settlements do not persist" (was AUDIT-038).** Withdrawn. The rebuild
function is idempotent and correct:

- On a consistent client (Alpha Traders) it reports `payments_replayed: 401` and changes
  0 rows — because it is re-deriving the state that is already stored.
- Deliberately corrupting a 51,896 bill and re-running it **restored it exactly**
  (3,588,067 → 3,639,963).
- The replay run on its own settles the client to 0.00 and commits.
- A live payment POST durably settles: 100,000 payment → open 3,639,963 → 3,539,963.

The original alarm came from comparing *remaining open* (25,500,242) against *all payments
ever received* (49,994,882). Those are different scopes: the payments had already settled
2,478 bills whose `amount` was set to zero on settlement. The correct identity holds to the
cent:

```
sold + booked           81,038,521.00
money received          55,538,279.00   (49,994,882 separate payments + 5,543,397 paid at the counter)
still owed              25,500,242.00
received + owed         81,038,521.00   -> difference 0.00
```

**6b. "Arithmetic correct, only persistence broken."** Withdrawn with the above.

---

## 7. Delete behaviour — what the tests establish

Owner requirement: a deliberate delete must leave the workspace exactly as it was before
the entry existed.

| scenario | result |
|---|---|
| cash sale 1,000, fully paid → delete | **clean** — cash 1,000→0, stock 98→100, all child rows gone, only `audit_log` grows |
| credit sale, nothing paid → delete | **clean** |
| credit sale, half paid at counter → delete | **clean** |
| booking, nothing paid → delete | **clean** |
| booking 5,000 + 2,000 paid at booking → delete | **clean** — cash 2,000→0, open 3,000→0 |
| booking + a *separate* 2,000 payment → delete the booking | the separate payment **remains**, by design — it is real money received with no bill to settle, and the client ledger (`/ledger/<id>`) shows it as a 2,000 credit |
| credit sale 5,000 → pay 5,000 → delete the payment | **fixed** — bill reopens at 5,000, cash back to 0 |
| credit sale 2,000 → pay → delete payment → delete sale | returns **exactly** to the starting state |

The one deliberate non-deletion: money that genuinely changed hands is never destroyed by
deleting the document it was meant for. It stays visible as a client credit. Destroying it
would make the ledger disagree with the cash drawer, which is the opposite of what the
requirement asks for.

---

## Reproducing the delete tests

Scripts live in `APPLICATION_AUDIT/STRESS_TEST/DELETE_TESTS/`. Each builds its own throwaway
database, so none of them touches live data:

```bash
export APP_DB_PATH=/tmp/proof.db AMS_SCHEMA_VERSION=v44 AMS_ENABLE_BACKGROUND_WORKERS=0 \
       ALLOW_EMPTY_DB=1 WTF_CSRF_ENABLED=0 ALLOW_DB_DROP=1
python3 delete_suite.py     # 6 delete kinds -> exact prior state
python3 delete_seq.py       # delete a payment, then the document
python3 deltest4.py         # Case B: the reopened-bill fix
python3 autofix_check.py    # Auto-Fix no longer writes
```

Every assertion reads through a **separate read-only SQLite connection**, never through the
session under test, so a pending object cannot be mistaken for a saved one.
