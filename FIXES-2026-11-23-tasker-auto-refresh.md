# Tasker — auto-refresh every 60 seconds

**Date:** 2026-11-23 · **Scope:** Tasker board page only (`static/tasker.js`,
`templates/tasker.html` cache version)

## What was requested

The Tasker page should reload itself every 60 seconds. Nothing else in the app
changes.

## What changed

- `static/tasker.js` schedules `window.location.reload()` 60 seconds after the
  board starts. The script is only loaded by the Tasker page, so no other page
  is affected.
- If a dialog is open (New Task, reset time, cleanup, delete…), the reload is
  postponed by 5 seconds and checked again. Reloading mid-form would discard
  what the user is typing.
- `tasker.js` cache version bumped to `?v=6` so browsers pick up the change.

## Evidence

- Browser-DOM test: one 60-second timer is scheduled; with a dialog open it
  defers by 5 seconds; after the dialog closes the reload proceeds.
