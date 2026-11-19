# FIX + VERIFICATION REPORT — AMS v5.2 — ROUND 3
### Every finding from the round-2 audit repaired, then re-audited with the identical 86-test suite

| | |
|---|---|
| **Date** | 2026-11-18 |
| **Build repaired** | `/home/user/AMSV5-2_FIXED` (the build the round-2 audit examined) |
| **Patch** | `FIXES_2026-11-18_R3.patch` — 20 files, 849 diff lines, applies cleanly with `patch -p1` |
| **Verification build** | byte-identical copy in `AUDIT_2026-11-18/r3/app_under_test`, own database `r3/test_db/audit_test.db` |
| **Suite** | **the same 86 tests**, same chain (`s1 … s10`), same harness, same fixtures |
| **As-found (round 2)** | 73 PASS / **13 FAIL** — `../r2/evidence/results.json` |
| **After the fixes** | **86 PASS / 0 FAIL** — `evidence/results.json` |
| **Findings** | 14 (6 HIGH · 5 MEDIUM · 1 LOW · 2 INFO) — **13 fixed, 1 confirmed as correct behaviour** |
| **Audit evidence left untouched** | `r2/app_under_test` is still the exact as-found code; every r2 artefact still describes the unfixed build |

---

## 1. Result at a glance

| | As found (r2) | After fixes (r3) |
|---|---|---|
| Tests | 73 PASS / 13 FAIL | **86 PASS / 0 FAIL** |
| Total-stock route sweep (313 GET) | 272 ok, 0 errors | 272 ok, **0 errors** |
| P&L screen vs database | exact | exact |
| P&L **workbook** reproduces its own net profit | **no** | **yes** (39,000 − 25,468.75 − 3,500 = 10,031.25) |
| Supplier paid twice through two screens | **−60,000 balance** | **refused**: "Payment 160,000.00 exceeds the remaining payable 100,000.00" |
| Void of a bill that already had a return | 520 kg on hand (500 physical), client credit 3,200 short | 500 kg, client owed exactly 35,000 |
| Void of an expense voucher | "data error", nothing reversed | voided, cash back, document kept |
| Anonymous `GET /logs` | 500 AttributeError | 302 → `/login` |
| New user with no permissions | infinite redirect loop | 403 page that names the missing permission |
| Part-paid walk-in sale | receivable owned by nobody | client master created, `/current_payables` shows 3,000 |
| Stock on hand | no value anywhere | **117,665.62 + 61,736.25 on `/stock_summary`** |
| Dashboard dues tile | "Real dues across open bills" = 0.00 | "Net Client Dues 3,000 — open bills total 17,500" |

---

## 2. What was changed, finding by finding

### F-R2-01 (HIGH) — supplier payments are now allocated to the bill they pay

**File:** `app/services/grn_costing.py`
**New:** `supplier_level_applied_minor()`, `_direct_material_paid_minor()`; cache slot `_SUPPLIER_LEVEL_ALLOC` cleared with the other GRN line caches.

Unattributed supplier vouchers (`grn_id IS NULL`, recipient SUPPLIER, not an `[auto_grn_pay]` marker) are now allocated **oldest open bill first** over that supplier's material obligations, capped at each bill's open amount. `supplier_obligation_paid_minor()` adds that share, so the GRN page, the pay screen, the supplier ledger and the payables report all read the same number.

*Verified:* R2-P05 → "cash 100,000→40,000; supplier balance 204,000→144,000; obligations [('MATERIAL:1', 160,000, **paid 60,000, remaining 100,000**)]"; R2-A03 → "supplier ledger 100,000 = obligations 100,000 = pay_grn screen 100,000"; R2-A07 → the 160,000 payment is **refused**, supplier balance stays 100,000.

### F-R2-02 (HIGH) — voiding a bill reverses the returns that belong to it

**Files:** `app/services/void_rebuild.py` (`_cascade_sale_returns`, called from both void entry points), `models/sales.py` + `app/services/schema.py` (`material_return.voided_with_sale`).

Voiding a sale now voids its `material_return` rows first (stock back out, client credit reversed) and marks them, so restoring the bill restores exactly those returns and never one the operator voided on its own.

*Verified:* R2-S09 → "stock 20mm 320→500 (physical expectation 500); client net −6,200 → **−35,000**"; the P&L no longer has an orphan return, so R2-R06 passes too.

### F-R2-03 (HIGH) — an expense voucher can be voided, and is kept as a void

**File:** `app/services/cash_flow_svc.py` — `void_manual_cash_flow_entry()` rewritten; `app/blueprints/reports/cash.py` message.

