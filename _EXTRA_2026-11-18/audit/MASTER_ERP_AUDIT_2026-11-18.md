# MASTER ERP AUDIT — AMS v5.2 (Cement / Construction-Materials ERP)

**Audit date:** 2026-11-18 · **Auditor:** Arena Agent Mode (independent, black-box + white-box)
**Application:** AMS v5.2 — Flask 3.x / SQLite, 553 source files, 618 routes, 86 tables
**Subject of audit:** the delivered build in `AMSV5-2.zip` (GitHub `rizwanahmedsora9-pixel/arena-workspace`, `AMS/AMSV5-2.zip`)
**Nature of audit: AUDIT ONLY.** No source file, template, migration or production database was modified. Every defect below was reproduced, evidenced and left in place.

| Deliverable | File |
|---|---|
| This report | `MASTER_ERP_AUDIT_2026-11-18.md` |
| Bug register (15 findings, 11-part format) | `BUG_REGISTER_2026-11-18.csv` |
| Test results (33 tests) | `TEST_RESULTS_2026-11-18.csv` |
| Route inventory (618 routes, coverage flags) | `ROUTE_INVENTORY_2026-11-18.csv` |
| Financial reconciliation (17 figures) | `FINANCIAL_RECONCILIATION_2026-11-18.csv` |
| Regression recheck of B-01…B-09 | `REGRESSION_RECHECK_2026-11-18.csv` |
| One-page summary | `AUDIT_SUMMARY_2026-11-18.txt` |
| Raw evidence | `evidence/` (results.json, results_phases_1_6.json, routes_raw.csv, recon_raw.json, source hashes) |
| Reproduction tooling | `tools/` (harness.py, recorder.py, step1…step11) |

---

## 1. Executive summary

**Verdict: NOT READY FOR UNSUPERVISED PRODUCTION USE — conditional release.**
The transaction engine and the double-entry arithmetic are genuinely good: every rupee I pushed through the real forms is accounted for to the cent, stock never drifts, GRNs never self-pay, oversell and over-return are blocked, and duplicate submissions are rejected. The failures are concentrated in **one area with a systemic cause**: *the app has several different definitions of "what a customer owes"*, and in **one area with a systemic cause**: *the posting layer has no protection and no immutability*.

Headline numbers from a single day of real-form trading (opening cash 50,000, GRN purchases 209,000, sales, returns, a booking, a supplier part-payment):

- **Financial integrity: PASS.** Cash flow closes to the ledger (50,000 + 60,278 − 60,000 = 50,278 = account balance), profit report ties to independently recomputed FIFO landed cost (COGS 55,600; net revenue 78,500; net profit 22,900), supplier ledger ties (209,000 − 60,000 = 149,000), stock ties (1,990 in − 620 out = 1,370 = material totals), customer ledger ties (47,100 due − 28,878 paid = 18,222).
- **Cross-screen consistency: FAIL.** The same customer's debt reads **18,222 / 48,222 / 15,322** on three screens on the same day (`/client_ledger/2`, `/current_payables`, dashboard "Daily Due"). The dashboard's undelivered-booking tile read **90,000 against a true 30,000** — exactly 3×, because a client-level figure is added once per booking.
- **Returns: FAIL on allocation.** A 1,600 credit note for bill **B** was posted to unrelated bill **A** (5,722 → 4,122); B stayed 1,600. Returns are settled oldest-bill-first, with no link to the invoice returned. Walk-in (cash) sales **cannot be returned at all**.
- **Security: FAIL.** No CSRF validation anywhere. A money-moving POST with **no token at all** was accepted from the logged-in session and created a Rs 1 receipt that moved the customer's balance and the cash account. Authenticated routes do correctly redirect anonymous callers (PASS).
- **Immutability: FAIL.** "Void transaction" **hard-deletes** the posted sale, its items, its stock entry and its bill; the bill number is burned and only a one-line text audit record survives.

**Severity distribution (15 findings):** 0 CRITICAL · 7 HIGH · 5 MEDIUM · 2 LOW · 1 INFO.
**Regression recheck of the nine previously reported bugs:** 3 PASS · 2 PARTIAL · 4 FAIL (see part 4).

Because the traffic of a shop is dominated by exactly the flows that fail (walk-in sales, returns, bookings), this is a release that needs the HIGH items closed before it runs a real ledger without daily manual correction.

---

## 2. Scope, environment and provenance

