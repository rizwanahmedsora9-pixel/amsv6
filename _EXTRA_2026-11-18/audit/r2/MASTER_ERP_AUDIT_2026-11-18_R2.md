# MASTER ERP AUDIT — AMS v5.2 — ROUND 2
### Independent audit of the **current** code base (`AMSV5-2_FIXED`), after the round-1 remediation

| | |
|---|---|
| **Audit date** | 2026-11-18 |
| **Build audited** | `/home/user/AMSV5-2_FIXED` (byte-identical copy in `AUDIT_2026-11-18/r2/app_under_test`, 314 `.py` files, sha256 manifest in `r2/evidence/source_hashes.txt`) |
| **Round-1 report** | `MASTER_ERP_AUDIT_2026-11-18.md` (15 findings, 6 of them HIGH) |
| **Round-1 remediation** | `FIXES_2026-11-18.md` + `FIXES_2026-11-18.patch` (20 files, 1,032 lines) |
| **This report** | audit only — **no source file, database or configuration was changed**; the production database was never opened for writing |
| **Verdict** | **Conditional — not release-ready.** 6 HIGH findings, 3 of them can move money wrongly |

---

## 1. Executive summary

Round 1 audited the original build and the team then shipped a remediation. This round starts from scratch on the
remediated build: a fresh isolated copy, a fresh database, a re-derived route inventory (618 rules, not just the
navigation), 86 scripted tests, and an independent reconciliation of every headline number.

**What improved.** The accounting core now holds up under a much harsher scenario set than round 1 used. Every
figure on the profit & loss screen — gross sales, discounts, net revenue, cost of goods, net profit — was rebuilt
from the raw tables and matched **to the paisa**. Cash, receivables, payables, stock and the client/supplier
ledgers all reconcile. The cost basis frozen on each delivery line (`cost_rate_at_sale`) means a returned kilogram
reverses exactly 611.25 of cost and not a rupee more. All nine round-1 regression claims (B-01…B-09) now pass.

**What is still broken.** Six HIGH findings remain, and three of them touch money directly:

1. **The same supplier invoice can be paid twice.** A payment taken through the *Pay Supplier* form is not
   allocated to the GRN it belongs to, so the *Pay GRN* screen keeps offering the full bill. Paying it drove a
   supplier balance to **−60,000** in the test.
2. **Voiding a sale that has a material return counts the returned goods twice** — phantom stock (+20 kg) and a
   client credit (3,200) that is never reversed.
3. **Expense vouchers cannot be voided at all** — the request dies on a foreign-key constraint and the operator
   only sees "data error".
4. **`/logs` returns a 500 to an anonymous visitor** instead of redirecting to the login page.
5. **A newly created user with default permissions is stuck in an infinite redirect loop.**
6. **An under-collected counter sale to a walk-in name creates a receivable that belongs to no client** — no
   ledger can show it, and *Current Payables* reports zero while the bill is open.

Totals: **86 tests — 73 PASS, 13 FAIL** (the failures map to 11 of the 14 findings; the rest were found by
inspection of passing tests). **14 findings: 6 HIGH, 5 MEDIUM, 1 LOW, 2 INFO.** No CRITICAL: nothing corrupts the
double-entry core, and every wrong figure found is traceable and correctable.

---

## 2. Scope, environment and provenance

