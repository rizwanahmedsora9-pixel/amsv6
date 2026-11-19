# EVIDENCE INDEX — stress test and data-integrity audit

Everything in `23_STRESS_TEST_AND_DATA_INTEGRITY_REPORT.md` is reproducible from the
files in this folder. Each script is standalone; run them with any Python that has
Flask and SQLAlchemy installed.

## Machine-readable results

| File | Contents |
|---|---|
| `seed_b1.json` | batch 1 outcome — 1,000 per section, zero errors |
| `seed_b2.json` | batch 2 (after reboot) — 1,000 per section, zero errors |
| `seed_b9.json` | small 200-per-section run used to reproduce the receivable defect |
| `verify_batch1.json` | 42 independent SQL checks after batch 1 |
| `verify_batch2.json` | 42 independent SQL checks after batch 2 |
| `snap_b1.json` | independent SQL totals after batch 1 |
| `snap_b2.json` | independent SQL totals after batch 2 |
| `reports_batch1.json` | every report route: status, timing, size, headline figures |
| `final_sweep.json` | 35-check discrepancy / orphan / arithmetic sweep |

## The two headline proofs

### `proof_autofix.py` — the "Auto-Fix Issues" button destroys settled bills

Builds a database from scratch, records a credit sale of 1,000, records a payment of
1,000, then requests `GET /fix_system_issues` three times, printing the receivable
balance after each.

Expected output: receivable goes `1,000 → 0 → 1,000 → 1,000 → 1,000`, and the
`pending_bill` row count goes `1 → 1 → 2 → 3 → 4`.

### ⚠️ `proof_volume.py [N]` — WITHDRAWN as a proof

This script's conclusion does not stand: it compared *remaining open* against *all payments
ever received*, which measure different things. With payments large enough to settle bills,
"open" correctly falls. See report section 7a. The arithmetic identity
`received + owed = sold + booked` balances to **0.00**.

The script is kept because it is a useful volume generator; it is not evidence of a defect.

### `proof_volume.py [N]` — original (withdrawn) claim

Records `N` credit sales of 1,000 and then `N` payments of 1,000 against one client,
printing the open receivable after each stage. With `N=5` the result is correct
(0.00 open); the defect appears as volume grows.

`proof_receivables.py` is the same experiment in the smallest possible form.

## Instruments

| File | Purpose |
|---|---|
| `harness.py` | `wipe`, `seed --batch N --n 1000`, `report`, `smoke`. Detects success only from real `alert-success` elements — never from page text, because the dashboard's JavaScript contains the word "successfully" and will produce false passes. |
| `verify.py <tag>` | 42 independent SQL assertions: totals, orphans, arithmetic, stock conservation, account reconciliation, entry numbering, `foreign_key_check`, `integrity_check`. |
| `reports.py <tag>` | Requests every report route as an administrator, records status / milliseconds / bytes / error-page detection / headline figures. Decodes only non-binary bodies — an xlsx begins `PK\x03\x04` and cannot be read as UTF-8. |
| `final_sweep.py` | The full discrepancy / orphan / wrong-calculation sweep across all sections. |
| `crosscheck.py <snap.json>` | Independently recomputes the business totals straight from SQL, bypassing the application entirely. |
| `mech.py` | Instruments the payment save to show `23` settlement calls mutating `49` rows of which `1` survives the commit. |

## Findings, as separate documents

| File | Finding |
|---|---|
| `FINDING_receivables.md` | **WITHDRAWN** — the comparison was invalid; the settlement path is correct (report §7a) |
| `DELETE_TESTS/` | **the delete-cleanliness suite** — 6 delete kinds, payment-then-document sequences, the reopened-bill fix, and the Auto-Fix read-only proof. Each script builds its own throwaway database. |
| `FINDING_autofix.md` | CRITICAL — "Auto-Fix Issues" recreates paid debt |
| `FINDING_perf.md` | HIGH — 10,417 SQL statements and 12.4 s per payment save |

## Notes for anyone re-running this

- The harness writes to a fixed database path. Set `LAB_DB` to keep runs separate —
  otherwise a small reproduction will overwrite a large dataset (this happened during
  the audit and cost a full re-seed).
- Never run two application instances against the same SQLite file. It produces
  `database is locked` and multi-second stalls that look like application defects.
- `create_app()` takes about 40 seconds once the database is large, because it
  back-fills `pending_bill` for every sale at boot.


---

## Delete-path evidence (added after the fixes)

`DELETE_TESTS/` holds the scripts that test the owner's requirement that a deliberate
delete leaves the workspace exactly as it was.

| Script | Proves |
|---|---|
| `delete_suite.py` | six delete kinds — cash sale, unpaid credit sale, part-paid credit sale, unpaid booking, part-paid booking, standalone payment — each returns to the **exact** prior state |
| `delete_seq.py` | credit sale → pay → delete the payment → delete the sale returns to the pre-sale state; a booking's *separate* payment correctly survives |
| `deltest.py` | cash sale fully paid → delete: cash 1,000→0, stock 98→100, only `audit_log` grows |
| `deltest2.py` | booking + paid-at-booking → delete: cash and open both return to 0 |
| `deltest3.py` | the two-payment scenario in full |
| `deltest4.py` | **Case B** — credit sale 5,000, pay it, delete the payment: bill reopens at 5,000 |
| `autofix_check.py` | four clicks on Auto-Fix create **zero** rows and leave the balance untouched |

Run them with `AMS_APP_ROOT` pointing at the app (defaults to `/home/user`); set
`PROOF_DB` to choose the throwaway database path. Every assertion reads through a
**separate read-only SQLite connection**, never through the session under test.

### Regression evidence after the fixes

On the restored 2,000-per-section database, re-run through the same instruments:
`verify_full.py` **42/42 OK**, `crosscheck_full.py` reproduces every headline figure to the
rupee, `reports_full.py` **37/38** render (the formerly-destructive route now returns 200
and changes nothing).