### 2.1 What was audited
| Item | Value |
|---|---|
| Build | AMS v5.2, `BUILD_INFO` local HEAD `a3107ff`, built 2026-11-16; archive dated 2026-11-18 |
| Upstream | `github.com/rizwanahmedsora9-pixel/git-recovered`, branch `main`, HEAD `f3403ca9`, last push 2026-11-14T12:05:38Z |
| Provenance note | The zip is **not** a git checkout, so the archive cannot be pinned to a commit. The build's own `a3107ff` is *not* on the mirrored repo's `main` (`f3403ca9`, older). Treat the delivered zip as the authority and the repo as unverified context. Source revision is therefore recorded as a SHA-256 manifest of all 314 `.py` files (`evidence/source_hashes.txt`, with a pristine re-hash in `evidence/pristine_source_hashes.txt`). |
| Runtime | Python 3, Flask 3.x, SQLAlchemy + raw `sqlite3` paths, Jinja2, SQLite 3 (WAL), 86 tables, schema version v44 / 17 |
| Stack | requirements-lock.txt installed (pandas, openpyxl, reportlab, weasyprint-free PDFs, numpy-free) — background workers, schedulers and outbound mail disabled by the harness |
| Surface | **618 URL rules, 330 of them mutating** (POST/PUT/PATCH/DELETE) across 20 modules |

### 2.2 Isolation (proof that nothing production was touched)
- The app was **copied** to `AUDIT_2026-11-18/app_under_test/` (`tar`, excluding `instance/` and `__pycache__`); tests ran only from that copy.
- Tests ran against a **separate, freshly created database** `AUDIT_2026-11-18/test_db/audit_test.db` via `APP_DB_PATH` + `ALLOW_EMPTY_DB=1`. The app under test has no production DB in this environment; the only other DB file, `AMSV5-2/instance/ahmed_cement_v44_fresh.db`, was last written at 12:03 (before the audit began) and still contains **0 sales, 0 entries, 1 client** — i.e. untouched. MD5 `124f7a31809e32061d370f7c93e2d7d4`.
- **No SQL was used to create test data.** Every transaction below was created through the app's own HTML forms/routes with validation. All DB reading was read-only (`SELECT`, `PRAGMA`). The only writes to the DB were the app's own writes.
- CSRF: the harness deliberately posts as the app itself would; see F-06 — that finding was obtained by *omitting* the token, not by disabling a check.

### 2.3 Environment distinction check (gate before creating data)
Passed. Test DB path logged at boot (`DB loaded: …/audit_test.db`, counts `client_total=2`), DB file deleted and re-created empty before phase 1, and every phase re-verified the path from `app.config`. Production DB confirmed absent/untouched before any data was created.

---

## 3. Coverage

### 3.1 Route inventory
618 rules inventoried with module, endpoint, methods, mutating flag, parameters and whether this audit exercised them (`ROUTE_INVENTORY_2026-11-18.csv`). Menu items are a small subset: 20 modules expose routes, including `accounts` (65), `misc` (45), `masters` (42), `system` (42), `sales` (32), `ledgers` (24), `reports` (24), `import_export` (23), `api` (16), `ops` (12), `inventory` (10), `auth` (9), `legacy_migration` (7), `data_lab` (4), `admin` (4).

### 3.2 What was exercised end-to-end (23 routes, 33 recorded tests)
| Flow | Routes |
|---|---|
| Masters | client, supplier, material, account creation |
| Purchases | `/grn` (2 GRNs incl. a capitalised freight charge), `/accounts/pay_grn/save` + `/accounts/api/pay_grn/<id>` |
| Sales | `/add_direct_sale` × 5 (cash, credit ×2, booking delivery, idempotent repeat) |
| Money in | `/add_payment` × 4 (receipt, duplicate-key receipt, CSRF probe) |
| Returns | `/add_material_return` × 4 (normal, over-return attempt, two allocation probes) |
| Bookings | `/add_booking` × 3 (advance, undelivered ×2) + delivery |
| Destructive | `/void_transaction/<type>/<id>` × 1 |
| Reports/ledgers | `/`, `/profit_reports`, `/cash_flow`, `/current_payables`, `/client_ledger/2`, `/financial_ledger/2`, `/supplier_ledger/1`, `/inventory/stock_summary`, `/inventory/daily_transactions`, `/material_ledger/<id>`, `/system_report` |
| Security | anonymous access on 9 routes; CSRF probe on a money route |

