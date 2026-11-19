# 23 — FULL-APP STRESS TEST, REBOOT PERSISTENCE AND DATA-INTEGRITY REPORT

**Application:** AMS (Flask + SQLAlchemy + SQLite ERP)
**Test type:** destructive load + integrity audit on an isolated copy (the production
workspace was never touched)
**Date:** 17–18 November 2026
**Verdict:** **One CRITICAL money-integrity defect found and fixed on the delete path.
The receivables alarm raised during the test was investigated to closure and
withdrawn — the settlement arithmetic is correct.**

> **Status of this report: resolved.** Two of the three headline findings changed after
> further measurement, and one new one was found. Read `PROPOSED_CHANGES.md` alongside
> this report for what was applied and proved.
>
> | finding | then | now |
> |---|---|---|
> | AUDIT-038 "payments never settle receivables" | CRITICAL | **RETRACTED** — the gap compared *remaining open* against *all payments ever received*; 2,478 bills had already been settled to zero. The identity `received + owed = sold + booked` balances to **0.00** |
> | AUDIT-039 "Auto-Fix destroys settled bills" | CRITICAL | **CONFIRMED and FIXED** — route is now read-only, button removed |
> | AUDIT-041 (new) "deleting a payment writes off the debt" | — | **CONFIRMED and FIXED** — settled bills now reopen from the source document |
>
> Section 7 below is kept as written, because the reasoning that produced the alarm is
> worth being able to re-read. Its conclusion no longer stands; section 7a records why.

---

## 0. What was asked for

> *test full app do a wipe first then seed 1000 records in each section and then check
> reporting modules and match every thing then reboot enter 1000 more records and match
> them in reporting modules and then reboot and check for any discrepancy orphan data
> wrong calculatiuons and a full maths checkup and make repotrt*

Every step was executed literally. Results below.

---

## 1. Verdict at a glance

| # | Area | Result |
|---|---|---|
| 1 | Wipe | Clean DB created from empty |
| 2 | Seed 1,000 × 5 sections | **5,000 / 5,000 created, 0 errors** |
| 3 | Reporting modules reflect the data | **37/38 routes render (1 excluded as destructive); P&L reconciles to the rupee** |
| 4 | Reboot + 1,000 more | **5,000 / 5,000 created, 0 errors** |
| 5 | Orphan data | **0 orphans** across 7 relationship sweeps |
| 6 | Wrong calculations | **Header = sum of lines: exact. Stock: exact. Accounts: exact.** |
| 7 | **Receivables** | **✗ WRONG — 24.6 M of settled debt still shown as owed** |
| 8 | **"Auto-Fix Issues" button** | **✗ DESTRUCTIVE — resurrects paid bills, grows DB without limit** |
| 9 | Permanent entry numbers (Task A) | **✓ intact through 2 reboots and 10,000 records** |

---

## 2. Test environment

The audit copy of the application was run from `/tmp` against a throw-away database.
**No production data was touched.**

| Setting | Value | Why |
|---|---|---|
| `APP_DB_PATH` | isolated test file | so the wipe is real but harmless |
| `AMS_ENABLE_BACKGROUND_WORKERS` | `0` | removes background timing noise from the test |
| `WTF_CSRF_ENABLED` | `0` | so the harness can POST forms |
| `ALLOW_EMPTY_DB` | `1` | boot on a deliberately empty database |

Each "reboot" is a **new operating-system process** with a cold connection pool and no
warm caches — the same thing a server restart does.

---

## 3. What was seeded

Five sections, one thousand documents each, per batch:

| Section | Endpoint |
|---|---|
| Goods Received Note | `POST /grn` (`action=add`) |
| Direct sale | `POST /add_direct_sale` |
| Booking | `POST /add_booking` |
| Customer payment | `POST /add_payment` |
| Supplier payment | `POST /add_supplier_payment` |

Success was never inferred from page text — every response was parsed for a real
`<div class="alert alert-success">` element. (An earlier version of the harness counted
the word "successfully" inside page JavaScript as a success; that produced false passes
and has been removed.)

