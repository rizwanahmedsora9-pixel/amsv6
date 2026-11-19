"""Audit harness — isolated AMS instance + read-only verification helpers.

Safety contract (enforced at import time):
  * the app under test is the isolated COPY in AUDIT/app_under_test,
  * its database is AUDIT/test_db/audit_test.db,
  * if either resolves anywhere near the workspace/production paths the import
    raises instead of continuing.
"""
from __future__ import annotations

import os
import sqlite3
import sys

AUDIT = "/home/user/AUDIT_2026-11-18"
# Overridable so the same harness can drive the pristine audit copy and the
# fixed build without duplicating this file.
APP = os.environ.get("AMS_AUDIT_APP") or f"{AUDIT}/app_under_test"
TEST_DB = os.environ.get("AMS_AUDIT_DB") or f"{AUDIT}/test_db/audit_test.db"
PROD_MARKERS = ("/home/user/AMSV5-2/instance",)

# ---- isolation guard -------------------------------------------------------
for marker in PROD_MARKERS:
    if os.path.abspath(TEST_DB).startswith(marker):
        raise SystemExit(f"REFUSING TO RUN: test DB inside production path {marker}")

os.environ["APP_DB_PATH"] = TEST_DB
os.environ["AMS_SCHEMA_VERSION"] = "v44"
os.environ["ALLOW_EMPTY_DB"] = "1"
os.environ["SQLITE_JOURNAL_MODE"] = "WAL"
os.environ.pop("AMS_ENABLE_BACKGROUND_WORKERS", None)
os.environ["AMS_DAILY_BACKUP_SCHEDULER"] = "0"
os.environ["BACKUP_EMBEDDED_SCHEDULER"] = "0"
os.environ["PUBLIC_BASE_URL"] = "http://localhost"

sys.path.insert(0, APP)

if APP not in sys.path[0]:
    raise SystemExit("REFUSING TO RUN: app_under_test is not first on sys.path")

from app import create_app  # noqa: E402

app = create_app()
app.config["TESTING"] = True
app.config["WTF_CSRF_ENABLED"] = False

RESOLVED_DB = os.path.abspath(app.config["APP_DB_PATH"])
assert RESOLVED_DB == os.path.abspath(TEST_DB), f"ISOLATION BROKEN: {RESOLVED_DB}"
assert RESOLVED_DB.startswith(AUDIT), "ISOLATION BROKEN: DB outside the audit folder"


# ---- read-only database verification --------------------------------------
def ro():
    """Read-only connection to the test database (never writes)."""
    con = sqlite3.connect(f"file:{TEST_DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def q(sql, params=()):
    con = ro()
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def scalar(sql, params=()):
    row = one(sql, params)
    return list(row.values())[0] if row else None


def tables():
    return [r["name"] for r in q(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def columns(table):
    return [r["name"] for r in q(f'PRAGMA table_info("{table}")')]


def dump(table, where="1=1", params=(), limit=20):
    return q(f'SELECT * FROM "{table}" WHERE {where} LIMIT {limit}', params)


def ledger_dump(table):
    return q(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]


def counts():
    """Row count of every table — cheap integrity snapshot."""
    out = {}
    for t in tables():
        try:
            out[t] = scalar(f'SELECT COUNT(*) FROM "{t}"') or 0
        except Exception:
            out[t] = "ERR"
    return out


# ---- HTTP helpers ---------------------------------------------------------
def client():
    c = app.test_client()
    r = c.post("/login", data={"username": "Admin", "password": "Admin@fbm12345"},
               follow_redirects=True)
    assert r.status_code == 200, f"login failed: {r.status_code}"
    return c


def flash_messages(resp):
    """Pull flashed text out of a rendered page (app shows flashes as alerts)."""
    import re
    body = resp.get_data(as_text=True)
    out = re.findall(r'class="alert alert-([a-z]+)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{4,300})', body)
    return [(kind, " ".join(txt.split())) for kind, txt in out][:8]


def post(c, url, data, follow=True):
    """POST and report status + what the user was told."""
    r = c.post(url, data=data, follow_redirects=follow)
    return r


def brief(resp, limit=140):
    kind = "JSON" if resp.is_json else "HTML"
    msgs = flash_messages(resp)
    txt = msgs if msgs else resp.get_data(as_text=True)[:limit]
    return {"status": resp.status_code, "kind": kind, "messages": txt}
