# TASKER — Isolated Delivery Tracking Board (PLAN → AS-BUILT)

> Status: **IMPLEMENTED 2026-11-22** — owner decisions confirmed (see §8).
> **2026-11-23:** admin-only cleanup + auto-clean added (see §9).
> Goal: a fully isolated tracking module. No sales, no delivery, no bill, no stock,
> no payments — nothing else in the app is read from or written to except
> read-only name lookups (clients / materials) for the combo boxes.

---

## 1. What it is

A single page **"Tasker"** in the sidebar. Admin creates a *task*:

- **Client** — picked from a combo-box search list (type-to-search, like the
  existing client search style).
- **Item list** — one or more lines, each picked from the **material/brands**
  list (combo search), with **quantity + unit + warranty (wty)** per line.
- **Time allowed to deliver** — a deadline set by admin at creation.

Then the floor runs a strict 4-step stamp workflow, each step stamped with
**who** and **when**:

| Step | Who | Action |
|------|-----|--------|
| 1. **Noted** | first user who sees it | "Marked as noted at first sight" |
| 2. **Loaded** | loader | after loading the material (task asked to loader/delivery person) |
| 3. **Dispatched** | loader | when loader dispatches it |
| 4. **Delivered** | loader | when loader confirms delivery — task closes (green) |

