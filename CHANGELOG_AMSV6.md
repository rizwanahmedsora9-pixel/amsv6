# AMS v6 — CHANGELOG (audit remediation)

- **Release:** AMS SYSTEM FOR EASE **v6** (application code), schema **v44** (unchanged — no migration added)
- **Previous shipped build:** v5.2, `BUILD_INFO.txt` build date 2026-11-16
- **Base repository revision:** `f3403ca9842a6c4ee7f7fd3abd28cb5068ad266e` (see `RELEASE_METADATA.json`)
- **Audit that produced these items:** `MASTER_ERP_AUDIT_2026-11-19.md` (12 parts) + `BUG_REGISTER_2026-11-19.csv`
- **Built:** 2026-11-19 (Asia/Karachi)

Everything below is a **code change made deliberately**, one finding per change, with the test that
proves it. Nothing was reformatted, renamed or "cleaned up" in passing.

---

## 1. Fixed in v6

### AMS-2026-1119-01 — HIGH — the integrity audit was blind to four ways money disappears
`app/blueprints/misc/_wipe_system_report.py` (`/system_report`)

The page walked `OUT` entries only and skipped the `Cash` category, so it reported
"No sync issues found" while a receivable, a receipt or a payment link was missing.
Three detector families were added to the existing report (they feed the same *Sync Issues* tile and
list, so no new screen is needed):

| New check | Catches |
|---|---|
| **Missing sale / booking receivable** — recompute what every live source document still owes (`amount − discount − paid_amount`) and require a live `pending_bill` for it | the receivable row of a sale or a booking was deleted |
| **Orphan receipt** — a non-void `payment` whose client name is not in the client master | a receipt attributed to a client that does not exist |
| **Paid without receipt** — a non-void bill marked `is_paid` with a real amount and no non-void payment pointing at its bill number | a receivable written off by flipping a flag, with no cash received |

Each block is wrapped so a diagnostic can never 500 the page (the page is the safety net).
Bills settled by discount at the counter (`amount = 0`) are excluded on purpose: they are legitimately
paid without a receipt and would otherwise be permanent false alarms.

**Evidence (fresh database, this build):**
- `S11-D00` control — un-corrupted copy → **Sync Issues 0, Stock 0** (no false positives)
- `S11-D02` deleted sale receivable → **detected**
- `S11-D03` deleted booking receivable → **detected**
- `S11-D04` orphan receipt → **detected**
- `S11-D05` paid-without-receipt → **detected**
- `S11-D01` stock corruption and `S11-D06` negative stock still detected (unchanged)
- 20/20 structural checks still pass (`S13`), full scenario chain **86/86** (`run_chain.sh`)

### AMS-2026-1119-02 — HIGH — a user with every permission at 0 could read eight `/api/*` endpoints
`app/services/constants.py`, `app/hooks.py`

The permission hook treated an unmapped endpoint as **allowed**, and the JSON surface (`/api/…`) had
almost no entries in the map. Now:

* `API_OPERATIONAL` is defined as "any one operational permission" (23 flags).
* 34 `ENDPOINT_PERMISSION_MAP` entries cover the JSON endpoints, each mapped to the module whose data
  it exposes (e.g. `api_grn_consumption` → GRN/stock, `api_check_bill` → sales/bookings/pending
  bills, `api_supplier_balance` → supplier ledger/payments).
* `BLUEPRINT_PERMISSION_PREFIXES['api'] = API_OPERATIONAL` — a **deny-by-default floor**: any `/api/`
  route added in future requires at least one operational permission instead of falling through.
* A refused JSON caller now receives **HTTP 403 with JSON**, not a redirect to the dashboard.
* A tiny explicit allow-list survives the floor: the caller's own theme switch and presence heartbeat
  (`_PERMISSION_EXEMPT_ENDPOINTS`). Nothing else bypasses the map. Admin/root are unchanged.

Deliberately broad (a cashier or storekeeper must not be locked out of the search boxes every form
uses) — but no longer empty, which is exactly what leaked.

**Evidence (fresh database, this build):**
- `S15-X04` zero-permission user → **all probed endpoints refused** (`[]` returned data), previously 7 endpoints returned business data
- over-lockout control: a user holding only `can_manage_sales` → **HTTP 200** on all 10 probed endpoints
- `S15-X01…X08` 8/8 PASS; CSRF, anonymous access, SQL-injection and XSS probes unchanged
- full chain **86/86** (every admin flow still works)

### AMS-2026-1119-04 — LOW — an impossible date was silently re-dated to "now"
`app/services/time_money.py`, `app/blueprints/sales/{_direct_sales_add_direct_sale,bookings,returns,payments}.py`

`resolve_posted_datetime()` swallowed a parse error and returned the fallback, so `2026-13-45` posted
the document on today's date with no warning. Added `posted_date_input_error()` and wired it into
every form that collects a date — direct sale, booking add, booking edit, material return add/edit and
client payment:

> `'2026-13-45' is not a valid date. Use the date picker (YYYY-MM-DD, or YYYY-MM-DD HH:MM) — nothing was saved.`

