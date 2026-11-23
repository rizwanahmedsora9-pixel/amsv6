# Tasker — no alarm sound on the note or delivery deadline

**Date:** 2026-11-23 · **Scope:** Tasker alarm only
(`static/tasker_siren.js`, `static/tasker_alarm.js`, `static/tasker.js`,
`templates/tasker.html`, `templates/layout.html`, `blueprints/tasker/api.py`)

## What was reported

> I can confirm no alarm sound on the noted ultimatum and no [alarm] on order
> time. It must play a police siren on any deadline not fulfilled and a red
> flash message too.

## Why it was silent

The old alarm was a short 880 Hz square-wave **beep**, played from a Web Audio
context created on page load.  Two things made it effectively inaudible:

1. **Browser autoplay policy** — Chrome/Edge/Safari create the `AudioContext`
   in the `suspended` state and refuse to start it until the user has clicked
   or pressed a key on that page.  The old code called `resume()` but never
   retried from a guaranteed gesture, so the beep never played.
2. **No visible fallback** — the only cue was a small top-right badge and the
   red rows.  There was no unmistakable red flashing message, and no button to
   unlock audio.

## What changed

### A real police siren

- New shared `static/tasker_siren.js` exposes `window.AMSTaskerSiren`.
- Two sawtooth oscillators (720 Hz / 1080 Hz) swept by a 0.65 Hz LFO, through a
  low-pass filter and a 0.22 master gain — a continuous wailing siren, not a
  beep.
- `start()` is idempotent and retries while the browser context is suspended,
  so it begins the moment audio is allowed.

### Red flashing message on every page

- `static/tasker_alarm.js` now creates a fixed **red flashing banner** at the
  top of every AMS page when any deadline is missed:
  `⚠ TASKER ALARM — 2 deadline(s) missed — TK-0001 (NOTE+DELIVERY)` plus the
  instruction that only an admin can reset it.
- Polls `/tasker/api/alarm_state` every **5 s** (was 20 s).
- The banner has an **Enable siren** button.  Any click or keypress anywhere
  also unlocks Web Audio; the siren then starts automatically.
- On the Tasker board itself, the existing `tkAlarmBar` now uses the same
  shared siren, flashes red (`tk-alarm-flash`), lists the overdue task numbers
  and deadline kinds, and has its own **Enable siren** button.
- The admin header has a **Test siren** button that unlocks audio and plays a
  2.5-second burst, so the sound can be checked before a real deadline is
  missed.

### Immediate trigger on the board

- Countdown spans carry `data-live-alarm="1"` while their deadline is still
  actionable (note only while `NEW`, delivery while not `DELIVERED`/`CANCELLED`).
- `tick()` starts the siren the moment a live deadline passes, without waiting
  for the next board poll.
- `api_board` now returns `overdue_tasks` (`task_no`, `client_name`, `kinds`)
  so the alarm bar can list every missed deadline even when a filter is active.

## Browser limitation (not a bug)

No browser lets a page play sound before the first user gesture.  The red
flashing message is therefore guaranteed, and the **Enable siren** button (or
any click/key) is the one-time unlock.  After that the siren plays automatically
on every missed deadline, on every page.

## Evidence

- `tests/test_tasker.py` asserts `overdue_tasks` with the correct `kinds`
  (`NOTE`, `DELIVERY`) on the board API.
- Tasker suite: **18 passed**; full suite: **164 passed**.
- Manual smoke test: created a task with a note and delivery deadline one hour
  in the past → board `counters.overdue = 1`,
  `overdue_tasks = [{task_no: TK-0001, kinds: [NOTE, DELIVERY]}]`,
  `alarm_state.overdue_count = 2` (one per missed deadline).