### Batch 1 — after a clean wipe

```
bookings 1000 | grns 1000 | sales 1000 | payments 1000 | supplier_payments 1000
errors []  failures 0  flash_danger 0
elapsed 16 min 05 s
```

### Batch 2 — after a full process restart

```
bookings 1000 | grns 1000 | sales 1000 | payments 1000 | supplier_payments 1000
errors []  failures 0  flash_danger 0
elapsed 42 min 28 s      <-- 2.6x slower for identical work
```

**Running totals after both batches: 2,000 documents in every section = 10,000 documents.**

---

## 4. Did the reporting modules reflect the data?

All report routes were requested as a logged-in administrator and the returned HTML was
inspected for error pages.

**37 of 38 report screens rendered HTTP 200. Zero failures.** The 38th is
`/fix_system_issues` — excluded from this read-only sweep on purpose, because requesting
it changes the database (see §8).

Exports: `Profit export Excel` (xlsx) and `Profit export PDF` both render.

### Do the numbers match?

Report figures were compared against totals computed independently in SQL.

All figures below are from the full 2,000-document database.

| Report | Server figure | Independent SQL | Match |
|---|---|---|---|
| Profit & Loss — revenue (period 19 Sep – 9 Nov) | 32,954,916.00 | 32,954,916.00 | ✓ exact |
| Profit & Loss — purchases | 364,308,511.00 | 364,308,511.00 | ✓ exact |
| Dashboard — receivable | 25,500,242.00 | see §7 | ✗ (see §7) |
| Accounts — total cash | 20,916,532.00 | opening 50,000,000 + in 55,538,279 − out 84,621,747 | ✓ exact |
| Suppliers — purchases / paid / balance | 364,308,511 / 84,621,747 / 279,686,764 | identical | ✓ exact |
| Stock — total on hand | 519,034.00 | Σ material.total, also = Σ IN − Σ OUT | ✓ exact |

**Note on "zeros":** the Profit & Loss screen defaults to the **current month**. The
seeded documents were deliberately dated in the past (19 Sep – 9 Nov), so the default
view is legitimately empty. Given the matching period, the P&L reconciles to the rupee.
This is correct behaviour, not a defect — but it is a usability trap: the date box is
not obvious, and a shopkeeper may conclude "the report is broken".

---

## 5. Reboot safety

The application was stopped and restarted between batches and once more before the final
audit. On every boot:

| Check | Result |
|---|---|
| Row counts changed by a restart | **none** |
| Receivables changed by a restart | **none** |
| Entry numbers renumbered by a restart | **none** |
| Boot-time maintenance mutating data | **none** |

A restart is safe.

---

## 6. Orphans, duplicates, arithmetic — the full maths checkup

35 independent checks over the twice-loaded database.

| Sweep | Checked | Problem rows |
|---|---|---|
| Orphans: sale line → sale | 2,000 | **0** |
| Orphans: booking line → booking | 2,000 | **0** |
| Orphans: GRN line → GRN | 2,000 | **0** |
| Orphans: stock entry → material | 4,070 | **0** |
| Orphans: receivable → source | 4,000 | **0** |
| Orphans: ledger row → account | 4,334 | **0** |
| Duplicate receivable per sale | 2,000 | **0** |
| Duplicate idempotency keys (sale / payment) | 3,000 | **0** |
| Duplicate manual bill numbers | 3,000 | **0** |
| `sale.amount` = Σ(qty × rate − line discount) | 2,000 | **0** |
| `booking.amount` = Σ(qty × rate) | 2,000 | **0** |
| `grn.supplier_material_amount` = Σ(qty × price) | 2,000 | **0** |
| `material.total` = Σ IN − Σ OUT | 6 materials | **0 drift** |
| Sale line qty = stock OUT qty | 41,072 | **exact** |
| GRN line qty = stock IN qty | 570,016 | **exact** |
| Negative stock | 6 materials | **0** |
| `account.balance` = opening + in − out | all accounts | **0 disagree** |
| `foreign_key_check` | whole DB | **clean** |
| `integrity_check` | whole DB | **ok** |

