# AUDIT AMENDMENTS — 2026-11-18

Corrections to `MASTER_ERP_AUDIT_2026-11-18.md` and `BUG_REGISTER_2026-11-18.csv`, raised during the remediation pass. These are changes to **my own findings**, not to the application. The application's behaviour was correct in both cases; my test setup or my page-reading was not.

---

## A-01 · F-06 "No CSRF protection" — WITHDRAWN

**Original claim:** a money-moving POST was accepted with no token and created a receipt, therefore the app has no CSRF protection (HIGH).

**What is actually true:** the app implements session-bound CSRF protection in `app/hooks.py::_protect_against_csrf`, registered as a `before_request` hook. Every form renders `_csrf_token` (layout + login), fetch/XHR calls send `X-CSRF-Token`, and the hook validates with `secrets.compare_digest`. The hook **stands down when `app.config['TESTING']` is true** (`if app.config.get('TESTING') or app.config.get('WTF_CSRF_ENABLED') is False: return None`) unless `AMS_CSRF_ALWAYS` is set.

**Why my test was wrong:** my audit harness set `app.config["TESTING"] = True` and `app.config["WTF_CSRF_ENABLED"] = False`. I was testing the app with its own protection disabled by my own harness, then reporting the absence.

**Evidence (gate forced on with `AMS_CSRF_ALWAYS=1`):**

| Request | Result |
|---|---|
| `POST /add_payment` with no `_csrf_token` | **HTTP 400** `{"error": "Invalid or expired form token. Reload the page and try again."}` — 0 rows created |
| `POST /login` with the token from the login page | HTTP 302 → dashboard |
| `POST /add_payment` with the session token | HTTP 302 — receipt created |

**Correction:** remove F-06 from the register. Security verdict for CSRF: **PASS**. The audit's "Security: FAIL — CSRF" line and the P0 priority #1 are withdrawn. (Authentication was already PASS; the app's security posture on the tested surface is now PASS/PASS.)

---

## A-02 · F-09 "'No charges on this GRN' after charges were saved" — WITHDRAWN

**Original claim:** after saving a GRN with a 5,000 freight charge, the app flashed "No charges on this GRN" (MEDIUM).

**What is actually true:** that sentence is the **empty-state alert** of the Expenses tab in `templates/grn_wizard.html`:

```html
<div class="alert alert-secondary small py-2 px-3 mb-2" id="grnChargesEmpty" {{ 'hidden' if charge_rows }}>
    <i class="bi bi-inbox me-1"></i>No charges on this GRN. ...
```

It is hidden whenever `charge_rows` exist. The post-save flash was always "GRN added successfully!". The charge was saved as a `grn_charge` row and capitalised into the landed cost (49,000 / 400 = 122.50 per unit) exactly as intended.

**Why my test was wrong:** I scraped the GRN page's visible text with tags stripped, which cannot distinguish a hidden empty-state container from a rendered message, and attributed the string to the flash.

**Evidence:** the charge row exists (`grn_charge` LOADING 5,000), the item's `landed_unit_cost` is 122.50, and the charge is listed on the GRN page. Re-test R-F09.

**Correction:** remove F-09 from the register. The success flash was nevertheless improved to state how many charge lines were capitalised — a cosmetic improvement, not a defect fix.

---

## A-03 · F-11 — severity framing corrected (not withdrawn)

**Original claim (partly wrong):** "the booking's 11,000 receivable stayed open after full delivery", implying a receivable defect.

**What is actually true:** once the goods are delivered, the booking balance **is** a real receivable — the customer owes 11,000 for material they now have. Leaving it open is correct. What was genuinely missing: the delivery document carried **no cost basis** (`cost_rate_at_sale` NULL, price 0), so the P&L had to estimate the cost of every booked dispatch.

**Correction:** F-11 is retitled "Booking-delivery sale carried no value or cost basis"; the receivable part is withdrawn; the missing cost freeze was fixed and re-tested (R-F11). The zero sale value is documented product policy (fulfilment only), not a defect.

---

## A-04 · Two harness lessons recorded

1. **Never run a behavioural test with the framework's TESTING flag silently changing security behaviour.** The audit harness will keep `TESTING=True` (needed for isolation and error surfacing) but must explicitly assert the production-relevant config (`AMS_CSRF_ALWAYS`) for security tests.
2. **Page-text scraping cannot see CSS/HTML state.** Any claim of the form "the screen said X" must name the element and its state (`hidden`/`show`), not just the string.

Both are now encoded in the re-test suite (`tools/retest_all.py`, test R-F06 and R-F09).

---

## Net effect on the audit's conclusions

| | Before | After amendments |
|---|---|---|
| Findings | 15 | **13** (F-06, F-09 withdrawn) |
| HIGH | 7 | **6** (F-01, F-02, F-03, F-04, F-05, F-07) |
| Security verdict | auth PASS / CSRF FAIL | **PASS / PASS** (tested surface) |
| P0 list | 5 items incl. CSRF | **4 items** (F-01, F-02/03/04, F-05) |

The overall audit verdict ("conditional release: trustworthy ledger, untrustworthy screens and write paths") is unchanged and, with the four P0 items now fixed and re-tested, the condition has been met for the tested surface.
