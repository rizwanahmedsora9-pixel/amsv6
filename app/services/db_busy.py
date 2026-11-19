"""Bounded retry for SQLite write contention on shared/NFS hosts (PythonAnywhere).

Why this file exists
--------------------
On a host whose storage is a network filesystem (PythonAnywhere's ``/home``),
SQLite cannot use WAL, so ``wsgi.py`` forces the rollback journal (``DELETE``).
In that journal mode:

* an **open reader** blocks the writer's ``COMMIT`` (SQLite must take
  ``EXCLUSIVE`` to write the rollback journal), and
* only one writer may hold the lock at a time.

A page that renders a large report keeps its read transaction open for the whole
render.  A save that happens during that render waits ``busy_timeout`` (15 s in
``create_app``) and then fails with::

    sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
      File ".../app/blueprints/masters/add_client.py", line 55, in add_client
        db.session.commit()

i.e. a 500 on a perfectly valid save, after the user waited 15 seconds.

What this module does
---------------------
The failed transaction was rolled back completely (nothing was committed), so
re-running the handler is safe.  ``busy_retry`` re-runs a mutating view while
the database is busy, up to a wall-clock budget, and -- when the budget runs out
-- shows the shop-floor message ("nothing was saved, press Save again") instead
of an opaque 500.

``install_busy_retry(app)`` applies it to every mutating route automatically,
skipping the long-running operations where re-running a handler could repeat a
non-database side effect (imports, backups, wipes, mail, exports).

Knobs (environment)
-------------------
``AMS_BUSY_RETRY=0``                     disable the whole layer
``AMS_BUSY_RETRY_BUDGET_SECONDS=30``     wall-clock budget per request
``AMS_BUSY_RETRY_SKIP=import,backup``    extra endpoint substrings to skip
"""
from __future__ import annotations

import functools
import logging
import os
import random
import time

from flask import jsonify, redirect, request, flash

LOG = logging.getLogger("db-busy")

# Every way SQLite/pysqlite/SQLAlchemy spells "the lock was not available".
_BUSY_SIGNATURES = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
    "sqlite_busy",
)

#: Mutating handlers are re-run only when the lock -- not a data problem -- was
#: the cause, so these are the methods worth wrapping.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Endpoint substrings that must never be replayed: they perform work outside
#: the rolled-back transaction (files, e-mail, staged restores).
DEFAULT_SKIP_TOKENS = (
    "import", "backup", "restore", "wipe", "purge", "reset", "migrate",
    "email", "send", "upload", "export", "download", "print", "seed",
    "rebuild", "vacuum", "selfcheck", "self_check", "diagnostic",
)

#: An attempt can block for the driver's busy_timeout before it gives up, so a
#: retry is only worth starting when at least this much budget remains.
MIN_ATTEMPT_SECONDS = 10.0

BUSY_USER_MESSAGE = (
    "The database was busy for a moment (another save, report or backup was "
    "running), so nothing was saved. Your entry is still in the form — press "
    "Save again."
)


def _env_flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name) or default).strip().lower() not in ("0", "false", "no", "off")


def _budget_seconds() -> float:
    try:
        return max(2.0, float(os.environ.get("AMS_BUSY_RETRY_BUDGET_SECONDS") or 30.0))
    except (TypeError, ValueError):
        return 30.0


def _skip_tokens() -> tuple[str, ...]:
    extra = (os.environ.get("AMS_BUSY_RETRY_SKIP") or "").strip()
    tokens = list(DEFAULT_SKIP_TOKENS)
    if extra:
        tokens.extend(t.strip().lower() for t in extra.split(",") if t.strip())
    return tuple(tokens)


def _exception_chain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def is_busy_error(exc) -> bool:
    """True when *exc* (or anything it wraps) is SQLite lock contention."""
    # Imported lazily so this module stays importable before the app exists.
    try:
        from app.services.errors import DatabaseContended

        if isinstance(exc, DatabaseContended):
            return True
    except Exception:
        pass
    for item in _exception_chain(exc):
        text = str(item).lower()
        if any(sig in text for sig in _BUSY_SIGNATURES):
            return True
    return False


def _reset_session() -> None:
    """Roll back the failed transaction so its connection releases the lock.

    Deliberately *rollback only* -- never ``session.remove()``.  The scoped
    session also holds ``current_user`` (loaded by flask_login), and removing it
    would leave the retried request with a detached User, which fails with
    ``DetachedInstanceError`` instead of retrying.  A rollback is enough: it
    ends the transaction and returns the connection to the pool unlocked.
    """
    try:
        from models import db

        db.session.rollback()
    except Exception:  # pragma: no cover - never mask the original error
        LOG.debug("Could not roll back after a busy error", exc_info=True)


def _busy_response():
    """What the user sees when the retry budget is exhausted."""
    wants_json = (
        str(request.path or "").startswith("/api/")
        or (request.headers.get("X-Requested-With") or "").lower() == "fetch"
        or (request.headers.get("Accept") or "").lower().find("application/json") >= 0
    )
    if wants_json:
        response = jsonify({"ok": False, "error": BUSY_USER_MESSAGE,
                            "retryable": True, "busy": True})
        response.status_code = 503
        response.headers["Retry-After"] = "3"
        return response
    try:
        flash(BUSY_USER_MESSAGE, "danger")
    except Exception:  # pragma: no cover
        pass
    target = request.referrer or "/"
    return redirect(target)


