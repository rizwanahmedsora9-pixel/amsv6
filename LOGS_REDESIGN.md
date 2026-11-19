# Logs section — implementation notes (2026-11-18)

Built and verified. This is the working code, not a proposal; the design
rationale and the measured "what was actually wrong" evidence are in
`LOGS_REDESIGN_PLAN.md`.

**Try it:** the app is running on port 5001 → sign in as `Admin` /
`Admin@fbm12345`, then open **Logs & Activity** in the sidebar (or `/logs`).
The database in `instance/` currently holds a few *real* demo records created by
running the app (3 clients, 1 booking) so the tabs are not empty on first look.
`python3 -c "import shutil; shutil.rmtree('instance')"` starts it fresh.

---

## 1. The problem this solves (measured, before any change)

Booted the original build, added a client, edited it, deleted it, viewed pages:

| Operation | Rows the old log kept |
|---|---|
| add a client | `http.post.masters.add_client \| by=Admin \| path=/add_client status=302` |
| delete a client | `http.post.masters.delete_client \| by=Admin \| path=/delete_client/2 status=302` |
| page views | **nothing** |
| any sale / booking / dispatch | **nothing but the generic breadcrumb** |

No client name, no bill number, no amount, no old value, no new value — and
`status=302` was written for refused saves too. `accounting_audit_log` did store
before/after JSON, but only from 39 write sites and **no page ever displayed it**.

## 2. What exists now

### 2.1 Capture engine — `app/services/activity_capture.py`
SQLAlchemy `before_flush` → stage a field-level diff; `after_flush_postexec` →
fill in the id and the human label; `after_commit` → persist; `after_rollback` →
discard. One place, 18 audited models, no route edits:

`DirectSale, Booking, Payment, MaterialReturn, Invoice, PendingBill, GRN, Entry,
StockAdjustment, SupplierPayment, Account, AccountTransaction, Client, Supplier,
Material, DeliveryPerson, User, ImportJob`

Consequences that matter in a shop:
* **Sales and ops are covered** — they had zero audit calls before.
* **A refused save writes nothing** (rollback discards it), so the log can no
  longer say "302" for work that never happened.
* **Soft deletes are classified as deletes**: `is_active → False`,
  `is_void → True`, `is_cancelled → True` are logged as *Suspended* / *Voided*
  with `result=soft|void`, and a restore as `result=restore`. An operator asking
  "who deleted this?" gets the suspend, not the earlier edit.
* **Amounts are stored as `amount_minor`** (integer paisa), never as a second
  float copy of the money.
* **Secrets are never copied** — `password_hash`, `qr_token_*`, Google tokens,
  recovery-code hashes are filtered out before serialisation.
* **It cannot break a save**: every listener body is wrapped; a logging bug logs
  at debug and is swallowed.

### 2.2 Presence — `app/services/presence.py` + heartbeat in `layout.html`
The old "live" badge just meant *seen in the last 45 minutes* (heartbeat was
every 5 minutes). Now the browser sends a beat every 60 s **only while the tab is
visible**, and reports seconds since real input (click / key / scroll / touch /
route change):

| State | Rule |
|---|---|
| ONLINE — WORKING | beat < 90 s, real input < 3 min |
| ONLINE — IDLE | beat < 90 s, no input for 3 min+ |
| AWAY | tab hidden but still beating |
| OFFLINE | no beat for 90 s, or signed out |
| EXPIRED | no beat for 45 min (row still open) |

Presence is mirrored onto `user_login_session.last_active_at / activity_state`,
so the old *Live logins* page and any report see the same truth.

### 2.3 Server logs now exist as files — `_configure_logging()`
Before: one file (`errorlog.txt`, WARNING+) and request lines went to **stdout
only**, so a "server logs" tab had nothing to read. Now, all in `instance/logs/`,
all rotating at 2 MB × 3 backups (~24 MB total ceiling):

| File | Content |
|---|---|
| `request.log` | one line per HTTP request: ip, method, path, status, ms |
| `server.log` | INFO+ app chatter (startup, migrations, workers, db-busy) |
| `errorlog.txt` | WARNING+ (unchanged) |
| `500-errors.jsonl` | structured crash records with `E-xxxxxx` reference (unchanged) |

### 2.4 The pages — `app/blueprints/system/logs.py` + `templates/logs/`
Every tab is its own URL (`/logs/<tab>`), so a filtered view can be bookmarked,
refreshed, printed, or opened on a second screen. Tabs carry live row counts.