**Test outcomes: 18 PASS · 12 FAIL · 1 PARTIAL · 2 INFO.**

### 3.3 NOT exercised (honest gap list — see part 10)
Loan/udhaar module, delivery rents & delivery-person ledger, GRN edit/void, bill edit, payment edit, expense/expenditure posting (endpoint unresolved), import/export round-trip, legacy migration, backups/restore, wipe/reset, multi-tenant/company switching, PDF/print output, Urdu locale, QR codes, notifications, user/role administration, "busy retry" behaviour under real concurrency, and any load/performance testing.

---

## 4. Regression recheck — previously reported bugs B-01…B-09

| ID | Claim | Verdict | Evidence |
|---|---|---|---|
| B-01 | Profit report arithmetic correct on a day's trading | **PASS** | Gross 86,200 − 500 = 85,700; COGS 61,600; net 24,100; margin 28.12% — matched an independent FIFO recomputation to the cent (T-REP-01) |
| B-02 | Return reverses cost and stock exactly once | **PASS** | 20 kg @140: COGS −2,400, stock +20 (300→320), cash untouched (T-RET-03) |
| B-03 | Dashboard separates undelivered bookings from collectable due | **FAIL** | Due 13,000 vs a true 29,000 (delivered booking value lost); undelivered tile 90,000 vs 30,000 (F-02, F-03) |
| B-04 | Credit note links to the original bill | **FAIL** | 1,600 return against bill B landed on bill A (5,722→4,122); B unchanged (F-01) |
| B-05 | Cash sale can be returned | **FAIL** | "Select a valid active client from the list." — no path for walk-in returns (F-08) |
| B-06 | Client ledger shows advance/delivered/receipt/undelivered without double counting | **PARTIAL** | The ledger itself is correct (47,100 / 28,878 / 18,222, undelivered 30,000 held separately) — but Current Payables (48,222) and the dashboard contradict it (F-04) |
| B-07 | Daily Breakdown classifies receipts, cash sales and return credits | **FAIL** | "Payments" tile counts return credits: 12,800 (incl. 2,800) then 23,878 (incl. 13,100) (F-05) |
| B-08 | Integrity Audit reports relevant inconsistencies, no false success | **PARTIAL** | It does flag (good) but its single finding is a false positive on its own booking-delivery flow, and it misses all three receivable inconsistencies (F-10) |
| B-09 | Cash flow ties to the cash ledger; supplier payment not expensed twice | **PASS** | 50,000 + 60,278 − 60,000 = 50,278 = account balance; the 60,000 appears once, never as an expense |

**Net movement since the last report:** B-01, B-02, B-09 confirmed fixed and stable; B-06 improved to PARTIAL; B-03, B-04, B-05, B-07 remain open; B-08 is a new-shape partial. **No regression** was introduced in the previously repaired areas.

---

## 5. End-to-end scenarios (traced: form → record → entry → ledger → report)

**S1 — Purchase (2 GRNs).** Form → `grn` SB-GRN-1000 160,000 and SB-GRN-1001 49,000 → `grn_item` with landed 122.50 on the freight-charged line → 3 `entry` IN rows → `material.total` +1,400 / +500 / +400 → supplier credited 209,000 → `/supplier_ledger/1` closing 149,000 after the 60,000 payment. GRN `paid_amount` forced to 0 (`payment_type='Credit'`): **a GRN never self-pays — correct.** Gaps: entries 1–3 carry **no source linkage** (F-12); flash "No charges on this GRN" after charges were saved (F-09).

**S2 — Cash sale.** 300 kg @150 = 45,000, discount 500, paid 44,500 → `direct_sale` amount 45,000 / paid 44,500 / discount 500 → OUT entry 300 → stock 1,400→1,100 → cash account 50,000→94,500 → profit gross includes 44,500 net of the 500 discount. Defect: a `pending_bill` with **amount 0.0** is still created (F-13).

**S3 — Supplier part-payment.** 60,000 against `MATERIAL:1` → `supplier_payment` + `account_transaction` OUT 60,000 → cash 34,500 → supplier remaining 100,000 → cash flow "spent" 60,000, never an expense. Correct.

**S4 — Credit sale + receipt.** 200 × 20mm @140 = 28,000 → pending bill `is_paid=0` 28,000 → receipt 10,000 `SB-CP-1000` → bill 28,000→18,000→ (after a 2,800 return) 15,200. Correct arithmetic; wrong allocation target (see S7).

