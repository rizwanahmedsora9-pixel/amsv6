# Tasker — adding a task sometimes made two tasks

**Date:** 2026-11-23 · **Scope:** Tasker create only
(`blueprints/tasker/api.py`, `static/tasker.js`, `templates/tasker.html`,
`tests/test_tasker.py`)

## What was reported

> last PR worked fine for tasker function but when I added a task it made
> double tasks.

## What was checked

- The create route inserts exactly one task per request. Two back-to-back
  POSTs produce two tasks; a single POST produces one (including the
  task-number collision retry added in the previous PR, and past, blank or
  string-quantity inputs).
- The page script binds the Create button once. In a browser-DOM test
  (jsdom, real `tasker.js` and rendered page) one click sends one POST, a
  double click sends one, and one row is rendered.
- A single click could not be made to produce two tasks in these tests. The
  remaining way to get two is the same form being sent twice: a double
  click, or a second Create click after an error that arrived after the task
  had already been saved.

## What changed

- **Request id per form.** Each opening of New Task generates a `request_id`.
  Every send from that form carries it.
- **Server dedupe.** `POST /tasker/api/tasks` looks for a `CREATED` event from
  the last 24 hours with the same `request_id`. If one exists, it returns that
  task (`duplicate: true`) and creates nothing. The id is stored inside the
  existing event text, so there is no schema change.
- Requests without an id behave exactly as before.

## Evidence

- `tests/test_tasker.py`: 23 passed (3 new: same id twice → one task; new id →
  second task; no id → no dedupe).
- Browser-DOM test: first send fails after the server saved, second Create
  click reuses the id, server returns the existing task, one task on the board.
