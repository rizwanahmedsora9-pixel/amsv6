# Logs section redesign — findings + design (2026-11-18)

Status: **proposal, awaiting 4 decisions.** No app code changed yet.

Everything below was verified by booting this build in a scratch copy
(`/tmp/ams_probe`), making real requests, and reading the resulting SQLite rows.
Nothing here is assumed from the docs.

---

## 1. What the app records today (measured, not assumed)

Booted the app, logged in as `Admin`, viewed 3 pages, added a client, deleted it.

| Request | Rows written to `audit_log` |
|---|---|
| `POST /login` | 2 (`auth.login` + `http.post.auth.login`) |
| `GET /`, `GET /settings`, `GET /settings/activity`, `GET /settings/sessions` | **0** |
| `POST /add_client` (succeeded) | 1 |
| `POST /delete_client/2` (succeeded) | 1 |

The seven rows, verbatim:

```
http.post.auth.login            by=Admin role=admin | path=/login status=302
auth.login                      by=Admin role=admin | ip=127.0.0.1 role=admin sid=6646... concurrent=allowed
http.post.masters.add_client    by=Admin role=admin | path=/add_client status=302
http.post.masters.delete_client by=Admin role=admin | path=/delete_client/2 status=302
```

That is the whole log. Consequences:

- **"Entries added" is not recorded.** There is no *what*. No client name, no id,
  no amount. The row says "somebody POSTed to /add_client and got a 302". The tab
  you asked for cannot be built honestly from this table — it would have to invent
  the entry details, which is exactly what you told me not to do.
- **GETs are invisible.** Page views (tab 5) are literally zero rows today.
- **Failure ≠ success.** `status=302` covers "client created" *and* "validation
  failed, try again". Both look identical in the log.
- **Errors are not in `audit_log`.** They go to `instance/logs/errorlog.txt` only.

### The one good table nobody can see

`accounting_audit_log` **does** hold real before/after data. The client
"delete" above actually wrote:

```
module=clients  action=Suspend  entity_type=Client  entity_id=2
username=Admin  ip=127.0.0.1
before={"code":"TST1","id":2,"is_active":true,"name":"ZHUMAR CLIENT"}
after ={"code":"TST1","id":2,"is_active":false,"name":"ZHUMAR CLIENT"}
reason=Client suspended
```

This is exactly the "entries edited / deleted" content you want, with a proper
diff. **It has no UI at all** — no template in the repo renders
`AccountingAuditLog`. It is write-only data.

Coverage is the problem: **39 writes across 15 files** — `accounts`, `suppliers`,
`clients`, `grn`, `grn_payments`, `inventory`, payments, loan register, void
rebuild. **`sales/`, `ops/`, `masters/` and `ledgers/` contain 0 `audit_log`
calls.** So a booking, a direct sale, a material return, a stock adjustment or a
dispatch edit leaves only the generic `http.post...` breadcrumb — no payload.
(The supplied DB is empty at `direct_sale: 0, booking: 0, payment: 0`, so this
cannot be verified against live traffic — only against the code paths.)

### Two defects to fix while we are here

1. **Session linkage is broken.** `accounting_audit_log.session_id` is filled from
   `session['login_sid']` or `session['_id']`. Neither key exists anywhere in the
   codebase — the real key is `ams_sid` (set by `utils/sessions.py`). So that
   column stores a 71-char Werkzeug session id that **cannot be joined to
   `user_login_session`**. IP survives; "which browser session, which tab, which
   user on a shared yard PC" does not.
2. **The 15 s audit stall the code comments warn about** is real for GET-heavy
   paths, and `busy_timeout=1500` means a busy DB silently *drops* audit rows.
   `audit_log()` swallows every exception (`except Exception: pass`). Under load
   the "full log" quietly has holes. Any new activity log must say so in the UI
   (e.g. "gap detected") rather than pretend the trail is complete.

---

## 2. What exists that we can reuse (no invention needed)