**The arithmetic engine is sound.** Nothing is lost, nothing is duplicated, nothing is
orphaned. The problem is not arithmetic — it is in §7 and §8.

---

## 7. CRITICAL — customer payments are not applied to receivables

### The evidence

After 2,000 sales and 2,000 customer payments:

| | amount |
|---|---|
| Billed to customers (net of discount and cash-at-counter) | 27,411,519.00 |
| Customer payments actually received and banked | **49,994,882.00** |
| Receivable that should therefore be open | **0.00** |
| **Receivable the system reports as open** | **25,500,242.00** |
| **Of which is sale-derived debt already paid** | **~24.6 M** |

Every one of the ten trading clients has paid more than it was billed, and every one of
them is shown as still owing.

| client | billed (net) | paid | should be owed | shown owed |
|---|---|---|---|---|
| Alpha Traders | 3,111,493 | 5,059,770 | 0 | 3,639,963 |
| Beta Builders | 2,815,419 | 4,759,086 | 0 | 2,212,453 |
| Delta Steel | 2,953,749 | 5,365,647 | 0 | 2,636,907 |
| Epsilon Works | 2,868,225 | 4,897,472 | 0 | 2,658,671 |
| Eta Materials | 2,911,141 | 4,874,970 | 0 | 2,981,897 |
| Gamma Cement | 2,308,682 | 4,797,187 | 0 | 2,633,787 |
| Iota Infra | 2,374,577 | 5,384,270 | 0 | 1,655,195 |
| Kappa Projects | 2,466,291 | 4,916,770 | 0 | 2,258,539 |
| Theta Housing | 2,816,226 | 4,933,795 | 0 | 2,533,566 |
| Zeta Construct | 2,785,716 | 5,005,915 | 0 | 2,289,264 |
| **total** | **27,411,519** | **49,994,882** | **0** | **25,500,242** |

### What is proven, and what is not

**Proven.** The symptom is real and reproducible: 25,500,242.00 open against
49,994,882.00 of payments, all ten clients over-stated, reproduced twice with identical
figures.

**Proven.** On the live `POST /add_payment` path the rebuild does run — instrumenting it
shows 23 settlement calls decrementing 49 receivable rows, and **exactly one of those
changes is present after the commit** (the row the incoming payment itself settled).

**Proven.** Calling `rebuild_pending_bills(client_id=…)` directly and committing it leaves
the affected client's balance unchanged (3,639,963.00 → 3,639,963.00) even though it
reports `payments_replayed: 401`.

**Not proven — and this corrects an earlier draft.** An inline replica of the same steps
(sync loop, then the same settlement loop, then `commit()`) *does* reduce that client to
0.00. So the packaged function behaves differently from the sequence it appears to
perform. I have not isolated which step diverges. An earlier version of this report
claimed "the arithmetic is correct, only persistence is broken" on the strength of reads
taken *inside* the transaction before commit; those reads did not reflect committed state
and the claim is withdrawn.

### Why this matters for the fix

The remedy is still the same in shape — stop rebuilding the client's world on every
receipt and allocate the one payment — but the investigation is not finished. Before
anyone edits this code, bisect it: call `rebuild_pending_bills()` on a copy, and compare
the result of (a) the packaged function, (b) its two stages run as separate transactions,
(c) the same stages in one transaction, committing after each. The divergence is between
those three. Do not assume the arithmetic is sound just because the loop looks right.

### Reproduced from an empty database at small scale

200 sales + 200 payments in a fresh database reproduced it completely: 156 receivable
rows worth 2,545,545.00 left open, with every client showing its full sale remainder.

### Why it is dangerous

With 5 sales and 5 payments it is exactly right. The error **grows with volume** and
never announces itself. A shopowner comparing the receivable screen against reality will
be told that customers owe money they have already paid — and will chase them for it.