| # | Tab | Source | Notes |
|---|---|---|---|
| 1 | Logins | `activity_event(category=login)` | success / failed / blocked / logout; failed logins were previously invisible |
| 2 | Entries added | `category=create` | type, bill no / client name, amount, user, path, full payload |
| 3 | Entries edited | `category=update` | **field-level diff**: field, before → after (8 shown, rest on expand) |
| 4 | Entries deleted | `category=delete` | Soft (Suspended) and Void distinguished from hard deletes |
| 5 | Page views | `category=view` | aggregated per (user, path, minute) + "most used screens" and 14-day bars |
| 6 | Import / Export | `import_job` / `import_upload` + events | file, rows, processed, errors, timings, plus export events |
| 7 | Error logs | `category=error` + `500-errors.jsonl` | **grouped** ("same error ×23"), traceback on expand, searchable by reference code |
| 8 | Server logs | the three files | level / logger / text filter, line count, **Download** |
| 9 | **Users** | presence + every `User` row | status pills, current page, IP, beats, multiple sessions per PC |

### 2.5 Filters (shared by all tabs, remembered in the URL)
Period (today / yesterday / 7 / 30 / 90 days / all / custom from–to) · user ·
module · result · IP · free text (bill no, client, user, path, IP, **error
reference**, or any changed field) · amount from/to · sort (newest / oldest /
largest amount) · CSV of the current filter · pager that preserves everything.

### 2.6 Honesty banner on every tab
States the real coverage window, how many events exist, and how many rows sit in
the legacy stores that could **not** be merged (they have no payload). If events
were dropped because SQLite was busy, it says that too, in red, instead of
pretending the trail is complete.

### 2.7 Retention — `python3 tools/maintenance.py purge-logs`
Defaults: events 365 days, page views 30 days, closed presence 90 days
(override with `--events-days/--views-days/--sessions-days` or
`AMS_LOG_RETENTION_DAYS`, `AMS_LOG_VIEW_RETENTION_DAYS`,
`AMS_LOG_SESSION_RETENTION_DAYS`). Nothing is purged automatically until you add
it to cron — the command is additive and safe to run on a live site.

---

## 3. Files

**New**
```
models/activity.py                        ActivityEvent, ActivitySession
app/migrations/0017_activity_log.sql      tables + indexes + 2 session columns
app/services/activity_capture.py          ORM capture, page-view buckets, purge
app/services/activity_queries.py          filters, paging, aggregates
app/services/presence.py                  heartbeat, states, per-user summary
app/services/system_logs.py               bounded log-file reader, grouping
app/blueprints/system/logs.py             /logs/<tab>, heartbeat, state API, download
templates/logs/index.html                 shell: tabs, banner, filter bar
templates/logs/_tab_entries.html          tabs 1–5 (shared table + diff viewer)
templates/logs/_tab_import_export.html    tab 6
templates/logs/_tab_errors.html           tab 7
templates/logs/_tab_server.html           tab 8
templates/logs/_tab_users.html            tab 9
templates/logs/_pager.html, _views_summary.html
static/logs.css                           styles (dark + light via ui.css tokens)
tools/logs_selfcheck.py                   25-assertion proof the pipeline works
```

**Changed (small, surgical)**
```
app/__init__.py           3 rotating log files; jinja filters age/prettyjson/filesize
app/hooks.py              capture install; page-view bucket; status stamp; logout presence
app/blueprints/auth.py    login success / failed / blocked / suspended events
app/services/errors.py    unhandled exceptions also land in the Error tab
models/__init__.py        import the two new models
app/services/sidebar_nav.py  new "Logs & Activity" entry
app/services/constants.py    permission map for the new endpoints
app/blueprints/system/settings.py  old /settings/activity + /settings/sessions redirect
blueprints/import_export/…  export + import-start events with real row counts
tools/maintenance.py      purge-logs subcommand
```

Nothing was deleted: the old pages still resolve (as redirects), `audit_log` and
`accounting_audit_log` still receive their rows, and no existing route changed
behaviour.

---

## 4. Verification actually performed

`python3 tools/logs_selfcheck.py` → **25/25 checks passed**, on a throwaway DB:

```
1. authentication          ok bad password rejected / ok admin login accepted
2. create/edit/delete      ok client created
3. failing save            ok refused save wrote no activity row
4. page view + presence
5. stored rows             ok login success / ok login failure / ok create /
                           ok update / ok suspend recorded as delete /
                           ok edit row carries a field diff / ok page views /
                           ok no activity row is empty of detail
6. every tab renders       ok logins, added, edited, deleted, views,
                           import_export, errors, server, users,
                           /api/activity/state, both legacy redirects
7. retention job           ok purge completed
```

Real rows produced by a real booking (sales had no audit coverage before):