Blank dates keep their old meaning ("now"), and accepted formats are unchanged
(`YYYY-MM-DD`, `YYYY-MM-DD HH:MM`, `YYYY-MM-DD HH:MM:SS`, `YYYY-MM-DDTHH:MM`).

**Evidence:** `S12-V06` now **PASS** — `created=False`, no row, stock unchanged, danger flash shown.
The full chain still passes **86/86**, i.e. every normal date input is still accepted.

### AMS-2026-1119-03 — MEDIUM — no way to tell which code is deployed *(partially closed)*
Added `VERSION`, `RELEASE_METADATA.json` (base commit + release tag name + build date) and
`MANIFEST_SHA256.txt` (hash of every file in this zip), so the deployed tree can be identified and
verified against this release. **Still open:** the repository's pytest suite cannot import against
this tree (the shipped tree lacks the repository-only `restore_driver_payment` helper and its
`tests/` package). That divergence needs its own change and was not attempted inside a release build.

### AMS-2026-1119-06 — INFO — expenses are cash-basis *(documented, not changed)*
No accounting change. The policy is now stated where the owner looks: a footnote on
`/system_report` explains that operating expenses are recognised **when paid**, that an unpaid rent /
utility / wage bill therefore does not reduce profit until it is paid, and that unpaid *purchase*
liabilities are handled separately through GRN charges and Pay GRN. Converting to accrual accounting
is a business decision with data-model impact, not a bug fix.

### AMS-2026-1119-05 — RETRACTED (no code change)
"Re-pricing a booking after delivery is a silent no-op" was an **audit error**: the probe posted to
`/edit_booking/<id>`, a URL that does not exist. The real route is `/edit_bill/Booking/<id>`; retested
there, a booking at 10 kg × 200 becomes 2,500 after a re-price to 250 and the undelivered balance
moves 1,000 → 1,250 — only the remainder is re-rated, exactly as it should be. The register keeps the
retraction on record. **No application defect, no code change.**

---

## 2. What was deliberately *not* changed

* **Schema v44 and all 17 migrations** — untouched. Any existing database opens as-is.
* **Business logic and reports** — no formula, report, ledger or posting rule was altered; the
  arithmetic still reconciles to the paisa (gross 39,500 · discount 500 · net 39,000 ·
  COGS 25,468.75 · opex 3,500 · profit 10,031.25 · stock 179,401.87 · cash closing 664,000).
* **Design findings from the earlier round** (default admin credential, non-enforcing health guard,
  no shipped tests, import cycle) — unchanged; the first fix remains "change the admin password on
  first login", which is an operational step, not a code change.
* **No database files ship in this zip** — see §4.

---

## 3. Files touched (complete list)

```
app/blueprints/misc/_wipe_system_report.py     +3 detector families (finding -01)
app/services/constants.py                      API_OPERATIONAL + 34 API mappings + 'api' blueprint floor (-02)
app/hooks.py                                   exempt allow-list, JSON 403 for /api (-02)
app/services/time_money.py                     posted_date_input_error() (-04)
app/services/api.py                            export posted_date_input_error (-04)
app/blueprints/sales/_direct_sales_add_direct_sale.py   date guard (-04)
app/blueprints/sales/bookings.py                        date guard, add + edit (-04)
app/blueprints/sales/returns.py                         date guard, add + edit (-04)
app/blueprints/sales/payments.py                        date guard (-04)
templates/system_report.html                   accounting-basis footnote (-06)
VERSION, RELEASE_METADATA.json,                release identity (-03)
MANIFEST_SHA256.txt, CHANGELOG_AMSV6.md        this file
BUILD_INFO.txt                                 v6 header block appended
```

---

## 4. Verify this build in five minutes

```bash
unzip amsv6.zip -d amsv6 && cd amsv6
pip install -r requirements-lock.txt
python main.py                     # http://127.0.0.1:5000  (fresh DB is created on first boot)
#   login Admin / Admin@fbm12345   -> change the password immediately
sha256sum -c MANIFEST_SHA256.txt   # every file matches this release
```

Then check the three fixes by hand:

1. **Integrity audit** — open `/system_report` as admin: it must say 0 sync issues on a clean
   database, and must name the document if you delete a `pending_bill` row from a *copy* of the DB.
2. **Permissions** — create a user through `/add_user` and leave every permission at 0; log in as
   that user: `/api/clients/search?q=a` must answer **403**, not a client list.
3. **Posting date** — try to save a sale with the date `2026-13-45`: it must refuse with the message
   above and write nothing.

---

## 5. Evidence files (kept outside the app, in the audit workspace)

`ERP_AUDIT_2026-11-19/evidence_v6/` — `results_v6_chain.json` (86/86), `results_v6_s11.json`
(7/7 fault detection), `results_v6_s13.json` (20/20), `results_v6_s15.json` (8/8),
`results_v6_s12.json` (15/15), `results_v6_s14.json` (7 PASS, 1 FAIL pre-existing on a fresh DB,
1 N/T).

S14-C07 fails identically on the **untouched** v5.2 tree against the same database, i.e. it is a
data-state artefact of a freshly seeded database (the sales list is paginated and short), not a v6
regression.
