# AMS v5-2 — Release Notes

**Build date:** 2026-11-18
**Package:** `AMSV5-2.zip`
**Base:** v5-1 source tree (`/home/user/AMS`) + verified fixes B-01, B-02, B-03, B-06
**Database:** none shipped. A fresh database is created automatically on first run at
`instance/ahmed_cement_v44_fresh.db` (17 schema migrations apply themselves).

---

## What is new in v5-2

### B-01 — Profit Reports: the gross / discount / net strip

The three published figures (Gross, Discounts, Net) are now derived from the **same rows** the
profit is computed from, so the printed identity is true as displayed:

```
gross revenue − discounts − COGS − operating expenses = net profit
```

Before, gross and discounts were summed over *every* row while the profit used only rows with a
defensible cost, so the strip could contradict the profit by exactly the revenue of an excluded
row. New summary fields: `total_net_revenue`, `net_revenue_excluded`; `total_discounts` is now
derived (gross − recognized) rather than summed independently.

**Files:** `app/services/profit_loss.py`, `templates/partials/profit_loss_results.html`

### B-02 — Material returns: cost is always reversed

A return that could not be matched straight to its original supply line used to reverse revenue
but leave the bag's cost in cost-of-sales (row marked `cost_status = missing` and dropped from the
net). The matcher now falls back to the client/material bill chain and uses the **original sale
price and original actual cost rate**, so every resalable return reverses both:

* revenue at the price actually charged (including the returned share of any discount), and
* COGS at the original landed cost.

Verified on the reference scenario: a 1-bag return against a discounted cash sale reverses
**1,600 revenue, 100 discount and 1,240 cost**, and stays in the net profit.

**Files:** `app/services/profit_loss.py`

### B-03 — Dashboard: Daily Cash Received and Daily Due Amount

Two headline tiles were wrong on a normal trading day.

| Tile | Was | Now |
|---|---|---|
| Daily Cash Received | counted **material-return credit notes** as money received | cash only — trading cash in (booking advances + cash sales), with separate client receipts disclosed in the sub-label |
| Daily Due Amount | counted **undelivered booking balances** as collectable | due on goods actually delivered |
| Undelivered Bookings | mixed into the due figure | **new tile**, held separately |

The rule, applied per client: `delivered charge − money already in = still collectable`.
The cash and credit drill-downs behind both tiles use the same resolvers, so a tile and its
breakdown can no longer disagree. Reference scenario: cash **17,500**, due **6,200**, undelivered
**18,000** (was 22,650 / 26,700 / hidden).

**Files:** `app/services/financial_ledgers.py` (new shared resolvers `cash_receipt_components`,
`due_on_delivered_goods`, `is_cash_receipt`, `booking_undelivered_value`), `app/services/api.py`
(exports), `app/blueprints/core.py`, `app/blueprints/reports/_profit_financial_details.py`,
`templates/index.html`

### B-06 — Client Ledger: booking advances and the delivered balance

`/ledger/<client>` now separates the collectable delivered balance from the money still held as a
booking advance, and shows the undelivered remainder as its own line ("Undelivered Booking …
held separately, not in Net Balance") instead of folding the whole booking document into the net.

**Files:** `app/blueprints/ledgers/_client_financial_ledger.py`, `templates/client_ledger.html`

---

## Verification performed on this build

Run on a fresh database from the packaged source, transactions entered through the real forms:

| Check | Result |
|---|---|
| Boot, 17 migrations, admin user creation | clean |
| 38 routes | all 200 (four 404s in an early sweep were empty-record lookups, confirmed 200 once a master record existed) |
| GRN 100 @1,200 + 3,000 freight + 1,000 loading | landed **124,000**, **1,240/bag** |
| Booking 20 bags, 10,000 advance; 8 delivered | advance into cash, 18,000 undelivered |
| Cash sale 5 @1,600 − 500 discount | 7,500 collected, discount recorded once |
| Credit sale 4 @1,550 | receivable 6,200 |
| Return 1 bag against the cash sale | revenue −1,600, discount −100, **COGS −1,240**, included in net |
| Return 1 bag against the Bilal sale | revenue −1,550, **COGS −1,240** |
| Profit Reports | gross **23,050** − discounts **400** = net **22,650**; net profit **2,350** |
| Dashboard | cash **17,500**, due **6,200**, undelivered **18,000**, outstanding 22,650 |
| Client ledgers | Asif 12,000 / 12,000 / **0** (+18,000 held separately); Bilal 6,200 / 1,550 / 4,650 |
| Cash Flow | in 19,500 / out 4,200 / net 15,300 |
| Stock | 85 bags (100 in, 8+5+4 out, 2 returned) |
| Delivery rents | 500 earned, 0 paid, 500 outstanding |
| Integrity audit | `ok: true`, 0 issues, 0 duplicate postings |

---

## Known issues carried over (not fixed in v5-2)

| ID | Issue |
|---|---|
| **B-04** | A return credit is posted at sale price and is not linked to its originating bill, so it lands on the client's running balance instead of against the bill. A client who returns goods shows a reduced receivable (e.g. 4,650 instead of 6,200) and a cash customer can end up with a **negative** balance ("company owes client") with no refund outstanding. |
| **B-05** | A cash sale made to an unregistered walk-in cannot be returned — register the customer as a client first. |
| **B-07** | Daily Transactions "Payments" tile counts client receipts **and** return credit notes, and omits cash sales. Display only. |
| — | Booking-supply COGS is a reconstructed FIFO **estimate** (flagged "Estimated cost" in the report) because a booking delivery creates no cost allocation. |

---

## Running it

```
pip install -r requirements.txt        # or requirements-full.txt for Excel/PDF export
python main.py                         # serves on PORT (default 5000)
```

First run creates `instance/`, the SQLite database and the default admin user
(`Admin` / `Admin@fbm12345` — **change this before any real use**).

A `instance/secret_key` is generated on first run; do not ship or commit it.

Default credentials, database file and `instance/` contents are deliberately **not** included in
this archive.