```
[create/success] Booking      #1 "SB-BK-1000"  Added Booking “SB-BK-1000” Rs 8,000      path=/add_booking
[create/success] PendingBill  #1 "SB-BK-1000"  Added PendingBill “SB-BK-1000” Rs 8,000  path=/add_booking
[update/success] Client #2                     Edited Client (3 fields: name, phone, opening_balance)
                                               diff name: 'HASHIM TRADERS' -> 'HASHIM TRADERS RENAMED'
[delete/soft   ] Client #2                     Suspended Client “CHAUDHRY CEMENT STORE” Rs 15,000
[login/failed  ] Admin                         Failed sign-in for "Admin" from 127.0.0.1 (account exists)
[view /success ] core.index                    Viewed core.index
```

Live server check (port 5001, real HTTP, CSRF on): all nine tabs HTTP 200;
`POST /api/activity/heartbeat` → `{"ok":true}`;
`GET /api/activity/state` → `{"counts":{"working":3,…}}`;
`request.log` and `server.log` created and being written.

The two bugs found during verification — a `history` AttributeError in the diff
loop (a logging bug that was aborting a *save*) and the `errors` tab falling
through to the generic table — are fixed and covered by the self-check.

---

## 5. Known limits (deliberate, stated in the UI)

1. **No back-fill.** Nothing before this build is invented; the banner says where
   the old rows live and why they are not merged.
2. **Derived rows.** One action can write several rows (a booking also creates its
   pending bill). They are marked *part of the same save* rather than hidden — the
   truth is kept, the noise is labelled.
3. **Page views are bucketed**, one row per user/path/minute, and throttled to one
   write per path per 45 s. Exact per-second counting would make this the busiest
   writer in a SQLite database that already fights for the write lock.
4. **Presence needs the browser.** A tab that is closed, or a browser with JS off,
   goes OFFLINE after 90 s — correct, since there is genuinely no one there.
5. **`Account` diffs skip deferred columns** to avoid firing a SELECT per attribute.
6. **A busy database can drop an event** (best-effort, like the old audit log).
   Unlike before, the drop is counted and shown in red on the page.

## 6. Suggested next steps (not done)

1. **Force logout** from the Users tab — presence already knows the session id;
   it needs a revocation check on each request.
2. **"Someone else is editing this bill"** — reuse the same heartbeat to warn
   before two cashiers save the same document.
3. **Nightly digest** — errors + deletions + failed logins printed/emailed for
   the proprietor, from the same queries.
4. **Wire `purge-logs` into cron** next to `backup-daily` (one line, same tool).
5. **Fill the remaining capture gaps**: `DirectSale` needs a valid stock row to
   save in an empty yard, so it could not be exercised end-to-end here; the model
   is registered, but confirm it once against your live data.
6. **Urdu labels** for the tabs, matching the receipt UI.

## 7. Patch 2026-11-18 (v5.2.1) — "All logs" tab + filter-bar layout

Two defects reported from the live Settings → Full logs page.

1. **There was no combined view.** The section had nine tabs, each pinned to one
   `ActivityEvent.category`, so "everything that happened" was not available
   anywhere on one screen. Added **`/logs/all` — "All logs"** as tab 1 and as the
   landing target of `/logs` and of the sidebar link:
   * `AQ.ALL_TAB` / `AQ.all_query()` — the same table without a category
     predicate, same filters, same sort, same paging, same "part of the same
     save" derivation;
   * a `Category` filter (login / create / update / delete / view /
     import_export / error) that exists **only** on this tab and is dropped from
     the tab links of the other tabs, so it can never silently narrow them;
   * the stream shows the category pill, plus a result pill when the row is a
     failure.

2. **The filter fields had no usable layout.** `templates/_ui/filter_bar.html`
   renders `.ui-form > .ui-card-body > .ui-row`, and `ui.css` defines
   `.ui-form .ui-row` as a **12-column** grid. The logs bar has eleven fields and
   no `ui-col-*` spans, so every field was one twelfth of the card wide (labels
   clipped, inputs unusable) — it looked like the CSS had not loaded at all.
   `.logs-filter-grid` already existed in `logs.css` but was never applied
   because the macro was called without `extra_class`. Fixed by:
   * `ui_filter_bar(..., extra_class='logs-filter-grid')`,
   * `.ui-form .ui-row.logs-filter-grid` — `auto-fit / minmax(168px, 1fr)`,
     Search spans two tracks (`.ui-field-wide`), Apply / Clear / CSV get a
     full-width right-aligned row, narrow-screen breakpoints,
   * the inline `style="grid-column: span 2"` replaced by that class.

3. **Bonus fix on the same bar:** the **CSV** button pointed at `?format=csv`
   with nothing listening, so it silently reloaded the page. `logs_page` now
   streams a real CSV (UTF-8 BOM for Excel + Urdu) of exactly the filtered view,
   capped at 50 000 rows, on every activity tab.

Files: `app/services/activity_queries.py`, `app/blueprints/system/logs.py`,
`templates/logs/index.html`, `templates/logs/_tab_entries.html`,
`static/logs.css`, `app/services/sidebar_nav.py`.