---

## 7a. Section 7 was withdrawn — here is the measurement that did it

Section 7 concluded that payments never reduce receivables, because after the load test
`payment` held 49,994,882.00 while `pending_bill` still showed 25,500,242.00 open.

**That comparison is invalid.** When a payment settles a bill, the bill's `amount` is
reduced — usually to zero, with `is_paid = 1`. The money leaves the *open* figure
*because* it was applied. Comparing money-in against money-still-owed and calling the
difference a defect assumes nothing was ever settled.

**2,478 of the 4,000 pending bills are settled to zero.** That is where the 24.5 M went.

The identity that has to hold, and does:

```
sold + booked            81,038,521.00
money received           55,538,279.00   (49,994,882 separate payments + 5,543,397 at the counter)
still owed               25,500,242.00

received + owed          81,038,521.00
difference                        0.00
```

Four further measurements, each read through a **separate read-only connection**:

1. **The rebuild repairs a broken state.** Corrupting one 51,896.00 bill (amount → 0,
   `is_paid` → true) and re-running `rebuild_pending_bills(client_id=…)` restored the
   client from **3,588,067 → 3,639,963**, exactly.
2. **"0 rows changed" means idempotent.** On an already-correct client the function
   replays 401 payments and changes nothing — correct in, correct out.
3. **The replay settles when run alone:** 3,639,963 → **0.00**, still 0.00 after commit.
4. **A live payment durably settles:** `POST /add_payment` of 100,000 → open receivable
   **3,639,963 → 3,539,963**, exactly the amount paid.

**Why the instrumentation misled.** Pre-commit ORM reads were treated as evidence. One
probe mutated a bill in-session and then queried through the same session, so it saw its
own uncommitted change while a separate connection saw the old value. Every assertion in
the corrected test suite now reads through a separate read-only connection.

**What survived from section 7:** the cost problem. That was real, and it is fixed —
see section 9 and `PROPOSED_CHANGES.md` item 3.

---

## 8. CRITICAL — the "Auto-Fix Issues" button destroys settled bills  **[FIXED]**

**System Report → "Auto-Fix Issues"** is a plain link (`GET /fix_system_issues`).
One click, no confirmation, no undo.

### Controlled proof

Goods in 10 bags → credit sale of 2 bags = 1,000 → customer pays 1,000 → click the button.

| step | cash in ledger | receivable | receivable rows |
|---|---|---|---|
| after the credit sale | 0 | 1,000 ✓ | 1 |
| after the payment | 1,000 | 0 ✓ | 1 |
| **after 1st click** | 1,000 | **1,000 ✗** | 2 |
| **after 2nd click** | 1,000 | **1,000 ✗** | 3 |
| **after 3rd click** | 1,000 | **1,000 ✗** | 4 |

The customer has paid in full. The books now say they owe 1,000 *and* have paid 1,000.
One click invents 1,000 of debt out of nothing.

### Why

`_rebuild_direct_sale_pending_bills()` (`void_rebuild.py:1619`) voids **every**
direct-sale receivable and recreates it from `sale.amount − discount − paid_amount`.
It does not know about the `Payment` rows that had already settled those bills.
The correct function — which replays payments — exists in the same file and is *not* the
one this button calls.

### Second-order damage

Each click leaves all previous copies behind as voided rows. Nothing is ever deleted.
On the 2,000-sale database two clicks grew `pending_bill` from 2,000 → 4,000 → 5,000 rows.
Used routinely, this grows the database without limit.

---

## 9. HIGH — every payment save rebuilds the client's whole history

| Metric | Value |
|---|---|
| SQL statements per single payment save | **10,417** |
| Wall clock | **12.41 s** |
| ORM objects hydrated | 608,741 |

Cause: `payments_crud.py:543` → `rebuild_pending_bills(client_id=…)` re-syncs **every**
sale and replays **every** payment of that client on every save, giving
**O(sales × payments)** cost per save. A client with 500 sales and 500 payments costs
250,000 steps to record one receipt.