Root cause was **two** broken links, not one: (a) the entry still pointed at the `account_transaction` being deleted → `FOREIGN KEY constraint failed`; (b) `cash_flow_entry_audit.entry_id` is `NOT NULL`, so deleting the parent tried to blank the audit trail. The service is now a real void: money reversed, entry flagged `is_void` with who/when/why, audit trail intact — exactly what the route name and every other module already promise.

*Verified:* R2-C06 → "voucher id 10 amount 3,000 … → **is_void = 1**; cash 108,500→111,500"; R2-REG-09 → expense line back to 2,500 instead of 5,500.

### F-R2-04 (HIGH) — `/logs` authenticates before it authorises

**File:** `app/blueprints/system/logs.py` — `_guard()` checks `is_authenticated` first and `@login_required` added to `/logs`.

*Verified:* R2-X09 → "clean" (anonymous `/logs` now 302 → `/login?next=/logs`).

### F-R2-05 (HIGH) — a permission-less account gets a page, not a loop

**File:** `app/hooks.py` — `_no_access_response()` + a terminal branch in the permission hook.

The dashboard is itself permission-gated, so redirecting a refused user to `/` re-entered the same guard. The hook now returns a **403 page** that names the missing permission and links the modules the account *can* open (or says none), plus a sign-out link.

*Verified:* R2-X10 → "redirect chain `[('/', 403, None)]`".

### F-R2-06 (HIGH) — a receivable always has an owner

**Files:** `app/services/lookups.py` (`ensure_client_master_for_name`), `app/services/void_rebuild.py` (used by `_sync_direct_sale_pending_bill`), flash text in both sale screens.

When a sale leaves a shortfall under a name that is not in the client list, the master row is created (or the existing one reused) and the pending bill gets that client code.

*Verified:* R2-R08 → "open bills 17,500; **bills with no client master = []**; `/current_payables` 3,000"; R2-R11 → dashboard dues 3,000 = ledger 3,000, open bills stated on the card.

### F-R2-07 (MEDIUM) — a cancelled booking keeps its lines

**Files:** `app/blueprints/ledgers/booking_cancel.py`, `models/sales.py` + `schema.py` (`booking_item.cancelled_qty`).

The line is no longer deleted: `qty` drops to the undelivered remainder and `cancelled_qty` records what was taken off, so material, rate and ordered quantity stay readable.

*Verified:* R2-B09 → "booking_item rows 1→1".

### F-R2-08 (MEDIUM) — an opening balance must be a number

**File:** `app/blueprints/masters/add_client.py`

Strict parse (commas and ₹ tolerated); on failure the client is **not** created and the message says so.

*Verified:* R2-M05 → "row=[]; flash: 'Opening balance "abc" is not a number … the client was NOT saved'".

### F-R2-09 (MEDIUM) — the P&L workbook can reproduce its own profit

**File:** `app/services/profit_loss_exports.py` — new Summary row **"Revenue Used In Net Profit (recognized)"** (`recognized_revenue`).

The sheet already carried the excluded-revenue line; it now also carries the base the net is computed from, so revenue → COGS → expenses → net is checkable inside the workbook. The orphan-return case that triggered it is gone with F-R2-02, so this is belt-and-braces.

*Verified:* R2-R06 → "revenue line 39,000; COGS 25,468.75; opex 3,500; reported net 10,031.25; revenue − COGS − opex = **10,031.25**".

### F-R2-10 (MEDIUM) — the shop can read what its stock is worth

**Files:** `blueprints/inventory/stock.py`, `templates/stock_summary.html`

A **Value at Cost** column (closing qty × the same FIFO unit cost the P&L uses) and a "Total stock value on hand" footer row.

*Verified (live page):* `R2 12mm Steel Bar … 1155 → 117,665.62 @ 101.88/Kg`, `R2 20mm Steel Bar … 505 → 61,736.25 @ 122.25/Kg`.

### F-R2-11 (MEDIUM) — the dashboard says what it measures

**Files:** `app/blueprints/core.py`, `templates/index.html`

The tile is now labelled **"Net Client Dues — client balances owed after advances and credits"** and the same card states **"open bills total Rs. 17,500"**, so the advance-netted amount and the gross bills are both visible.

*Verified:* R2-R11 → "dashboard dues 3,000 (ledger 3,000); open bills stated on the dashboard 17,500 (pending_bill 17,500)".

### F-R2-12 (LOW) — recorded cost stops being labelled a guess

**File:** `app/services/profit_loss.py` (FIFO branch that costs a delivery from dated lots)

When the reconstructed lot cost matches the cost frozen on the sale line to the paisa, the row is **recorded**, not "estimated". The estimated flag is now reserved for placeholder/fallback rates.