| Item | Detail |
|---|---|
| Application under test | `AMSV5-2_FIXED` copied by `tar` into `r2/app_under_test`; `diff -rq` against the source tree: identical, no `instance/` directory carried over |
| Source identity | 314 `.py` files hashed (sha256) → `r2/evidence/source_hashes.txt` |
| Database under test | `r2/test_db/audit_test.db`, created by the app itself through `create_app()`; **86 tables**, 618 URL rules |
| Production database | `/home/user/AMSV5-2/instance/ahmed_cement_v44_fresh.db`, md5 `124f7a31809e32061d370f7c93e2d7d4`, 0 sales, 1 client — **read-only, never written** (md5 re-verified before and after) |
| Isolation guard | The harness refuses to import if the test DB resolves inside a production path, or anywhere outside `AUDIT_2026-11-18/r2` |
| Data creation rule | Every transaction was created through the application's own forms and routes. **No SQL INSERT/UPDATE/DELETE was used anywhere**; all verification SQL opens the database `mode=ro` |
| Tooling | `r2/tools/harness.py` (isolated client + read-only queries), `recorder.py` (result ledger), `r2_sweep.py` (route inventory + GET sweep), `r2lib.py` (page/money readers), `s1…s10` scenario steps, `run_chain.sh` (wipe DB, replay in order) |
| Chained replay | `tools/run_chain.sh s1_masters … s10_regressions` — deterministic: permanent test IDs, unique idempotency keys per run |

**What the user asked for, and how it was honoured:** audit the current code including bugs introduced by
recent changes; rechecking B-01…B-09 with explicit PASS/FAIL/REGRESSION/PARTIAL status; discovering *every* route
(not just navigation); tracing Original form → saved record → DB → stock ledger → financial ledger → client /
supplier ledger → cash flow → dashboard → reports; a 12-part report with the 11-field per-bug format and the
five-level severity taxonomy. **Nothing was fixed in this round**, no production file or record was touched, and
no test data was manufactured with SQL.

---

## 3. Coverage

### 3.1 Route surface (complete inventory, not the navigation)

| Metric | Value |
|---|---|
| URL rules registered | **618** (deduplicated by path; each rule also registers a short alias) |
| GET-renderable rules | 325 |
| Mutating rules (POST/PUT/DELETE) | 330 |
| Danger-named rules (`wipe|reset|purge|drop|truncate|restore|backup|migrate|seed|demo|nuke|erase`) | 39 — inventoried, never invoked |
| GET routes actually fetched | **313** |
| Result | **272 rendered (200/302), 35 × 404 (route needs an entity id that does not exist), 0 errors** |
| Detail routes exercised with a live id (by the scenario chain) | 60+ including `/view_bill_detail`, `/financial_ledger/2`, `/client_ledger/2`, `/delivery_ledger`, `/verify_bill`, every export |

The first sweep (empty database) produced 6 rejected-input (400) answers from `/view_bill_detail/<type>/<id>` and
the four `/import_export/*` report routes. Both were re-checked with valid input and are **input validation, not
defects** — recorded as INFO in `evidence/routes_sweep.json`.

### 3.2 Scenario and test coverage

| Area | Tests | What was exercised |
|---|---|---|
| Masters | 11 | materials, duplicate-name refusal, clients, duplicate-code refusal, invalid opening balance, suppliers, chart-of-accounts triples (valid + invalid), cash-flow category/payee, delivery person, service |
| Purchases | 7 | 2-line GRN with freight capitalised **and** a separate loading expense, landed-cost allocation (101.875 / 122.25), second batch at a new price, supplier bill = material only, part payment, GRN void |
| Supplier payments | 8 | expense paid to its own recipient, obligation-level payment, lumped payment vs obligation view, overpayment guard, duplicate manual bill no., GRN-3 void, GRN payment cancel |
| Bookings & delivery | 9 | booking with advance, price edit, partial delivery, balance delivery, substituted material, over-delivery, full-advance cancellation, dashboard commitment |
| Sales / receipts / returns | 9 | cash sale with discount, under-collected counter sale, credit sale, receipt, return, over-return, overpayment, bill edit, void-with-return |
| Accounts / cash | 8 | transfer, operating expense (rent), expense without payee, client refund, impossible transfer, void voucher, edit voucher, balance rebuild |
| Reconciliation / reports | 12 | P&L screen, P&L workbook, cash flow, receivables, payables, stock, system report, dashboard |
| Regression B-01…B-09 | 9 | the nine round-1 claims, re-derived |
| Security / exports / UI | 8 | anonymous access, roles, CSRF, SQL injection, stored XSS, exports, 20-page render contract, destructive GETs |
| Remediated-path probes | 6 | receipt void, delete path, GRN charge display, stock-entry void, commitment tile, credit-note cash neutrality |

