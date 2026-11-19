# Prompt for the next testing round

Copy everything between the lines into a new session. It encodes what took three hours
to learn, so the next run starts from the answers instead of rediscovering them.

---

## ROLE

You are testing a Flask + SQLAlchemy + SQLite ERP ("AMS") that is already live for a real
business. Its prime requirement is **no data loss, no drop, no orphan**. A deliberate
delete is not data loss — only spontaneous disappearance is. Intention is not to be
assumed: test real sequences, not the happy path.

## FIRST ACTIONS (do these before anything else)

1. Read `APPLICATION_AUDIT/STRESS_TEST/PROPOSED_CHANGES.md` — it lists the five fixes
   already applied and the tests that prove them. Do not re-investigate these.
2. Read `APPLICATION_AUDIT/STRESS_TEST/run_all_tests.py` — a 58-second suite covering
   every defect found so far. Run it first:
   ```bash
   python3 APPLICATION_AUDIT/STRESS_TEST/run_all_tests.py --app /home/user
   ```
   If it is green, the baseline is intact and you can spend your time on *new* ground.
3. Read `APPLICATION_AUDIT/19_FINDINGS_REGISTER/00_FINDINGS_INDEX.md` — 39 active
   findings, 1 retracted. Never report a retracted one as a defect.

## THE FIVE RULES THAT COST THE MOST TIME LAST ROUND

These are not style preferences. Each one caused either a wasted hour or a wrong finding.

**1. Every assertion reads through a SEPARATE READ-ONLY connection.**
```python
con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
```
Reading through the ORM session under test reports uncommitted state as though it were
saved. This mistake produced a fabricated CRITICAL finding that had to be publicly
withdrawn.

**2. Never compare "money still owed" with "all money ever received".**
They measure different things. When a payment settles a bill, the bill's `amount` is
reduced — the money leaves the "owed" figure *because* it was applied. The only valid
identity is:

```
sold + booked == money received + still owed
```
computed from the **source documents** (`direct_sale`, `booking`, `payment`), never from
`pending_bill` — because `pending_bill` is already net of settlement, so summing it only
reproduces "owed" and tests nothing.

**3. Never detect success from page text.**
The dashboard JavaScript contains the word "successfully". Parse real alert elements:
```python
re.findall(r'<div class="alert alert-(\w+)[^"]*"[^>]*>(.*?)</div>', body, re.S)
```
Never accept a `302` as success — a handler that swallows a lock error still redirects.

**4. Use past dates in every test.**
The app rejects future-dated transactions ("The transaction date cannot be in the
future"). A test that seeds `date = today + 10` silently creates 70 % of what it intended
and looks like a data-loss bug. Anchor to a fixed past month.

**5. One database per test, and never two app instances on one SQLite file.**
Two processes on one file produce `database is locked`, which reads exactly like an
application defect. `create_app()` takes ~40 s on a large database.

## ENVIRONMENT

Never call `create_app()` inside the live workspace. Copy the app to `/tmp` first:
```bash
cp -r /home/user /tmp/ams && cd /tmp/ams
export APP_DB_PATH=/tmp/proof.db AMS_SCHEMA_VERSION=v44 \
       AMS_ENABLE_BACKGROUND_WORKERS=0 ALLOW_EMPTY_DB=1 \
       WTF_CSRF_ENABLED=0 ALLOW_DB_DROP=1
```
Remove stale `-wal` / `-shm` / `-journal` files before every run. Login:
`/login` with `{username: 'Admin', password: 'Admin@fbm12345'}`.

Do **not** wipe or modify `APPLICATION_AUDIT/` or the source tree. `/tmp` does not
survive between sessions — copy anything you need to keep into the workspace.

## WHAT IS ALREADY PROVEN — DO NOT RE-TEST

| Area | Status |
|---|---|
| Orphans | **0** across 7 relationship sweeps |
| Header == sum of lines | exact, every document type |
| Stock conservation | 0 drift, at rest and after every delete |
| `account.balance` = opening + in − out | exact |
| `PRAGMA foreign_key_check` / `integrity_check` | clean |
| Entry numbering (Task A) | 0 null, 0 duplicate, survives reboots and edits |
| Payment settlement arithmetic | **correct** — identity balances to 0.00 on 81 M of volume |
| Delete cleanliness | **6/6 kinds** return to the exact prior state |
| Auto-Fix route | **read-only**, four clicks change nothing |
| P&L reconciliation | exact to the rupee over the seeded period |
| Supplier ledger | exact |

**Retracted, never report again:** "delivery rent never recorded"; "cash sale with
unpaid amount rejected" (rejected by design); "P&L all zeros" (correct for an empty
period); "arithmetic correct but persistence broken"; "payments never settle receivables".