**S5 — Booking + advance.** 100 × 12mm @160 = 16,000, advance 5,000 → `booking` + `account_transaction` source_type=Booking → cash 44,500→49,500 → 11,000 booking balance, no stock movement. Correct.

**S6 — Booking delivery.** Delivery sale `SB-SL-1002` → stock 12mm +100 (returned to 1,000), `booking_allocation` qty 100 → **but the sale is stored with amount 0.00, price 0.00, `cost_rate_at_sale` NULL**, and the booking's 11,000 receivable stays open (F-11). Revenue/cost are only derivable from the booking; the profit report shows "1 estimated-cost row" for it.

**S7 — Material return (credit note).** 20 × 20mm @140 = 2,800 → stock IN 20 (300→320), pending bill 18,000→15,200, a `payment` row of method "Material Return" created (source_type=MaterialReturn), **cash untouched**. Good: COGS reversed once, over-return blocked with an exact maximum ("Maximum returnable is 180.0"), idempotent. Bad: **no link to the returned bill.** Probe with two fresh bills A (1,500) and B (1,600) and a 1,600 return of B's goods: bill A's older sibling was reduced 5,722→4,122, **A and B untouched** (F-01). Also: a return against a *cash* sale is impossible (F-08).

**S8 — Void.** `POST /void_transaction/DirectSale/4` → flash "DirectSale **deleted**" → `direct_sale` row gone, items gone, `entry` id 8 gone, pending bill gone, stock correctly restored 280→330. Only `audit_log: transaction.delete.DirectSale id=4, reason=…` and `activity_event: Deleted DirectSale "SB-SL-1003" Rs 7,250` survive (F-07).

---

## 6. Financial reconciliation

Full table: `FINANCIAL_RECONCILIATION_2026-11-18.csv` (17 figures). Independent expectations were computed from raw tables and from GRN landed rates, never taken from the app.

| Figure | Expected (independent) | Observed | Variance | Verdict |
|---|---|---|---|---|
| Gross sales | 79,000 (92,100 documents incl. delivered booking − 13,100 returns) | 79,000 | 0 | PASS |
| Discounts | 500 | 500 | 0 | PASS |
| Net revenue | 78,500 | 78,500 | 0 | PASS |
| COGS (FIFO landed) | 55,600 | 55,600 | 0 | PASS |
| Net profit | 22,900 | 22,900 | 0 | PASS |
| Money received | 60,278 | 60,278 | 0 | PASS |
| Cash closing | 50,278 | 50,278 | 0 | PASS |
| Cash in / out | 60,278 / 60,000 | 60,278 / 60,000 | 0 | PASS |
| Supplier closing | 149,000 | 149,000 | 0 | PASS |
| Stock net | 1,370 | 1,370 | 0 | PASS |
| Customer receivable (client ledger) | 18,222 | 18,222 | 0 | PASS |
| Customer receivable (Current Payables) | 18,222 | **48,222** | **+30,000** | **FAIL (F-04)** |
| Collectable due today (dashboard) | 31,322 | **15,322** | **−16,000** | **FAIL (F-02)** |
| Undelivered bookings (dashboard) | 30,000 | **90,000** | **+60,000** | **FAIL (F-03)** |
| Day's payments (Daily Breakdown) | 10,778 | **23,878** | **+13,100** | **FAIL (F-05)** |
| Day's cash received (dashboard label) | 60,278 | 49,500 | −10,778 | INFO (F-14) |

**Reading:** the *books* are right; the *dashboards* are not. Every failure is a reporting/aggregation failure whose amounts are exactly identifiable (30,000 = the undelivered booking book; 16,000 = the delivered booking value; 60,000 = 2 extra copies of the client's undelivered value from 2 extra bookings; 13,100 = the day's return credit notes).

---

## 7. Database integrity

| Check | Result |
|---|---|
| `PRAGMA integrity_check` | ok |
| `PRAGMA foreign_key_check` | 0 violations |
| Orphan sale / return / GRN item rows | 0 / 0 / 0 |
| Entries pointing at deleted sources | 0 |
| Entries with **no** source linkage | **4** (all GRN receipts) — F-12 |
| Negative stock | 0 |
| Zero-qty sale lines | 0 |
| Duplicate bill numbers / duplicate idempotency keys | 0 / 0 |
| Duplicate pending bills per source document | 0 |
| Pending bills for voided sales | 0 |
| Payments without a matching account transaction (excluding credit notes) | 0 |
| WAL mode / busy-retry | enabled; 153 mutating handlers wrapped, 30 s budget |
| Schema | v44, 17 migrations applied, 86 tables |