def _delays(count: int, rand: random.Random) -> float:
    """0.4 s, 0.8 s, 1.5 s, 2.5 s, 4 s ... with jitter (thundering herd)."""
    base = min(6.0, 0.4 * (1.9 ** count))
    return base * (0.75 + rand.random() * 0.5)


def busy_retry(view, *, budget: float | None = None, attempts: int = 6):
    """Re-run a mutating view while SQLite reports lock contention."""
    if getattr(view, "_ams_busy_retry", False):
        return view

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not _env_flag("AMS_BUSY_RETRY", "1"):
            return view(*args, **kwargs)
        total = _budget_seconds() if budget is None else float(budget)
        deadline = time.monotonic() + total
        rng = random.Random()
        tries = 0
        while True:
            try:
                return view(*args, **kwargs)
            except Exception as exc:
                if not is_busy_error(exc):
                    raise
                _reset_session()
                tries += 1
                waited = total - max(0.0, deadline - time.monotonic())
                if tries >= attempts or time.monotonic() >= deadline:
                    LOG.warning(
                        "Database stayed busy for %.1f s over %d attempt(s); "
                        "returning a retry message instead of a 500 "
                        "(endpoint=%s method=%s)",
                        waited, tries, request.endpoint, request.method,
                    )
                    return _busy_response()
                # Each attempt can block for the driver's full busy_timeout
                # (15 s) before it reports contention, so never start one that
                # would run past the budget -- the user would rather be told
                # "press Save again" than wait twice over.
                if time.monotonic() + MIN_ATTEMPT_SECONDS > deadline:
                    LOG.warning(
                        "Database still busy with only %.1f s of the %.0f s budget "
                        "left; not starting another attempt (endpoint=%s)",
                        max(0.0, deadline - time.monotonic()), total, request.endpoint,
                    )
                    return _busy_response()
                pause = _delays(tries, rng)
                LOG.info(
                    "Database busy on %s %s (attempt %d, %.1f s waited); retrying in %.1f s",
                    request.method, request.endpoint, tries, waited, pause,
                )
                time.sleep(pause)

    wrapper._ams_busy_retry = True
    return wrapper


def install_busy_retry(app) -> int:
    """Wrap every mutating route with :func:`busy_retry`. Returns how many."""
    if not _env_flag("AMS_BUSY_RETRY", "1"):
        LOG.info("AMS_BUSY_RETRY=0 — busy-retry layer disabled")
        return 0
    if getattr(app, "_ams_busy_retry_installed", False):
        return 0
    app._ams_busy_retry_installed = True

    skip = _skip_tokens()
    wrapped_views: set[int] = set()      # id() of view functions already wrapped
    wrapped_endpoints: set[str] = set()  # endpoint names already covered

    def _should_wrap(endpoint: str) -> bool:
        if not endpoint or endpoint == "static" or endpoint.startswith("static."):
            return False
        lowered = endpoint.lower()
        return not any(token in lowered for token in skip)

    # Pass 1: every endpoint that accepts a mutating method.
    for rule in app.url_map.iter_rules():
        if not ((rule.methods or set()) & MUTATING_METHODS):
            continue
        endpoint = rule.endpoint or ""
        if not _should_wrap(endpoint) or endpoint in wrapped_endpoints:
            continue
        view = app.view_functions.get(endpoint)
        if view is None:
            continue
        app.view_functions[endpoint] = busy_retry(view)
        wrapped_views.add(id(view))
        wrapped_endpoints.add(endpoint)

    # Pass 2: aliases.  ``_alias_unprefixed_endpoints`` maps extra URLs onto the
    # *same* view function (e.g. 'clients' beside 'masters.clients'); those kept
    # the unwrapped original, so a save posted to the short URL bypassed the
    # retry entirely -- which is exactly how /add_client behaved.
    for endpoint, view in list(app.view_functions.items()):
        if endpoint in wrapped_endpoints or not _should_wrap(endpoint):
            continue
        if id(view) in wrapped_views:
            app.view_functions[endpoint] = busy_retry(view)
            wrapped_endpoints.add(endpoint)

    # Pass 3: safety net -- any remaining mutating route whose source commits.
    import inspect

    for rule in app.url_map.iter_rules():
        if not ((rule.methods or set()) & MUTATING_METHODS):
            continue
        endpoint = rule.endpoint or ""
        if endpoint in wrapped_endpoints or not _should_wrap(endpoint):
            continue
        view = app.view_functions.get(endpoint)
        if view is None or getattr(view, "_ams_busy_retry", False):
            continue
        try:
            source = inspect.getsource(inspect.unwrap(view))
        except (OSError, TypeError):
            continue
        if "commit()" in source:
            app.view_functions[endpoint] = busy_retry(view)
            wrapped_endpoints.add(endpoint)
            wrapped_views.add(id(view))

    count = len(wrapped_views)
    aliases = len(wrapped_endpoints) - count

    LOG.info(
        "Busy-retry enabled on %d mutating handler(s) across %d endpoint(s) "
        "(+%d alias URL(s)); budget %.0f s (rollback journal: a save waits for "
        "long readers instead of failing)",
        count, len(wrapped_endpoints), aliases, _budget_seconds(),
    )
    return count