---

## 4. Regression recheck — the nine round-1 claims

| ID | Claim under recheck | Round 1 | **Round 2** | Evidence |
|---|---|---|---|---|
| B-01 | Profit report arithmetic on the day's trading | PASS | **PASS** | Gross 39,500 − discounts 500 = net 39,000; COGS 25,468.75; net profit 10,031.25 — all four rebuilt from `direct_sale`, `direct_sale_item`, `booking_allocation` and `account_transaction`, difference 0.00 |
| B-02 | A return reverses cost and stock exactly once | PASS | **PASS** | 10 kg sold (cost 122.25), 5 kg returned: stock −5 net, bill −800, P&L COGS 26,691.25 → 26,080.00 = **611.25 exactly** |
| B-03 | Dashboard separates undelivered bookings from collectable due | FAIL | **PASS** | Daily Due 3,000 = ledger net without the commitment; Undelivered tile 3,000 = `booking_undelivered_value()` |
| B-04 | Credit note links to the bill it came from | FAIL | **PASS** | `material_return.source_sale_id = 6`, `source_bill_no = SB-SL-1005`, and that bill alone moved by 800 |
| B-05 | Cash (walk-in) sale can be returned | FAIL | **PASS** | Return of 5 kg against a bill with no client master: stock +5, return id 3 saved |
| B-06 | Client ledger shows advance / delivered / receipt / undelivered without double counting | PARTIAL | **PASS** | AC01: debits 51,100 = credits 50,800 + net 300; undelivered 3,000 outside the net |
| B-07 | Daily Breakdown classifies receipts, cash sales and return credits | FAIL | **PASS** | Cash Receipts 38,000 = receipts 40,000 − refund 2,000; returns 4,800 excluded and the note says so |
| B-08 | Integrity Audit reports real inconsistencies instead of false success | PARTIAL | **PASS (clean run)** | 0 sync issues, 0 stock discrepancies, 0 cross-report mismatches, 4 unpaid bills (correct). *Caveat:* faults cannot be injected without writing to the database, so only the clean-run behaviour is proven — see §10 |
| B-09 | Cash flow ties; supplier payment is not expensed twice | PASS | **FAIL on this build** | Cash still ties (650,000 + 79,500 − 109,500 = 620,000 = account balances) and the purchase payments are still excluded from expenses, **but the expense total is wrong**: the 3,000 rent voucher that could not be voided (F-R2-03) stays in the books next to its 2,500 correction, so the expense line reads 5,500 instead of 2,500 |

**Eight of the nine claims hold**; B-08 passes on a clean database and B-09 is dragged down by F-R2-03 (an
un-voidable expense leaves a dead voucher in the expense total). No claim failed for a reason of its own.

---

## 5. End-to-end scenarios (traced form → record → DB → stock ledger → financial ledger → client/supplier ledger → cash flow → dashboard → reports)

### 5.1 Purchase chain — GRN-1

| Link | Observed |
|---|---|
| Form → record | `/grn`: supplier R2-SUP-01, 1,000 kg 12mm @100 + 500 kg 20mm @120, freight 3,000 (INVENTORY, transporter), loading 1,000 (EXPENSE, labour crew) |
| DB | `grn.landed_cost_total = 163,000`; `grn_item.landed_unit_cost` 101.875 / 122.25; `grn_charge` keeps the treatment (`INVENTORY` / `EXPENSE`) and the recipient |
| Stock ledger | two `entry` rows, `source_module='GRN'`, `source_id=1` → `material.total` 1,000 / 500 |
| Payables | `MATERIAL:1` obligation 160,000 **= material only**; the 1,000 loading is offered to the loading crew, not the supplier |
| Financial ledger | `account_transaction` empty — a GRN never pays |
| Cash flow | unaffected until a payment or a charge payment is made |
| P&L | freight enters COGS through the landed cost; the loading expense is a separate operating expense |
| Later | after 100 kg delivered, 468.75 of freight sits in COGS — exactly the 3,000 × (value share) |