| Data you asked for | Real source already in the app |
|---|---|
| Logins | `user_login_session` (sid, ip, ua, created_at, last_seen_at, ended_at) |
| Failed logins | `app/services/login_throttle.py` + `security` logger (currently only a warning line) |
| Import / export ops | `import_upload`, `import_job`, `import_history_entry` — status, rows processed, errors, initiator |
| Errors | `instance/logs/errorlog.txt` (WARNING+, rotating 2 MB × 3) + `got_request_exception` hook |
| Server logs | see gap below |
| Entries edited/deleted | `accounting_audit_log` before/after JSON (39 write sites) |
| Entries added | **nothing usable** — must be captured |
| Page views | **nothing** — must be captured |

### Gap: there is no server log file to read

`_configure_logging()` (`app/__init__.py:615`) adds exactly one rotating file
handler at **WARNING**, for `errorlog.txt`. The request logger
(`logging.getLogger('request').info(...)`, `app/hooks.py:180`) writes
**INFO → stdout only**. On PythonAnywhere/WSGI stdout goes to the PA server log,
not to a file this app can read. So an "8. SERVER LOGS" tab has nothing to
display until we add a rotating request/app log file (or read PA's own logs,
which the WSGI process may not be allowed to open).

---

## 3. Proposed design

### 3.1 One table, one writer

New table `activity_event` (leave `audit_log` and `accounting_audit_log` intact
for compatibility and back-fill from them):

```
id, occurred_at, category, module, action, result,
entity_type, entity_id, entity_label, amount_minor,
summary, before_json, after_json,
user_id, username, role, ip, session_sid, request_id,
method, path, status_code, duration_ms, user_agent
```

`category` ∈ `login | create | update | delete | view | import_export | error | server`.
One writer, `app/services/activity.py::log_event(...)`, with the same
"never break the user's request" contract as `audit_log()` — but it records
*drops* in a counter so the UI can show "N events lost while DB was busy".

### 3.2 Capture, without touching 19 000 lines of routes

The important trick: **SQLAlchemy `before_flush` + `after_commit` hooks**, not
65 hand-edited call sites.

- Register `before_flush` on the session: for every `new` / `dirty` / `deleted`
  object of an audited model, stage a diff in a request-scoped buffer (model
  name, pk, changed columns, before → after).
- On `after_commit`, flush the buffer into `activity_event` with the request's
  user, IP, sid, path and duration.
- On rollback, drop the buffer — **a failed save never appears as an entry**,
  which fixes the "302 means success" problem for free.

This gives tabs 2–4 real data — table, primary key, human label (bill no / client
name), changed fields, old → new — for *every* model, including the modules that
have zero audit calls today. It is ~200 lines in one place, and it cannot lie:
it reads what the ORM is about to write.

Audited models: `DirectSale`, `Booking`, `Payment`, `SupplierPayment`,
`Entry`, `Invoice`, `PendingBill`, `GRN`, `MaterialReturn`, `Account`,
`AccountTransaction`, `Client`, `Supplier`, `Material`, `DeliveryPerson`,
`StockAdjustment`, `ImportJob`, `User`.

### 3.3 Page views (tab 5) — cheap, aggregated

Do **not** write a row per page load; this app's own docs are full of SQLite
lock pain. Instead upsert one row per `(user, path, minute)` with a hit counter,
written on the existing 250 ms busy-timeout connection. 45 min of history at
fine grain, then rolled up to `(user, path, day)` by the maintenance job.
Filters still work: page views by user / module / day.

### 3.4 The 8 tabs + users

1. **Logins** — every open/closed session, plus successful/failed/kicked logins.
   Columns: user, role, IP, device, in, last seen, out, duration, result.
2. **Entries added** — entity, bill/ref no, label (client/supplier name), amount,
   by, when, where (module → path).
3. **Entries edited** — same, plus a **diff view**: field, old → new. Built from
   `after_json`/`before_json`. Includes who and when.
4. **Entries deleted** — soft deletes (`is_active=False`) vs hard deletes
   distinguished with a badge, plus "what was removed / what stayed" summary for
   the sales/payment/advance cases your release notes describe.
