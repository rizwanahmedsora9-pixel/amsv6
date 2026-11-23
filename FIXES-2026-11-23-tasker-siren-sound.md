# Tasker — siren sounds like a Dubai-style police siren

**Date:** 2026-11-23 · **Scope:** Tasker alarm sound only
(`static/tasker_siren.js`, `templates/layout.html` cache version)

## What was requested

The alarm should sound like a proper police siren, like Dubai Police.

## What changed

- Sound is now a brassy two-voice tone (sawtooth horn + detuned square body)
  through a resonant lowpass for a horn-like bite.
- The pitch sweeps linearly between about 560 Hz and 1500 Hz and back, one
  full cycle every 2.2 seconds, using a triangle LFO. The old sine LFO slowed
  down at the top and bottom, which sounded softer.
- Start, stop, unlock and the "Enable siren" flow are unchanged.
- `tasker_siren.js` cache version bumped to `?v=2` in the layout.

## Evidence

- Stub Web Audio test: the siren builds, starts, reports running, and stops
  cleanly. Sweep range and waveforms match the values above.
- Not listened to in a real browser; the exact tone is worth a quick check on
  the Tasker page (use "Test siren" in the admin header).