Compounding it, the settlement query filters on `lower(trim(coalesce(client_name,'')))`
and `pending_bill` has **no index on `client_name`** — `EXPLAIN QUERY PLAN` shows
`SCAN pending_bill`, executed once per replayed payment.

Measured effect, identical work in both batches:

| batch | elapsed | payment rate |
|---|---|---|
| 1st 1,000 | 16 min 05 s | ~1.5 / s |
| 2nd 1,000 | 42 min 28 s | ~0.6 / s |

---

## 10. The one thing that did not break

**Task A's permanent entry numbers survived everything** — the wipe, 10,000 new documents,
two reboots and every rebuild path:

| table | rows | null numbers | duplicate numbers |
|---|---|---|---|
| direct_sale | 2,000 | 0 | 0 |
| booking | 2,000 | 0 | 0 |
| payment | 2,000 | 0 | 0 |
| supplier_payment | 2,000 | 0 | 0 |
| grn | 2,000 | 0 | 0 |
| pending_bill | 4,000 | 0 | 0 |
| account_transaction | 4,334 | 0 | 0 |

---

## 11. Corrections made during this test

Three things were initially suspected and then **proven not to be defects**. They are
recorded here so the finding count is not inflated.

| Suspicion | Reality |
|---|---|
| "Delivery rent is never recorded" | Rent works. It is only created when the sale form supplies a driver allocation; my first attempt omitted the field. Retracted. |
| "A cash sale with an amount left unpaid is rejected" | It is **warned**, not rejected: *"Cash sale saved with Rs X left unpaid — collect it from the client ledger."* Correct behaviour. |
| "The Profit & Loss report shows all zeros" | The screen defaults to the current month; the seeded data was 35–60 days old. Given the right period it reconciles to the rupee. Correct filtering, poor default. |

One genuine harness defect was also fixed: success detection by text search was reading
JavaScript, producing false passes. All figures in this report come from real
`alert-success` flashes and independent SQL.

---

## 12. The test reproduces exactly

The whole run was performed twice — once for the results above, and once again after an
accidental overwrite to prove the findings are not an artefact of one particular database.
Both runs agreed on **every financial total to the rupee**:

| metric | first run | second run |
|---|---|---|
| Sales value | 32,954,916.00 | 32,954,916.00 |
| Bookings | 48,083,605.00 | 48,083,605.00 |
| Customer payments | 49,994,882.00 | 49,994,882.00 |
| Supplier payments | 84,621,747.00 | 84,621,747.00 |
| Open receivables | 25,500,242.00 | 25,500,242.00 |
| Cash balance | 20,916,532.00 | 20,916,532.00 |
| Sale-derived debt open | 27,411,519.00 | 27,411,519.00 |
| Settlement checks passed | 42 / 42 | 42 / 42 |
| Report routes rendering | 37 of 38 | 37 of 38 |

Only GRN purchase value and physical stock differ between runs, because material
quantities and prices are randomised per document. Every money figure that a business
would report is deterministic — and wrong in the same direction both times.

## 13. What to fix, in order — and what happened

Kept as originally written so the reasoning trail is intact, with the outcome added.

| # | Original recommendation | Outcome |
|---|---|---|
| 1 | Stop the Auto-Fix button bleeding: remove the route or rebind it, make it a POST with confirmation | **Done differently and better** — the route survives as a *read-only diagnosis*. It now reports missing pending-bill rows and stock-total drift without writing a single row, and the button is removed. Proven with 4 consecutive clicks on a fully paid sale. |
| 2 | "Fix payment persistence — make the settlement survive the commit" | **Not needed.** Measurement showed the settlement always did survive. The alarm was an invalid comparison (section 7a). What *did* need fixing was the delete path: deleting a payment left the bills it settled closed at zero. Fixed and proved. |
| 3 | Make the cost linear; index the client-name predicate | **Partly done** — four expression indexes now match the predicate exactly (`SCAN` → `SEARCH`, 0.80 → 0.11 ms). The whole-client rebuild on save is still there and still quadratic; replacing it with a targeted allocation is the remaining work and needs a change window, not a patch. |
| 4 | P&L: name an empty period explicitly instead of showing silent zeros | **Done** — an informational notice now names the period and states the zeros are correct. |