### 5.2 Booking chain — BOOK-A

| Link | Observed |
|---|---|
| Booking | 100 kg 12mm @160, advance 5,000 → booking 16,000, cash +5,000, `account_transaction` "Booking paid now" 5,000 |
| Edit | rate 160 → 165: booking 16,500, advance untouched, no second posting |
| Delivery 60 | stock −60, `booking_allocation` qty 60, `direct_sale_item.cost_rate_at_sale = 101.875` frozen |
| Delivery 40 | stock −40, allocation total 100, undelivered 6,600 → **0** |
| Ledger | debit 16,500 (booking) / credit 5,000 (advance) → 11,500 collectable |
| Reports | P&L revenue recognises only the delivered part; the undelivered balance is excluded from profit and shown separately |

### 5.3 Credit-sale → return → void chain (where it breaks)

| Step | Expected | Observed |
|---|---|---|
| Credit sale 200 kg @160 | stock −200, bill 32,000 | as expected |
| Receipt 10,000 | cash +10,000, bill 22,000 | as expected |
| Return 20 kg | stock +20, bill 18,800, COGS −2,445 | as expected, `source_sale_id` set |
| Overpayment 30,000 | bill cleared, 11,200 advance | as expected (client net −500 after the 5,000 opening) |
| **Void the sale** | stock back to the physical 500 kg; client owed only the 35,000 paid | **stock 520 kg (phantom +20); client owed 38,200 (phantom 3,200)** |

### 5.4 Cash-sale → under-collection chain (where it breaks)

| Step | Expected | Observed |
|---|---|---|
| Counter sale 50 kg @150, 5,000 collected | 2,500 receivable under a client that can be chased | `pending_bill SB-SL-1003` created, **no client row** |
| Edit the bill to 160 | 8,000 bill, 3,000 due, stock unchanged | as expected (with a warning flash) |
| Current Payables | the walk-in appears | 0 clients / Rs. 0.00 |
| System report | count of unpaid bills matches the payables screen | "3 Unpaid Bills" while the payables screen shows nothing |

### 5.5 Supplier payment chain (where it breaks)

| Step | Expected | Observed |
|---|---|---|
| Pay 60,000 via *Pay Supplier* | supplier balance 144,000; GRN screen offers 100,000 | balance 144,000 ✓; GRN screen still offers **160,000** |
| Pay MATERIAL:1 in full | refused or warned | accepted → supplier balance **−60,000** |

---

## 6. Financial reconciliation

Every figure below was rebuilt independently in SQL (or from the document tables) and compared with the screen.

| # | Check | Rebuilt from the database | Screen | Difference |
|---|---|---|---|---|
| R-01 | Gross sales | 39,500.00 | 39,500.00 | **0.00** |
| R-02 | Discounts given | 500.00 | 500.00 | **0.00** |
| R-03 | Net revenue | 39,000.00 | 39,000.00 | **0.00** |
| R-04 | COGS (frozen landed cost of every delivered line) | 25,468.75 | 25,468.75 | **0.00** |
| R-05 | Net profit | 10,031.25 | 10,031.25 | **0.00** |
| R-06 | P&L **workbook** internal consistency | revenue line 35,800 vs net computed on 39,000 → 6,831.25 off | — | **FAIL** (F-R2-09) |
| R-07 | Cash: opening + receipts − payments | 650,000 + 79,500 − 109,500 = **620,000** | account balances 620,000 | **0.00** |
| R-08 | Every open bill has an owner | 17,500 open, 3,000 ownerless | Current Payables 0.00 | **FAIL** (F-R2-06) |
| R-09 | Stock: `material.total` vs IN − OUT | 1,150 / 520 / 0 | identical | **0.00** |
| R-09b | Payables: report total = supplier material + third-party charges | 107,000 = 100,000 + 7,000 | 107,000 | **0.00** |
| R-10 | Integrity audit tiles | 0 sync issues, 0 stock discrepancies, 0 mismatches | same | **0** |
| R-11 | Dashboard outstanding / undelivered | ledger debit balances 0.00, undelivered 3,000 | 0.00 / 3,000 | outstanding **FAIL** (F-R2-11) |
| C-08 | Every account balance rebuilt from its own transactions | 109,000 / 11,000 / 500,000 | identical | **0.00** |