**Conclusion:** the relational model is sound and the app's own writes are clean. The only structural weakness is traceability of purchase-side stock movements and the hard-delete of posted sales (which destroys rows rather than flagging them, so integrity checks cannot detect a "missing" document — by definition).

---

## 8. UI / route / security

- **Authentication: PASS.** All 9 probed business routes (GET dashboard, GET ledger, GET reports, POST client, POST sale, POST payment, POST return, POST GRN, POST pay-GRN) return 302 to the login page when anonymous; no data leak, no record created.
- **CSRF: FAIL.** No validation. A `POST /add_payment` with no token was accepted and created receipt id 4 (Rs 1); the client's oldest open bill moved 15,200 → 15,199 and cash 49,500 → 49,501. Forms render a hidden `_csrf_token` that nothing checks. (F-06)
- **Audit trail: PARTIAL.** `audit_log` records logins and deletes (with reason when supplied); `activity_event` records a human-readable delete line — but the deleted document itself is gone, and the audit row for the delete had `username = NULL`. (F-07)
- **Idempotency: PASS.** Resubmitted sale → "already saved (duplicate submission ignored)"; resubmitted receipt with the same `idempotency_key` → one row.
- **Input guards: PASS.** Oversell blocked with the available/required figures; over-return blocked with the exact maximum returnable; supplier-payment target validated ("Select a valid GRN recipient / charge to pay.").
- **Route hygiene:** 618 rules for a single-shop ERP (330 mutating) is a very large surface; several mutating routes are single-purpose one-click actions (`/activate_all_clients`, `/activate_all_materials`) — untested here, flagged as risk in part 10.
- **Messaging:** two misleading messages (F-09 "No charges on this GRN" after charges saved; F-07 "void" that says "deleted"), one ambiguous label (F-14).

---

## 9. Bug register (summary — full 11-part detail in the CSV)

| ID | Sev | Finding |
|---|---|---|
| F-01 | HIGH | Return credit settles the client's oldest bill, not the bill the goods came from (proved: 1,600 → bill A 5,722→4,122, bill B untouched) |
| F-02 | HIGH | Dashboard "Daily Due" drops the value of a delivered booking (16,000 short; clamp against client-level undelivered) |
| F-03 | HIGH | "Undelivered Bookings" tile multiplies the client's value by the number of bookings (90,000 vs 30,000) |
| F-04 | HIGH | "Total Outstanding"/"Current Payables" counts undelivered bookings the client ledger excludes (48,222 vs 18,222) |
| F-05 | HIGH | Daily Breakdown "Payments" tile counts return credit notes as payments (23,878 vs 10,778) |
| F-06 | HIGH | No CSRF protection; money-moving POST accepted without any token |
| F-07 | HIGH | "Void transaction" hard-deletes the posted document (row, items, stock entry, bill) |
| F-08 | MEDIUM | Cash/walk-in sale cannot be returned (client master required) |
| F-09 | MEDIUM | "No charges on this GRN" flash after charges were saved and capitalised |
| F-10 | MEDIUM | Integrity Audit false-positives on its own booking-delivery flow, misses the real receivable inconsistencies |
| F-11 | MEDIUM | Booking-delivery sale stores amount 0 and no cost rate; receivable not settled on delivery |
| F-12 | MEDIUM | GRN stock movements carry no source linkage (4 entries) |
| F-13 | LOW | Fully paid cash sale still creates a zero-amount pending bill |
| F-14 | LOW | "Daily Cash Received" tile label omits that it excludes separately-posted receipts |
| F-15 | INFO | Legacy GRN cost fields accepted and silently ignored (double-entry risk) |

No CRITICAL finding: no case of lost money, unauthorised write without a session, or unrecoverable corruption was found in the tested surface.

---

## 10. Untested / blocked