## REAL DEFECTS STILL OPEN

1. **`get_next_bill_no()` lock ordering** — takes the write lock *before*
   `find_bill_conflict()`, which can trigger a repair that writes and rolls back beneath
   it. `utils/audit.py`'s second connection only sets `busy_timeout=1500`, so a slow
   rebuild becomes `database is locked`. Not fixed because reordering trades away the
   uniqueness guarantee. **Highest-value thing to investigate properly.**
2. **Quadratic rebuild on payment save** — every receipt rebuilds the client's whole
   history (10,417 statements / 12.4 s on a large client). Indexes made it cheaper;
   the design is still quadratic. The fix is a targeted allocation of one payment.
3. **97 float money columns** — floats are used for currency across the schema.
   `sync_money_fields` mirrors the important ones to integer minor units, but not all.
4. **`journal_mode=DELETE`** on what is described as a network-mounted production path.

## HOW TO TEST, IN THIS ORDER

Cheap and deterministic first. Only escalate to volume when a cheap test passes.

1. **Static** — read the code path. No database needed. Confirm the fix is present.
2. **Single-transaction** — one sale, one payment, one delete. Seconds.
3. **Sequence** — create → pay → delete → delete. This is where the real defects live,
   because each step can leave residue the next step hides.
4. **Volume** — 100 of each. Catches settlement-at-scale problems.
5. **Restore/reboot** — only when the question is about persistence.

Budget: steps 1–4 should take **under two minutes**. If a question needs more than that,
it is an investigation, not a test — say so and use the heavy instruments in
`APPLICATION_AUDIT/STRESS_TEST/EVIDENCE/` (`verify_full.py`, `crosscheck_full.py`,
`reports_full.py`, `final_sweep_full.py`), which expect a 2,000-per-section database.

## OUTPUT

- Every claim cites `file:line`.
- Never call a safety mechanism "missing" without quoting the search that failed to find
  it.
- When a finding is disproved, write it up as a **retraction** rather than deleting it —
  the reasoning trail is the value.
- Distinguish plainly: *proven by measurement* / *code-evident but not exercised* /
  *suspected*. Never let the third read like the first.
- End with: what was tested, what was not, what is still open, and the run time.

---

## Appendix — the exact commands

```bash
# full regression, ~60 s, exit code 0 only if everything passes
python3 APPLICATION_AUDIT/STRESS_TEST/run_all_tests.py --app /home/user

# quick, ~40 s, skips the volume section
python3 APPLICATION_AUDIT/STRESS_TEST/run_all_tests.py --app /home/user --quick

# one-off delete proofs (each builds its own throwaway database)
cd APPLICATION_AUDIT/STRESS_TEST/DELETE_TESTS
AMS_APP_ROOT=/home/user python3 delete_suite.py
AMS_APP_ROOT=/home/user python3 delete_seq.py
AMS_APP_ROOT=/home/user python3 deltest4.py
AMS_APP_ROOT=/home/user python3 autofix_check.py

# integrity sweep against any database
python3 APPLICATION_AUDIT/TOOLS/db_integrity_audit.py <db> --out result.json
```