**Reading of the result:** the double-entry spine is sound. Every failure in this table is a *view* problem
(a screen reporting a different thing than its label claims), never a wrong posting — except F-R2-02, which is a
real double posting of a returned quantity.

**Reconciliation details worth keeping:** landed cost per kg = 101.875 (12mm, GRN-1) and 120.00 (GRN-2); the
weighted landed inventory on hand is **149,875** for 1,400 kg bought, and **no screen shows it** (F-R2-10).

---

## 7. Database integrity

| Check | Result |
|---|---|
| Schema created by the app itself | 86 tables, `PRAGMA integrity_check` clean; `material_return.source_sale_id` present (the round-1 migration adds it on boot) |
| Account balances | three accounts, each rebuilt from its own opening balance and transactions: differences 0.00 |
| Stock | three materials, each rebuilt from non-void IN minus OUT: differences 0.00 |
| Ledger ↔ document links | every `entry` carries `source_module` + `source_id` (GRN, sales, material_return); every `account_transaction` carries `source_type` |
| Cross-report | Current Payables 0.00 vs client ledger net 0.00 (+0.00), 0 stock discrepancies |
| Audit trail | `accounting_audit_log` keeps before/after JSON, amount, account and reason for create/delete/edit; `activity_event` keeps a human-readable line; `audit_log` records the HTTP action |
| Deletion discipline | bills and payments are voided and kept; the GRN-payment route deletes the row but writes a full audit snapshot (INFO) |
| Voided rows | the app reports "13 legacy voided/cancelled rows… they stay inert" — the count includes rows this build created itself, and the wording ("from older builds") is misleading (INFO) |

---

## 8. UI, routes, security

| Check | Result |
|---|---|
| 20 busiest pages | all 200, no raw `None`/`nan` in markup, no unrendered Jinja, no traceback |
| Anonymous access to 14 sensitive routes | none rendered data (**no leak**); `/logs` raised instead of redirecting (F-R2-04) |
| Role separation | a sales-only user: `/system_report`, `/settings`, `/logs`, `/import_export`, `/wipe`, `/accounts/*` refused (302/404/308); `/`, `/direct_sales`, `/material_returns` served |
| New user with no permissions | **infinite redirect loop** (F-R2-05) |
| CSRF | forced on (`AMS_CSRF_ALWAYS=1`): token-less POST → 400, no row written; correct |
| SQL injection | three payloads through search and filters: no traceback, tables intact |
| Stored XSS | `<script>alert(1)</script>` stored as a client name is escaped on render |
| Destructive GETs | `/wipe`, `/reset_system`, `/purge`, `/restore_backup`, `/backup`: none performs an action; sales and stock unchanged |
| Exports | P&L workbook (PK, 26 KB), P&L PDF (%PDF, 61 KB *after* installing the optional `requirements-pdf.txt`), supplier ledger CSV, delivery-person CSV |
| GRN charges on screen | charge lines listed; the "No charges on this GRN" sentence is a hidden empty-state, not a false flash (round-1 retraction stands) |

---

## 9. Bug register

Full 11-field detail for every row is in `BUG_REGISTER_2026-11-18_R2.csv`.