**Total list** board shows every task with a live countdown. If the task is not
noted (or not fulfilled — see open question #1) within the admin-given time it
turns **RED** and an **alarm sounds in a loop** — it cannot be silenced by
normal users — **until admin opens it and resets the time manually with a new
deadline**. Every reset is logged.

---

## 2. Isolation guarantees (the hard requirement)

1. **Own tables only** — `tasker_task`, `tasker_item`, `tasker_event`,
   `tasker_deadline_reset`. No foreign keys to sales / delivery / billing /
   stock / cash tables. Nothing in the app can cascade into it or out of it.
2. **Snapshot data** — client name/code and material name/code/unit are
   COPIED into the task when it is created. Even if a client or material is
   later renamed/deleted, the task keeps its original text. Tasker never
   "follows" master data.
3. **Read-only lookups** — the combo boxes query `Client` / `Material` /
   `DeliveryPerson` for names only (SELECT). Tasker never writes to them.
4. **Own blueprint package** — `blueprints/tasker/` (auto-loaded module dir),
   physically separate from `app/blueprints/` business modules.
5. **No hooks** — Tasker does not register listeners, does not touch
   notifications/recon/backup/audit pipelines of other modules. Its own event
   log (`tasker_event`) is self-contained.
6. **One nav touch** — a single `NavLink("tasker", …)` entry in
   `app/services/sidebar_nav.py` (the data-driven registry). No other existing
   file is modified except one import line in `models/__init__.py`.

---

## 3. Data model (new file `models/tasker.py`)

### `TaskerTask`
- `id`, `task_no` (`TK-0001`-style, permanent)
- client snapshot: `client_ref_id` (plain int, optional, for search only),
  `client_code`, `client_name` (text, authoritative)
- `status`: `NEW → NOTED → LOADED → DISPATCHED → DELIVERED` (+ `CANCELLED`)
- **two deadlines** (owner decision): `note_deadline_at` (by when some user
  must mark NOTED) and `delivery_deadline_at` ("time allowed to deliver")
- stage stamps: `noted_by/at`, `loaded_by/at`, `dispatched_by/at`,
  `delivered_by/at` (+ `delivered_note` free text).  Whichever user presses
  the button is stamped — no loader assignment (owner decision).
- alarm: `note_alarm_on`, `delivery_alarm_on` (bools), `note_overdue_since`,
  `delivery_overdue_since`, `deadline_reset_count`
- `created_by`, `created_at`, `cancelled_*`, `status_before_cancel`

### `TaskerItem` (child of task, internal FK `task_id`)
- item snapshot: `material_ref_id` (optional int), `material_code`,
  `material_name` (brand/item text, authoritative)
- `qty`, `unit`, `warranty` (free text, e.g. "5 years", "n/a"), `note`

### `TaskerEvent` (self-contained audit trail)
- `task_id`, `event` (`CREATED`, `NOTED`, `LOADED`, `DISPATCHED`, `DELIVERED`,
  `DEADLINE_RESET`, `ALARM_ON`, `ALARM_OFF`, `CANCELLED`, `REOPENED`, …)
- `by_user`, `at`, `detail` (short text)

### `TaskerDeadlineReset`
- `task_id`, `kind` (`NOTE` | `DELIVERY`), `old_deadline`, `new_deadline`,
  `by_user`, `at`, `reason`

Tables are created by the existing `db.create_all()` + auto-migrate startup
path — no manual migration needed for new tables.

---

## 4. Deadline & alarm behaviour

- **Two deadlines** are set by admin at creation (datetime picker + quick
  chips: +15m/+30m/+1h/+2h for the note deadline; +2h/+4h/tonight/tomorrow
  for the delivery deadline).
- A **client-side live countdown** runs on every open Tasker page (JS timer +
  `/tasker/api/board` poll every ~10 s so all users stay in sync).
- **Overdue rule** (owner decision — each deadline is independent):
  - `now > note_deadline_at` while status is still `NEW` → note alarm;
  - `now > delivery_deadline_at` while not `DELIVERED`/`CANCELLED` → delivery
    alarm;
  - each trip turns the row **RED with a pulsing highlight** + `NOTE LATE` /
    `DELIVERY LATE` chips and starts the **looping alarm sound** (Web Audio
    beep — no audio file, works offline).
- **Global scope** (owner decision): a **police-style siren** + a fixed **red
  flashing banner** ("⚠ TASKER ALARM — N deadline(s) missed — TK-0001
  (NOTE+DELIVERY) …") follows users on **every AMS page** via
  `static/tasker_alarm.js` (two `<script>` includes in `layout.html`: the
  shared `tasker_siren.js` and the widget; the widget polls
  `/tasker/api/alarm_state` every 5 s).  Browsers block Web Audio until the
  first user gesture, so the banner carries an **Enable siren** button and any
  click/keypress unlocks the siren; the red message never depends on audio.
  The board page itself uses the same shared siren and its own flashing
  `tkAlarmBar`, which lists the overdue task numbers and deadline kinds.
- The alarm **cannot be dismissed by normal users** (no snooze, no mute). It
  keeps sounding until **admin**:
  1. opens the task (this is "admin sees it"),
  2. clicks **Reset time**, enters a **new manual deadline** (must be in the
     future) or clears that deadline,
  3. the reset is stored in `TaskerDeadlineReset` + `DEADLINE_RESET` event,
     the row returns to normal colour and the countdown restarts.
- If the new deadline is missed again → red + alarm again
  (`deadline_reset_count += 1`).
- **Total list header** shows counters: Total / Awaiting note / In progress /
  Delivered / **Overdue (red)**.

---

## 5. UI (one page `templates/tasker.html` + `static/tasker.js`)

Extends `layout.html`, Bootstrap 5 + icons + flatpickr like the rest of AMS.

- **"+ New Task"** (admin) → modal:
  - Client combo box (type-to-search dropdown, keyboard navigable)
  - Item lines: add/remove rows — material/brand combo, qty, unit,
    warranty (wty), line note
  - "Time allowed": two datetime pickers + quick chips (note: +15m…+2h;
    delivery: +2h, +4h, tonight 8pm, tomorrow 10am)
  - Task note
- **Board** = the total list: table/cards with
  - task no, client, items summary (+qty/wty), both deadline countdowns,
    status chips (+ `NOTE LATE` / `DELIVERY LATE`), stage stamps,
  - colour states: grey `NEW`, blue `NOTED`, amber `LOADED`, indigo
    `DISPATCHED`, green `DELIVERED`, **red pulsing rows when overdue**,
  - filter chips: All / Active / Overdue / Delivered / Cancelled,
    plus text search,
  - row actions (one click): **Noted → Loaded → Dispatched → Delivered**
    (any user); admin extras: **Reset time**, Cancel, Reopen; expandable
    detail row with items table, timeline and reset history.
- **Alarm banner** at the top of the page whenever any deadline is overdue:
  flashing red bar "⚠ TASKER ALARM — N deadline(s) missed — TK-0001
  (NOTE+DELIVERY) …", with the shared police siren.  It carries an
  **Enable siren** button (browsers require one click before audio), and the
  admin header has a **Test siren** button to unlock/check the sound before a
  deadline is missed.

---

## 6. Files touched (complete list)

| File | Change |
|------|--------|
| `models/tasker.py` | **NEW** — 4 tables above |
| `models/__init__.py` | +1 import line |
| `blueprints/tasker/__init__.py` | **NEW** — module + `MODULE_CONFIG` (auto-loaded) |
| `blueprints/tasker/views.py` | **NEW** — page route |
| `blueprints/tasker/api.py` | **NEW** — board JSON, combo lookups (read-only), status actions, reset deadline |
| `templates/tasker.html` | **NEW** — the page |
| `static/tasker.js` | **NEW** — combos, countdown, polling, alarm sound |
| `static/tasker_siren.js` | **NEW** — shared Web Audio police siren (`window.AMSTaskerSiren`) |
| `static/tasker_alarm.js` | **NEW** — global red flashing banner + police siren on every page |
| `templates/layout.html` | +2 `<script>` includes for the shared siren + global alarm widget |
| `app/services/sidebar_nav.py` | +1 `NavLink` ("Tasker") |
| `tests/test_tasker.py` | **NEW** — lifecycle, overdue→red→reset, isolation check (no writes to other tables) |

Nothing else. No change to sales, dispatching, deliveries, bills, GRN,
payments, stock, notifications, backups.  The Activity-Log capture list is a
fixed 18-model registry — Tasker models are not in it, so Tasker rows never
appear in the app-wide logs either.

---

## 7. Permissions (as-built)

- **Create task / Reset time / Cancel / Reopen** → `admin` role only
  (owner decision).  Enforced inside the Tasker API (403 JSON).
- **Noted / Loaded / Dispatched / Delivered** → any signed-in user; the
  pressing user's login name is stamped (owner decision — no loader
  assignment).
- Every action records `by_user` + timestamp in `tasker_event`.
- Transitions are strictly forward (`NEW→NOTED→LOADED→DISPATCHED→DELIVERED`);
  any other order is refused with 400.

---

## 8. Owner decisions (confirmed 2026-11-22)

1. **Deadline semantics** → **two deadlines**: a note deadline (red + alarm if
   no user marks it Noted in time) AND a delivery deadline (red + alarm if not
   Delivered in time).  Each is reset independently.
2. **Who creates tasks & resets time** → **admin only**.
3. **Loader identity** → **any user** presses the stage buttons; the login
   name is stamped.  No assignment step.
4. **Alarm scope** → **global**: red pulsing badge + looping beep on every
   page of the app (`static/tasker_alarm.js`), until admin resets the missed
   deadline manually.  On the Tasker page itself the alarm additionally shows
   a banner and turns the affected rows red.

**Tests:** `tests/test_tasker.py` — 18 tests (lifecycle, forward-only,
both deadline alarms, admin-only rules, read-only combos, FK isolation,
no-domain-writes, permanent delete, cleanup sweep, auto-clean).
Full suite: 164 passed.

---

## 9. Cleanup & auto-clean (owner request 2026-11-23)

The board had **no way to remove a task** — not even a DELIVERED one.  It now
has a full admin-only cleanup path.  Nothing here weakens the isolation: the
two new tables are `tasker_*` only and have **no foreign keys** at all.

### 9.1 Delete is admin-only — for **every** status

- `POST /tasker/api/tasks/<id>/delete` (admin/root only, else 403) permanently
  removes **any** task: `NEW`, `NOTED`, `LOADED`, `DISPATCHED`, `DELIVERED`,
  `CANCELLED`.  This is the "even completed marked" rule — a plain user can
  never delete a completed task.
- Requires the literal confirmation word `DELETE` in the JSON body; a missing
  or wrong word is a 400, so a stray click cannot destroy a task.
- Items, events and deadline resets go with the task through the model
  cascades.  A snapshot row is written first to `tasker_cleanup_log`.

### 9.2 Cleanup log (permanent proof)

`tasker_cleanup_log` keeps, for every removed task: `task_id` (plain snapshot
int, **no FK**), `task_no`, client name, status, item count, mode
(`MANUAL` / `BULK` / `CLEANUP` / `AUTO`), who deleted it (`auto` for auto-clean),
when, and the reason.  Task numbers are never reused: `next_task_no()` takes
its high-water mark from both live tasks and this log.

### 9.3 Manual cleanup tools (admin "Clean up" button)

1. **Selected tasks** — tick rows in the board (admin only) and delete the
   specific wrong ones, including completed tasks (`POST /api/tasks/bulk_delete`).
2. **Clean by age** — pick `DELIVERED` / `CANCELLED`, "older than N days",
   optional reason.  **Preview** runs a dry run and reports the exact match
   count before anything is removed (`POST /api/cleanup` with `dry_run: true`).
   The real run needs `DELETE` typed in the modal.
3. **Recent cleanups** — the last 12 log rows, shown in the same modal.

### 9.4 Auto-clean (optional, off by default)

- `GET/POST /tasker/api/settings` (admin only) stores the switches in the
  singleton `tasker_setting` row: `auto_clean_enabled`, `auto_clean_days`
  (1–3650, default 30), `auto_clean_include_cancelled` (default on).
- When enabled, the sweep runs lazily from the board poll and the global alarm
  poll (throttled to once per process every 5 minutes), so it also runs while
  nobody has the Tasker page open.  It only ever deletes **closed** tasks past
  retention — an active task can never be auto-deleted.
- Every auto-cleaned task is logged with `mode=AUTO`, `deleted_by='auto'`.

### 9.5 UI

- Admin sees a **Clean up** button next to **New Task**, a trash button on every
  row (any status), row checkboxes + "Delete selected", and a cleanup modal with
  the four blocks above.  Non-admins see none of it and get 403 from every
  cleanup endpoint.
- The board shows a green one-minute notice when auto-clean removed something.
