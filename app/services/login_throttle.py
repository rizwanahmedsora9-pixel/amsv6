"""In-process login throttle (AUDIT-002 / R5).

Design
------
The deployed site runs on the PythonAnywhere free tier: one worker, threads.
A process-local sliding-window counter is therefore sufficient — no Redis,
no new infrastructure, no behaviour the shop floor can feel until someone is
actually being brute-forced.

Policy: ``MAX_FAILS`` failed attempts for a (ip, username) key inside
``WINDOW_SECONDS`` locks that key for ``LOCK_SECONDS``.  The key includes the
username so a colleague fat-fingering a password does not lock out unrelated
accounts from the same office IP; the shared admin account still becomes
very hard to grind (5 tries per 15 minutes).

A restart clears the counters — acceptable at this scale and documented.
Set ``AMS_LOGIN_THROTTLE=0`` to disable (break-glass, not recommended).
"""
from __future__ import annotations

import os
import threading
import time

MAX_FAILS = 5
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60
MAX_KEYS = 5000

_LOCK = threading.Lock()
_ATTEMPTS: dict[str, list[float]] = {}
_LOCKS: dict[str, float] = {}


def _enabled() -> bool:
    return (os.environ.get("AMS_LOGIN_THROTTLE") or "1").strip().lower() not in ("0", "false", "no", "off")


def _key(ip: str, username: str) -> str:
    return f"{(ip or '').strip()}|{(username or '').strip().lower()}"


def _prune(now: float) -> None:
    stale = [k for k, stamps in _ATTEMPTS.items() if not stamps or now - stamps[-1] > WINDOW_SECONDS]
    for k in stale:
        _ATTEMPTS.pop(k, None)
    for k, until in list(_LOCKS.items()):
        if until <= now:
            _LOCKS.pop(k, None)
    if len(_ATTEMPTS) > MAX_KEYS:
        for k, _ in sorted(_ATTEMPTS.items(), key=lambda kv: kv[1][0])[:len(_ATTEMPTS) - MAX_KEYS]:
            _ATTEMPTS.pop(k, None)


def locked_seconds(ip: str, username: str) -> int:
    if not _enabled():
        return 0
    now = time.time()
    with _LOCK:
        until = _LOCKS.get(_key(ip, username))
        if until and until > now:
            return int(until - now) + 1
        return 0


def record_failure(ip: str, username: str) -> int:
    if not _enabled():
        return 0
    now = time.time()
    with _LOCK:
        _prune(now)
        key = _key(ip, username)
        stamps = [t for t in _ATTEMPTS.get(key, []) if now - t <= WINDOW_SECONDS]
        stamps.append(now)
        _ATTEMPTS[key] = stamps
        if len(stamps) >= MAX_FAILS:
            _LOCKS[key] = now + LOCK_SECONDS
            return LOCK_SECONDS
        return 0


def record_success(ip: str, username: str) -> None:
    with _LOCK:
        key = _key(ip, username)
        _ATTEMPTS.pop(key, None)
        _LOCKS.pop(key, None)


def reset_for_tests() -> None:
    with _LOCK:
        _ATTEMPTS.clear()
        _LOCKS.clear()