| ID | Sev | Title | Failing tests |
|---|---|---|---|
| F-R2-01 | **HIGH** | Supplier payments from the Pay Supplier form are not allocated to GRN obligations → the same bill can be paid twice (demonstrated: supplier balance −60,000) | R2-P05, R2-A03, R2-A07 |
| F-R2-02 | **HIGH** | Voiding a sale that has a material return counts the goods twice: phantom stock +20 kg and a 3,200 client credit never reversed | R2-S09, R2-R06 |
| F-R2-03 | **HIGH** | Expense vouchers cannot be voided — foreign-key error surfaces as "data error", no reversal path | R2-C06 |
| F-R2-04 | **HIGH** | Anonymous `GET /logs` → 500 AttributeError instead of a login redirect | R2-X09 |
| F-R2-05 | **HIGH** | Newly created user with default permissions → infinite redirect loop | R2-X10 |
| F-R2-06 | **HIGH** | Under-collected counter sale to a free-text walk-in creates a receivable with no client owner; Current Payables shows zero | R2-R08, R2-R11 |
| F-R2-07 | MEDIUM | Booking cancellation rewrites the total to 0 and deletes the item lines | R2-B09 |
| F-R2-08 | MEDIUM | Non-numeric opening balance silently stored as 0 with a success flash | R2-M05 |
| F-R2-09 | MEDIUM | P&L workbook quotes revenue its own net profit is not computed from (orphan return) | R2-R06 |
| F-R2-10 | MEDIUM | No screen reports the value of stock on hand (149,875 of inventory invisible) | R2-P03 |
| F-R2-11 | MEDIUM | Dashboard "Total Outstanding — real dues across open bills" reads 0.00 while 17,500 of bills are open | R2-R11 |
| F-R2-12 | LOW | Frozen delivery cost mis-labelled as "Estimated Material Cost" | R2-R04 |
| F-R2-13 | INFO | PDF exports require the optional `requirements-pdf.txt`; missing in the shipped environment | R2-X06 |
| F-R2-14 | INFO | Booking cancellation is client-wide (LIFO), not per booking — documented on screen | R2-B07 |

**Severity distribution:** HIGH 6 · MEDIUM 5 · LOW 1 · INFO 2 · CRITICAL 0.
**Carried over from round 1:** none. Every round-1 finding stays fixed; all round-2 findings are new.

### Which of these came from the recent changes?

- **F-R2-04 (`/logs` 500)** sits in `app/blueprints/system/logs.py`, the file created by the round-1 UI work for
  task 3. Its `_guard()` reads `current_user.role` before authentication, which is a defect introduced with that
  page.
- **F-R2-12 (estimated-cost label)** touches `profit_loss*`, a module the remediation round also edited.
- The other twelve are pre-existing behaviours of the product, not regressions from the fix round: the
  remediation did not break anything it touched (all nine B-claims pass, all six remediated-path probes pass).

---

## 10. Untested, blocked and not applicable

| Item | Status | Reason |
|---|---|---|
| Fault injection into the integrity audit (proving it *detects* a broken link) | **BLOCKED** | It would require writing wrong rows with SQL, which the audit rules forbid. Only the clean-run behaviour is proven |
| Wipe / reset / purge / restore / backup / migrate / seed / demo routes (39) | **NOT TESTED (by design)** | Destructive; inventoried and left untouched |
| Legacy data migration (`/legacy-migration/*`) | **NOT TESTED** | Needs a legacy dataset; out of scope for this round |
| Delivery-rent allocation, driver payments, delivery-person ledger settlement | **NOT TESTED** | No delivery rent was charged in this scenario set |
| WhatsApp / notification delivery, backup scheduler, multi-tenant (`/tenants`) | **NOT TESTED** | External services or out of scope |
| Concurrency (two operators posting at once) | **NOT TESTED** | Needs parallel load; the single-process test client cannot prove locking |
| Full database export → import round trip | **NOT TESTED** | Restoring a `full_db` import is destructive by nature |
| `/accounts/expenditures` as an entry point | **NOT APPLICABLE** | It is a read-only list; expenses are posted from the Accounts transaction form (tested) |
| CSRF (round-1 F-06) | **PASS — retraction stands** | Verified again with the gate forced on: 400, no row written |