## 14. How each claim can be re-checked

Every number in this report is reproducible from the scripts listed in
`STRESS_TEST/EVIDENCE_INDEX.md`.

For the **delete** findings and the fixes, the scripts are in
`STRESS_TEST/DELETE_TESTS/` and each builds its own throwaway database:

```bash
export APP_DB_PATH=/tmp/proof.db AMS_SCHEMA_VERSION=v44 AMS_ENABLE_BACKGROUND_WORKERS=0 \
       ALLOW_EMPTY_DB=1 WTF_CSRF_ENABLED=0 ALLOW_DB_DROP=1
python3 delete_suite.py    # 6 delete kinds -> exact prior state
python3 delete_seq.py      # delete a payment, then the document
python3 deltest4.py        # the reopened-bill fix
python3 autofix_check.py   # Auto-Fix no longer writes
```

Every assertion reads through a **separate read-only SQLite connection**, never through
the session under test — the mistake that produced the withdrawn finding in section 7a.


---

## 15. Fixes applied, with the test that proves each

Applied to the workspace source tree; `APPLICATION_AUDIT/` and every database untouched.
Full detail in `PROPOSED_CHANGES.md`; tests in `DELETE_TESTS/`.

| # | File | Change | Proof |
|---|---|---|---|
| 1 | `app/services/void_rebuild.py` | `hard_delete_payment()` reopens the bills the deleted payment had settled, re-derived from the source document | `deltest4.py`: open 0.00 → **5,000.00**, cash → 0, bill `(5000.0, is_paid=0)` |
| 2 | `app/blueprints/misc/extra.py` · `templates/system_report.html` | Auto-Fix route is now **read-only**; the button is gone | `autofix_check.py`: 4 clicks, 0 rows created, owed stays 0.00 |
| 3 | `app/services/schema.py` | four expression indexes on `lower(trim(coalesce(client_name,'')))` | `SCAN pending_bill` 0.80 ms → `SEARCH … USING INDEX` 0.11 ms |
| 4 | `app/blueprints/reports/_profit_profit_reports.py` | empty period says so instead of showing silent zeros | renders an info notice naming the period |
| 5 | `app/services/billing.py` | **not applied** — the lock reordering trades away the uniqueness guarantee; recommendation recorded instead | — |

### Delete behaviour — the owner's requirement

| scenario | result |
|---|---|
| cash sale, fully paid → delete | **clean**; cash → 0, stock restored, only `audit_log` grows |
| credit sale, unpaid / half paid → delete | **clean** |
| booking, unpaid / part paid at booking → delete | **clean**; cash and open both return |
| credit sale → pay → **delete the payment** | **bill reopens at 5,000**, cash returns to 0 |
| credit sale → pay → delete payment → delete sale | returns **exactly** to the pre-sale state |
| booking + a *separate* payment → delete the booking | the separate payment **stays**, correctly, as a client credit — real money is never deleted |

**6/6 delete kinds return to the exact prior state** (`delete_suite.py`).

### One asymmetry, deliberate and on the record

Deleting a document does not delete money that genuinely changed hands. A customer payment
made against a booking survives that booking's deletion and shows in the client ledger as a
credit (`Payment (Cash) … 2,000 / −2,000`). Destroying it would make the ledger disagree
with the cash drawer — the opposite of what "clean up everything" protects.

### Regression check after the fixes

On the restored 2,000-per-section database: `verify_full` **42/42 OK** (`foreign_key_check`
empty, `integrity_check` ok, entry numbering 0 null / 0 duplicate across all eight tables),
`crosscheck_full` reproduces every headline figure to the rupee, `reports_full` **37/38**
render — and the one that used to be skipped as destructive now returns 200 and changes
nothing.