*Verified:* `estimated_cost_rows` = **0** (was 2), `actual_material_cost` now carries the full 25,570.63; COGS and net profit are unchanged (this is a classification, not a valuation, change).

### F-R2-13 (INFO) — PDF exports

No code defect: the runtime degrades with a correct message. Documented here rather than changed; installing `requirements-pdf.txt` makes `/profit_reports/export/pdf` return a real `%PDF` (verified again: 60,250 bytes).

### F-R2-14 (INFO) — booking cancel is client-wide LIFO

Confirmed as documented behaviour (the screen says "Cancel Remaining Booking (LIFO)"). **Not changed** — recorded as INFO so the next audit does not re-file it.

---

## 3. Database changes (additive only)

| Column | Table | Why |
|---|---|---|
| `cancelled_qty REAL DEFAULT 0` | `booking_item` | keeps ordered quantity after a cancellation |
| `voided_with_sale BOOLEAN DEFAULT 0` | `material_return` | lets a restored bill restore exactly the returns it reversed |

Added by `app/services/schema.py::_ensure_document_archive_columns()`, which is idempotent (PRAGMA check first) and runs on every boot next to the existing column migrations. `db.create_all()` covers fresh databases. **No column was dropped, renamed or re-typed; no row was rewritten by a migration.**

---

## 4. Behaviour changes a user will notice

1. Paying a supplier through **Pay Supplier** now reduces the bill the **Pay GRN** screen shows, and paying more than what is left is refused with the amount.
2. Voiding a bill **also voids its returns** (the client credit and the returned stock go with it); restoring the bill restores them.
3. Voiding an expense **keeps the voucher** with a "voided" mark instead of erasing it.
4. A part-paid counter sale to a new name **creates a client record** (code `WC-<NAME>`) so the balance can be chased.
5. A brand-new user with no permissions sees a **"This account has no access yet"** page with a sign-out link.
6. `/stock_summary` has a **Value at Cost** column and total; the dashboard card is **Net Client Dues** with the open-bill total beside it.
7. A cancelled booking line stays on the booking with `qty 0` and its cancelled quantity recorded.

---

## 5. Residual risks / not covered

- **Concurrency** is still untested: two operators paying the same bill at the same instant. The allocation is computed per request; SQLite's write lock serialises commits, but a multi-user race is not proven either way.
- **Legacy data**: supplier-level vouchers from an old book are now allocated FIFO to that supplier's oldest open bills. That is the standard reading, but a shop that used *Pay Supplier* as a pure advance account should re-check `/accounts/purchase_payables` after upgrading.
- **`/logs` 500 for anonymous users** is fixed, but the wider "anonymous may trigger attribute access" class was only probed on 14 routes; the route sweep covers 313 GETs as an admin, not anonymous.
- The **39 destructive routes** (wipe/restore/migrate) remain untested by design.
- **F-R2-14** (client-wide LIFO cancel) is unchanged by decision.

---

## 6. How to reproduce this verification

```bash
cd /home/user/AUDIT_2026-11-18/r3
bash tools/run_chain.sh s1_masters s2_purchases s3_supplier_payments s4_bookings s5_sales \
     s6_accounts s7_recon s8_register s9_security s10_regressions   # ~42 s, wipes the DB first
python3 tools/r2_sweep.py                                          # 313 GETs, 0 errors
```

To move the fixes onto the live build:

```bash
cd /path/to/AMSV5-2
patch -p1 --dry-run -i /home/user/AUDIT_2026-11-18/r3/FIXES_2026-11-18_R3.patch   # must print no failures
patch -p1 -i         /home/user/AUDIT_2026-11-18/r3/FIXES_2026-11-18_R3.patch
# back up the database first: the two new columns are added on first boot.
```

**Nothing was applied to the production copy `/home/user/AMSV5-2/`.** Its database still hashes
`124f7a31809e32061d370f7c93e2d7d4`; the patched tree lives in `AMSV5-2_FIXED` and in `r3/app_under_test`.

---

## 7. Artefacts

```
AUDIT_2026-11-18/r3/
├── VERIFICATION_2026-11-18_R3.md          this report
├── FIXES_2026-11-18_R3.patch              20 files, applies with -p1
├── BUG_REGISTER_2026-11-18_R3_FIXED.csv   the 14 findings with "Status after fix"
├── TEST_RESULTS_2026-11-18_R3.csv         86 tests, all PASS, expected/actual per row
├── evidence/results.json                  machine-readable result ledger (r3)
├── evidence/routes_sweep.json             313 GET routes, 0 errors
├── app_under_test/                        the patched build that produced these results
└── test_db/audit_test.db                  the database those numbers come from

AUDIT_2026-11-18/r2/                       unchanged as-found audit of the same build before the fixes
```