| Area | Status | Why |
|---|---|---|
| Operating expense posting | **BLOCKED** | `POST /accounts/expenditures` → 405; the real expense endpoint was not resolved from `url_map` in the time available, so the profit report's "Expenditures" line was never exercised with a real expense |
| Bill/payment **edit** routes (`/edit_bill/...`, `/edit_ledger_transaction/...`) | NOT TESTED | Would mutate a posted document; deprioritised behind the core chain |
| GRN edit / void, supplier-payment reverse | NOT TESTED | Destructive; needs a second supplier scenario |
| Loan / udhaar module | NOT TESTED | Out of the cash-trading path; no test data |
| Delivery rent & delivery-person ledger | NOT TESTED | Requires a delivery person + rent rate scenario |
| Import / export (CSV/Excel round-trip), legacy migration | NOT TESTED | High blast radius; not attempted on an audit copy |
| Backup / restore, wipe/reset, data lab | NOT TESTED | Destructive by definition |
| PDF / print / QR / receipts (incl. Urdu) | NOT TESTED | Rendered output not compared |
| Users, roles, permissions (only `Admin` used) | NOT TESTED | Single-role audit; a non-admin role may see or post more than it should |
| Concurrency / load / "busy retry" under contention | NOT TESTED | Single-threaded requests only |
| Multi-company / tenant switching | NOT TESTED | Single dataset |
| Notifications, directory, decision ledger, material brands UI | NOT TESTED | Read-only screens, no bearing on the money chain |

**Consequence:** the PASS verdicts above are scoped to the cash-trading chain and its reports. They must not be read as "the module is fine" for the untested areas.

---

## 11. Fix priority (recommended order — nothing was fixed in this audit)

**P0 — before the next real trading day**
1. **F-06 CSRF** — one-line app-wide `CSRFProtect`, plus a token check on mutating routes. Highest exposure per unit of effort.
2. **F-01 return allocation** — store the returned `sale_id` on `material_return` and settle that bill; fall back to FIFO only for the remainder.
3. **F-02 / F-03 / F-04 receivable definitions** — one function, one definition. Recommended: undelivered bookings are *never* a receivable; make `/current_payables`, the dashboard tiles and the client ledger call the same projection, and add an assertion to the Integrity Audit that they agree.
4. **F-05 Daily Breakdown** — reuse the existing `_is_cash_receipt()` filter (the dashboard already does).

**P1 — within the week**
5. **F-07 void** — status flip + reversal entries; keep the document; require a reason; keep delete for drafts only.
6. **F-08 walk-in returns** — a "Cash Customer" pseudo-client.
7. **F-11 booking delivery** — write delivered value and FIFO cost on the delivery document; settle the booking receivable on delivery.
8. **F-09 / F-12 / F-13** — message derived from saved charges; linked entries for GRN receipts; no zero-amount pending bill.

**P2 — housekeeping**
9. **F-10** integrity checks rebuilt into a real reconciliation suite (ledger vs payables vs dashboard vs stock).
10. **F-14 / F-15** — label clarity; remove or translate the legacy cost fields.

---

## 12. Final verdict

| Dimension | Verdict |
|---|---|
| Transaction capture (purchases, sales, receipts, returns, bookings, supplier payments) | **PASS** — every document landed with the right amounts, stock, cash and ledgers |
| Double-entry arithmetic (cash, supplier, customer, stock, COGS) | **PASS** — 12 of 17 reconciliations exact to the cent; all 4 exceptions are traceable to three code lines in one reporting function |
| Reporting / cross-screen consistency | **FAIL** — three different answers to "what does this customer owe" |
| Returns policy implementation | **FAIL** — wrong bill credited; walk-in returns impossible |
| Security (auth / CSRF) | **FAIL** — auth PASS, CSRF FAIL |
| Auditability / immutability | **FAIL** — posted documents are hard-deleted |
| Data integrity | **PASS** — clean schema, no orphans, no drift, no duplicates |

**Overall: conditional release.** The ledger core is trustworthy — I could not break the arithmetic, the stock, or the cash. What is not trustworthy is *the story the screens tell about that ledger*, and *the protection around the buttons that write to it*. Closing F-01, F-02, F-03, F-04, F-05 and F-06 — all of which are small, localised changes with exact reproduction steps in the register — would move this build from "needs daily manual correction" to "safe to run daily".

**Confidence and limitations.** Confidence is high for the tested chain (every claim above is backed by a stored record, query result or captured page text). It is deliberately low elsewhere: 12 named areas were not exercised, ~595 of 618 routes were not called, and only one user role was used. No performance, concurrency, backup/restore or import/export conclusions may be drawn from this report.

*Prepared 2026-11-18. Audit only — no fixes, no source edits, no production data touched.*