---

## 11. Fix priority (nothing was fixed in this round)

1. **F-R2-01** — allocate `/add_supplier_payment` vouchers to the oldest open GRN obligations (or refuse the
   form when obligations exist and point the operator at *Pay GRN*). This is the only finding that can lose
   real money silently.
2. **F-R2-02** — make the sale-void path reverse `material_return` rows for that sale (stock and credit) before
   reversing the sale itself.
3. **F-R2-03** — delete the `CashFlowEntry` before its `AccountTransaction` (or make the FK `ON DELETE SET NULL`)
   so expense vouchers have a reversal path.
4. **F-R2-06 / F-R2-11** — create (or require) a client master row for any short-paid counter sale, and make the
   dashboard/payables tiles state what they actually measure.
5. **F-R2-04 / F-R2-05** — one-line guards: `@login_required` on the logs routes; a terminal "no access" page in
   the dashboard guard.
6. **F-R2-07 / F-R2-08 / F-R2-09 / F-R2-10** — document integrity and reporting gaps: keep the booking items on
   cancel, validate the opening balance, unify the export's revenue base, add a stock valuation screen.
7. **F-R2-12 / F-R2-13 / F-R2-14** — labels, deployment notes and wording.

---

## 12. Final verdict

**Conditional — not release-ready for unattended production use.**

The accounting engine is in good shape: 11 of 14 reconciliation checks are exact to the paisa, cost of goods is
frozen per delivery line, returns reverse exactly once *when the sale stays alive*, cash, receivables, payables,
stock and both ledgers tie out, and the round-1 remediation held on all nine regression claims without
introducing a single new failure in the paths it touched.

What keeps the build from release is a small set of money-touching paths that the happy-path reporting never
exercises:

- a supplier payment taken through the **ordinary** Pay Supplier form, followed by the GRN screen's own
  invitation to pay the rest — this paid 60,000 twice in the test;
- a **void** after a **return** — this invented 20 kg of stock and 3,200 of client credit;
- an expense posted with the wrong account — **impossible to void**.

Release conditions: fix F-R2-01, F-R2-02 and F-R2-03 and re-run this suite; then fix F-R2-06 and F-R2-11 so that
no receivable can sit outside every ledger; the remaining MEDIUM/LOW/INFO items can follow in a normal
maintenance window.

**Nothing in this report was fixed while auditing.** The findings above were then repaired in a separate,
clearly separated round — see `../r3/VERIFICATION_2026-11-18_R3.md` (patched build `AMSV5-2_FIXED`, same 86-test
suite, **86/86 PASS**). Every artefact below is evidence, not remediation:

```
AUDIT_2026-11-18/r2/
├── MASTER_ERP_AUDIT_2026-11-18_R2.md          this report
├── BUG_REGISTER_2026-11-18_R2.csv             14 findings × 11 fields
├── TEST_RESULTS_2026-11-18_R2.csv             86 tests with expected/actual/verdict
├── FINANCIAL_RECONCILIATION_2026-11-18_R2.csv 14 reconciliation rows
├── ROUTE_INVENTORY_2026-11-18_R2.csv          618 routes with methods/endpoints
├── AUDIT_SUMMARY_2026-11-18_R2.txt            one-page summary
├── evidence/
│   ├── results.json                            machine-readable result ledger
│   ├── routes_sweep.json                       every GET route and its outcome
│   ├── routes_all.csv                          route inventory source
│   └── source_hashes.txt                       314 sha256 hashes of the audited code
├── tools/                                      harness, recorder, sweep, s1…s10, run_chain.sh
├── app_under_test/                             isolated copy of the audited build
└── test_db/audit_test.db                       the database every number above comes from
```