5. **Page views** — user × page × hits, with a "most used pages" summary.
6. **Import / export** — real `import_job` data: file, rows, processed, errors,
   status, started/finished, initiated by. Exports from `export.data` events.
7. **Error logs** — parsed `errorlog.txt` + in-DB errors: level, timestamp,
   request id, path, user, message, traceback (collapsed), "same error ×23"
   grouping. Deduplication is what makes this tab usable instead of a wall of text.
8. **Server logs** — tail + level + text filter over the log files, with
   **Download** and explicit "showing last N lines / file rotates at 2 MB".
   Requires §2 gap to be fixed first (new request log file).
9. **Users** (your extra ask)

### 3.5 Users tab — presence honestly

Today `list_active_sessions(45)` calls a session "live" if seen in the last
**45 minutes**, and the heartbeat only writes every **300 s**. So every existing
"live" badge is really "seen recently" — it cannot separate online, idle and
offline.

Proposal: a tiny client-side heartbeat (`fetch`, ~60 s, only while the tab is
visible via the Page Visibility API) sends `{visible, last_input}`. Server stores
`user_login_session.last_active_at` + `activity_state`. Then:

| State | Rule |
|---|---|
| **ONLINE — WORKING** | heartbeat < 90 s ago **and** real input (click/key/scroll/route change) < 3 min |
| **ONLINE — IDLE** | heartbeat fresh, but no input for ≥ 3 min (tab open, user away) |
| **OFFLINE** | no heartbeat > 90 s, or `ended_at` set, or explicit logout |
| **SESSION EXPIRED** | cookie expired / row orphaned, cleaned by maintenance |

Per user: current page, IP, device, login time, working/idle since, total today.
Cost: one lightweight POST per visible tab per minute — same order as the
existing 5-minute heartbeat, and it *fills in* the idle gaps instead of guessing.

### 3.6 Filters (shared across tabs, remembered per user)

Date range (today / 7 d / 30 d / custom) · user · role · module · category ·
entity type + id · result · IP · free text · amount range (for money entries) ·
"only my actions". Plus: column sort, sticky filters in the URL (shareable),
CSV export of the filtered set, and optional auto-refresh (off by default).

### 3.7 Retention & honesty

- Daily purge: page views 30 d, activity events 12 months, error rows 6 months
  (all configurable in Settings), log files keep the existing rotation.
- A header on every tab states the real coverage window: "Activity capture began
  {date}. Earlier history: from `accounting_audit_log` only (39 write sites)."

---

## 4. Beyond your list — suggestions worth deciding on

1. **Failed-logins / security sub-tab** (brute-force by IP, lockouts). The data
   exists in `login_throttle`; currently invisible.
2. **Slow requests** (> 1 s, sorted) — this app has documented lock pain; a
   `duration_ms` column makes "why was saving slow at 4 pm" answerable.
3. **Gap detector** — if audit writes were dropped (busy DB), show a red banner
   rather than a silently complete-looking trail.
4. **"Who is editing this bill right now"** — reuse presence to warn before two
   cashiers save the same bill.
5. **Force logout** a session from the Users tab (needs a session-revocation
   check; today a stolen cookie stays valid).
6. **Tan/Urdu labels** for the log tabs to match the rest of the UI.
7. **Nightly digest** — errors + deletions + logins emailed/printed for the
   proprietor.
8. **Do not log money amounts twice**: money columns already have `_minor`
   mirrors; the log should store `amount_minor` and format at display, never a
   float copy.

---

## 5. Decisions needed before I write code

1. Capture method — model-level `before_flush` hooks (complete, one place) vs.
   editing the ~65 existing `audit_log()` call sites (narrower, won't cover
   sales/ops) vs. both.
2. Presence precision — per-minute heartbeat (accurate, ~1 write/min/visible tab)
   vs. keep coarse 5-min heartbeat (cheaper, cannot say "working").
3. Server log file — may I add a rotating `request.log` / `app.log` under
   `instance/logs/` (needed for tab 8), or must the tab read PA's own logs?
4. Retention defaults — 12 months events / 30 days page views acceptable?
