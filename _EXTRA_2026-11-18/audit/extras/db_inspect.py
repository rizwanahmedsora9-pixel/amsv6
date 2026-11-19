#!/usr/bin/env python3
"""AMS database inspector — read-only audit of the live SQLite runtime.

Reports, for the database the app would actually open:
  * resolved path, file size, sidecar (-wal/-shm/-journal) state
  * PRAGMA journal_mode / synchronous / busy_timeout / foreign_keys
  * integrity_check, quick_check, foreign_key_check
  * schema_version + migration_history (which NNNN_*.sql ran)
  * table count / row counts / freelist (space that VACUUM would reclaim)

Usage:
    python3 db_inspect.py                # inspect without booting Flask
    python3 db_inspect.py --boot         # boot the app factory first (creates DB)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

APP_ROOT = Path("/home/user/AMSV5-2")
sys.path.insert(0, str(APP_ROOT))

DB = Path(os.environ.get("APP_DB_PATH") or APP_ROOT / "instance" / "ahmed_cement_v44_fresh.db")


def pragma(con, name):
    try:
        return con.execute(f"PRAGMA {name}").fetchone()[0]
    except Exception as exc:  # pragma: no cover
        return f"ERR:{exc}"


def main() -> int:
    global DB
    boot = "--boot" in sys.argv
    out = {"app_root": str(APP_ROOT), "db_path": str(DB)}

    if boot:
        os.environ.pop("AMS_ENABLE_BACKGROUND_WORKERS", None)
        os.environ.setdefault("AMS_SCHEMA_VERSION", "v44")
        from app import create_app

        app = create_app()
        out["flask"] = {
            "db_uri": app.config["SQLALCHEMY_DATABASE_URI"],
            "journal_mode_config": app.config["SQLITE_JOURNAL_MODE"],
            "migration_report": app.config.get("AMS_MIGRATION_REPORT"),
            "bootstrap_error": (app.config.get("AMS_BOOTSTRAP_ERROR") or None),
            "health_warnings": app.config.get("DB_HEALTH_WARNINGS", []),
            "engine_options": {k: v for k, v in app.config["SQLALCHEMY_ENGINE_OPTIONS"].items()
                               if k != "connect_args"}
            | {"connect_args": app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"]},
        }
        DB = Path(app.config["APP_DB_PATH"])
        out["db_path"] = str(DB)

    if not DB.exists():
        out["status"] = "DB FILE DOES NOT EXIST (app would create it on first boot)"
        print(json.dumps(out, indent=2, default=str))
        return 0

    st = DB.stat()
    out["file"] = {
        "size_bytes": st.st_size,
        "sidecars": {},
    }
    for sfx in ("-wal", "-shm", "-journal"):
        p = Path(str(DB) + sfx)
        out["file"]["sidecars"][sfx] = p.stat().st_size if p.exists() else None

    con = sqlite3.connect(str(DB), timeout=5)
    try:
        out["pragmas"] = {
            "journal_mode": pragma(con, "journal_mode"),
            "synchronous": pragma(con, "synchronous"),
            "busy_timeout_ms": pragma(con, "busy_timeout"),
            "foreign_keys": pragma(con, "foreign_keys"),
            "user_version": pragma(con, "user_version"),
            "page_size": pragma(con, "page_size"),
            "page_count": pragma(con, "page_count"),
            "freelist_count": pragma(con, "freelist_count"),
            "auto_vacuum": pragma(con, "auto_vacuum"),
            "wal_autocheckpoint": pragma(con, "wal_autocheckpoint"),
            "cache_size": pragma(con, "cache_size"),
        }
        out["integrity"] = [r[0] for r in con.execute("PRAGMA integrity_check")][:5]
        out["quick_check"] = [r[0] for r in con.execute("PRAGMA quick_check")][:5]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        out["foreign_key_violations"] = len(fk)
        out["fk_sample"] = [tuple(r) for r in fk[:5]]

        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out["table_count"] = len(tables)
        idx = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
        out["index_count"] = idx

        def q(sql, default=None):
            try:
                return con.execute(sql).fetchall()
            except sqlite3.Error:
                return default

        out["schema_version"] = q("SELECT * FROM schema_version", "table missing")
        out["migration_history"] = q(
            "SELECT filename, applied_at FROM migration_history ORDER BY filename", "table missing")

        counts = {}
        for t in tables:
            r = q(f'SELECT COUNT(*) FROM "{t}"')
            counts[t] = r[0][0] if isinstance(r, list) and r else None
        out["row_counts"] = counts
        out["total_rows"] = sum(v for v in counts.values() if isinstance(v, int))

        # A few "is the shop actually running" markers
        out["key_counts"] = {k: counts.get(k) for k in
                             ("user", "client", "supplier", "entry", "direct_sale",
                              "booking", "pending_bill", "account", "activity_event")}
    finally:
        con.close()

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
