# Tasker — "no option to clean or auto-clean, admin-only delete even for completed"

**Date:** 2026-11-23 · **Scope:** Tasker isolated tracking board only
(`blueprints/tasker/`, `models/tasker.py`, `static/tasker.js`,
`templates/tasker.html`, `tests/test_tasker.py`)

## What was reported

> last PR about tasker is good, but there is no option to clean or auto clean if
> anything goes wrong — only admin access for deleting, even completed marked.

Two asks:

1. a way to **clean** tasks that were entered wrong, and an **auto-clean** so
   finished work does not pile up forever;
2. deletion must stay **admin-only**, including for tasks already marked
   `DELIVERED` (completed) — a normal user must never be able to remove one.

## What existed

The board had create, the four stage buttons, cancel, reopen and deadline
reset.  There was **no delete of any kind** — not for a wrong task, not for a
completed one.  The only way to make a task disappear was to let the list grow.

## What changed

### Admin-only permanent delete — every status

- `POST /tasker/api/tasks/<id>/delete` → admin/root only (403 otherwise).
  Works for `NEW`, `NOTED`, `LOADED`, `DISPATCHED`, `DELIVERED`, `CANCELLED`.
- Requires the literal word `DELETE` in the body; otherwise 400.  The UI uses a
  typed confirmation box, so one stray click cannot destroy a task.
- `POST /tasker/api/tasks/bulk_delete` deletes the ticked rows in one call
  (admin only, same confirmation, max 200 at a time).

### Permanent cleanup log

New table `tasker_cleanup_log` records every removal before the task is
deleted: task number, client, status, item count, mode
(`MANUAL`/`BULK`/`CLEANUP`/`AUTO`), who removed it (`auto` for auto-clean),
when, and the reason.  `task_id` is a plain snapshot integer — **no foreign
key**, so Tasker's isolation guarantee is untouched.  Task numbers are never
reused after a delete because `next_task_no()` now reads the log too.

### Manual cleanup ("Clean up" button)

- **Selected tasks** — tick rows (admin only) and delete the specific wrong
  ones, including completed tasks.
- **Clean by age** — `DELIVERED` and/or `CANCELLED`, older than N days, with an
  optional reason.  **Preview** is a server-side dry run that returns the exact
  match count; the real run requires typing `DELETE`.
- **Recent cleanups** — the last 12 log rows in the same modal.

### Auto-clean (optional, off by default)

`GET/POST /tasker/api/settings` (admin only) stores
`auto_clean_enabled`, `auto_clean_days` (1–3650, default 30) and
`auto_clean_include_cancelled` (default on) in the singleton `tasker_setting`
row.  When enabled, a sweep runs lazily from the board poll and the global
alarm poll (throttled to once per process every 5 minutes), so it also runs
when nobody has the Tasker page open.  It only ever deletes **closed** tasks
past retention — an active task can never be auto-deleted.

### UI

Admin gets a **Clean up** button next to **New Task**, a trash icon on every
row (any status), row checkboxes + "Delete selected", and the cleanup modal.
Non-admins see none of it; every cleanup endpoint returns 403.

## Evidence

`tests/test_tasker.py` grew from 11 to **18 tests**:

- admin can delete a `DELIVERED` task; a plain user gets 403;
- missing confirmation word is refused (400);
- task number is never reused after a delete;
- bulk delete and cleanup are admin-only;
- manual cleanup dry-run reports 2 and changes nothing, then deletes exactly the
  2 old closed tasks and leaves the active one;
- auto-clean removes only old closed tasks, logs `mode=AUTO` / `deleted_by=auto`;
- auto-clean can exclude `CANCELLED`;
- settings validation (0 and 4000 days rejected) and cleanup history.

Full suite: **164 passed**.

Manual smoke test on a fresh database through the real HTTP routes with CSRF:
create → note → load → dispatch → deliver → delete a `DELIVERED` task (200,
log `MANUAL`), then enable auto-clean, backdate a delivered task 40 days and
call the board → `cleanup.deleted = 1`, log `AUTO`, task gone.
