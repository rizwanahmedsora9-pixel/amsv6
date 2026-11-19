# AMS v5.2 — Database condition, WAL status & import-time schema migration

**Audit date:** 2026-11-18 · **Scope:** `AMSV5-2/` (552 files) · **Method:** static read of the
code **plus** a live boot of the app factory on this machine (real database created and measured,
lock behaviour reproduced, import paths exercised on throw-away copies).

Everything marked **[measured]** was executed, not inferred.

---

## 1. Full stack actually in use

| Layer | Component | Version |
|---|---|---|
| Runtime | Python | 3.13.18 |
| | SQLite (bundled with CPython) | 3.46.1 |
| Web | Flask / Werkzeug / Jinja2 | 3.1.3 / 3.1.9 / 3.1.6 |
| ORM | SQLAlchemy / Flask-SQLAlchemy | 2.1.8 / 3.1.1 |
| Auth | Flask-Login | 0.6.3 |
| Excel | openpyxl (pandas only for the legacy XLSX path) | 3.1.5 |
| QR | segno | 1.6.6 |
| Optional PDF/Urdu | reportlab, flask-weasyprint, uharfbuzz, arabic-reshaper, python-bidi, fontTools | requirements-pdf.txt |
| Optional crypto/data | cryptography, pandas | requirements-crypto/data.txt |
| Optional tests | pytest, pypdf, pymupdf | requirements-dev.txt |

* **No WSGI server is pinned anywhere** (no gunicorn/waitress in any requirements file). Entry points are
  `main.py` (Werkzeug dev server, `0.0.0.0:$PORT`, debugger off unless `AMS_DEBUG=1`) and `wsgi.py`
  (PythonAnywhere-style host, background workers explicitly disabled).
* Architecture: Flask app factory (`create_app`) + 11 core blueprints + 5 auto-loaded module packs
  (`accounts`, `admin`, `data_lab`, `import_export`, `inventory`) → **270 endpoints**, **147 templates**,
  **86 tables**, **427 indexes**.
* Data store: **one local SQLite file**, `instance/ahmed_cement_v44_fresh.db` (`AMS_SCHEMA_VERSION=v44`;
  the retired `ahmed_cement.db` / `ahmed_cement_v44.db` can never be re-opened by the v44 runtime).
* Auxiliary pure-stdlib engine: `full_db_sync` (export/verify/clean/import of whole-database `.amsdb` snapshots).
* Coordinated cross-process work uses file locks (`app/services/locks.py`, `system_lock` table), not a broker.

---

## 2. Database condition — measured on a freshly bootstrapped v5.2 database

| Property | Value |
|---|---|
| Path | `AMSV5-2/instance/ahmed_cement_v44_fresh.db` |
| Size | 2 220 032 B (2.12 MiB) |
| Tables / indexes | 86 / 427 |
| `PRAGMA integrity_check` | **ok** |
| `PRAGMA quick_check` | **ok** |
| `PRAGMA foreign_key_check` | **0 violations** |
| `journal_mode` (file header) | **wal** |
| page_size / page_count / freelist | 4096 / 542 / 0 |
| `schema_version` (id=1) | **17** |
| `migration_history` | **17 / 17** applied (0001 … 0017), `applied_at` = boot time |
| Rows | 31 total — 1 admin user, 1 client, 1 material_category, 10 terms_and_conditions, 17 migration rows |
| Health snapshot | `instance/health_snapshot.json` — baseline total **2** (user+client), guards against silent data loss on next boot |
| Bootstrap errors | none; migrations `{'applied': 17, 'files': 17, 'version': 17}` |
| Busy-retry layer | enabled on **153 handler functions / 270 endpoints** (+117 alias URLs), budget 30 s |

Verdict on a *clean* database: healthy — integrity clean, no FK damage, no orphan WAL/SHM/journal
sidecars, migration ledger complete and stamped.

The interesting part is not a clean DB, it is what happens to a DB that **arrives from somewhere else**
(import, restore, laptop → server). That is where the two real findings are.

---

## 3. WAL status — how it is decided, and the gap **[measured]**

### 3.1 The design (correct)

`_resolve_sqlite_journal_mode()` (`app/__init__.py:535`) picks the mode:

1. `SQLITE_JOURNAL_MODE` env wins if it is one of `WAL|DELETE|TRUNCATE|PERSIST|MEMORY|OFF`;
2. otherwise **`DELETE`** if the host looks like PythonAnywhere (`PYTHONANYWHERE_DOMAIN/SITE`) **or**
   `_on_network_filesystem(db_path)` matches `/proc/mounts` against NFS/CIFS/9p/sshfs/…;
3. otherwise **`WAL`**.

`wsgi.py:40` pre-forces `SQLITE_JOURNAL_MODE=DELETE` on PythonAnywhere, and `BUILD_INFO.txt` /
`PATCH-DB-LOCKED-FIX.md` restate the rule: *keep DELETE on PA, never enable WAL on NFS* (WAL needs POSIX
shared memory for the `-shm` file).

### 3.2 The gap

The resolved mode is **only ever executed while the app is creating a brand-new empty file** —
`app/services/instance_files.py:97` `_create_empty_sqlite()` runs `PRAGMA journal_mode=<mode>` once and
never again. For an **existing** database there is no `PRAGMA journal_mode` anywhere: the single
`connect` listener (`app/__init__.py:231-243`) sets only `foreign_keys=ON` and `busy_timeout=15000`.

Because `journal_mode` is **persistent in the file header** (unlike `busy_timeout`/`foreign_keys`,
which are per connection), the mode travels with the file — and the app happily runs whatever the file
says, whatever the config says.

**Proof [measured]:** a WAL database, booted with `SQLITE_JOURNAL_MODE=DELETE`:

```
config says    : DELETE
after boot     : wal        <-- file unchanged
app connection : wal | busy_timeout: 15000 | foreign_keys: 1
```

This bites in both directions:

| Direction | What happens | Severity |
|---|---|---|
| Created on a laptop/local disk (`wal`) → restored/imported onto PythonAnywhere (NFS) | The file stays WAL; `-shm` cannot be created on NFS → `unable to open database file` / `disk I/O error` **on every request**. The `SQLITE_JOURNAL_MODE=DELETE` override and the whole 30 s busy-retry layer are inert because this is not a lock error. | **High** — permanent 500s |
| Created under DELETE (PA export) → moved to a local-disk host | File stays DELETE while config/logs say WAL. Readers block a save's COMMIT — exactly the pathology `db_busy.py` was written to paper over. It survives only because of the 15 s `busy_timeout` + 30 s retry budget, i.e. slower saves under load. | **Medium** — silent perf/contention |

### 3.3 Fix (small, low-risk)

Add the mode to the existing connect listener, or to a dedicated boot step:

```python
# app/__init__.py, inside the sqlite connect listener (after foreign_keys/busy_timeout)
want = str(app.config.get("SQLITE_JOURNAL_MODE") or "").upper()
if want:
    have = cursor.execute("PRAGMA journal_mode").fetchone()[0]
    if str(have).upper() != want:
        got = cursor.execute(f"PRAGMA journal_mode={want}").fetchone()
        if not got or str(got[0]).upper() != want:
            logging.getLogger(__name__).error(
                "Could not switch journal_mode %s -> %s (got %s); the file header wins.",
                have, want, got)
```

Notes: the switch must happen **once at boot with no other connections open** (a `journal_mode` change
needs an exclusive lock), so the cleanest place is a boot step right after
`ensure_instance_runtime()` — open a raw `sqlite3` connection, read the mode, switch if different,
log success/failure. Run it every boot; it is a no-op after the first. Also worth surfacing the result
on the DB status page so "WAL is on/off" is a visible fact rather than a log line.

---

## 4. "Database is busy / locked" protection — what is really installed

Four independent layers, all keyed off lock *contention* only (never retried for corruption, disk-full,
read-only):

1. **Connection level** — `busy_timeout=15000` + `foreign_keys=ON` per connection; engine
   `timeout=30`, `pool_pre_ping`, `pool_recycle=1800 s`, `pool_timeout=30` (`app/__init__.py:130-160`).
2. **Per-view retry** — `install_busy_retry()` (`app/services/db_busy.py`) wraps every mutating route:
   6 attempts, 30 s wall-clock budget, exponential jittered backoff, skips
   import/backup/restore/wipe/purge/migrate/mail/export/…; on exhaustion the user gets
   *"nothing was saved, press Save again"* (503 + `Retry-After: 3` for XHR, flash+redirect for forms).
   **[measured]** installs on 153 view functions / 270 endpoints / +117 aliases.
3. **WSGI middleware** — `DatabaseBusyRetryMiddleware` (`app/services/errors.py:528`) re-runs an
   idempotent GET/HEAD/OPTIONS once (0.25 s, 1.0 s) when the handler flagged the failure as transient.
4. **Explicit write transaction** — `begin_write_transaction()` (`app/services/errors.py:145`) for the
   races where `BEGIN IMMEDIATE` fails *without* consulting `busy_timeout`; 8 s budget
   (`AMS_WRITE_LOCK_BUDGET_SECONDS`), currently used by direct-sale save.

Knobs: `AMS_BUSY_RETRY=0`, `AMS_BUSY_RETRY_BUDGET_SECONDS`, `AMS_BUSY_RETRY_SKIP`, `AMS_DB_RETRY`,
`AMS_WRITE_LOCK_BUDGET_SECONDS`.

Background/report writers deliberately use short timeouts so a page is never delayed:
`activity_capture` 1500 ms / 250 ms, `presence` 250 ms; maintenance/wipe/auto-migrate use 120 s
(off the request path).

**Reproduced on a DELETE-mode database [measured]** — a reader holding an open read transaction for 8 s:

```
write w/ 1.5 s busy_timeout while reader holds : FAILED after 1.5s -> database is locked
write w/ 15 s  busy_timeout while reader holds : OK after 6.3s
```

So the 15 s connection timeout genuinely converts the old instant 500 into a wait, and the 30 s retry
budget covers renders longer than that. This part of the v5.2 patch is sound — but only reachable in
rollback-journal mode. On a WAL database (local disk) readers do not block writers at all, which is why
§3.3 matters: getting the mode *right* is cheaper than retrying.

---

## 5. Schema migration when a database "comes by import"

There are three different import paths, with three different schema behaviours.

### 5.1 Path A — file appears in `instance/` (drop-in / restore / rsync) → **fully self-healing** ✅

Boot order (`app/__init__.py:320-425`):

1. `ensure_instance_runtime()` — create the file if missing, drop orphan `-wal/-shm/-journal` only when
   the main file is gone, never touch a live file.
2. `_guard_db_file_before_bootstrap()` (`health.py:213`) — size floor + `PRAGMA quick_check`; corrupt
   file fails fast with an actionable `.recover` hint instead of an opaque error later.
3. Optional v4.4 SQL bundle (skipped silently if absent — it is not shipped).
4. `_bootstrap_database()` (`schema.py:1264`) = `db.create_all()` + hand-written `_ensure_*` column
   repair + loan-party two-way schema.
5. **Versioned migrations**: every `app/migrations/NNNN_*.sql` not present in `migration_history` is
   applied, each recorded, then `schema_version.version` is stamped to the highest applied prefix
   (`auto_migrate.py:183`). 17 files ship today. A failure here is **fatal by design** — the app refuses
   to serve against a half-migrated schema.
6. Post-bootstrap data-loss guard (`health.py:306`) compares row counts against `health_snapshot.json`
   and **refuses to start** on a suspicious drop unless `ALLOW_DB_DROP=1`.

**[measured]** I built an "older" database — `entry_seq` dropped from 8 tables, `activity_event` /
`activity_session` dropped, 0016/0017 removed from history, `schema_version` forced to 15 — then booted:

```
migration report : {'applied': 2, 'skipped': 15, 'files': 17, 'version': 17}
entry_seq restored on direct_sale : True
activity_event recreated: True | activity_session: True
schema_version 15 -> 17 | integrity: ok | bootstrap error: none
```

So: **an imported older database is upgraded automatically on the next start.** Destructive statements in
migration files are blocked unless `MIGRATIONS_ALLOW_DESTRUCTIVE=1`.

Also **[measured]**: because the health guard records the DB *path*, booting against a differently named
file while the old one still exists **refuses to start** —

> `DB health check failed: DB path changed from '…ahmed_cement_v44_fresh.db' to '…target_copy.db'. Refusing to start to prevent data loss. Set ALLOW_DB_DROP=1 to override.`

Correct as a safety net, but it means a manual file-swap import needs `instance/health_snapshot.json`
deleted (or `ALLOW_DB_DROP=1`) before the next boot. The in-app import path handles this automatically
(`rebaseline_after_full_import`); the manual path does not.

### 5.2 Path B — upload via `/import_export/full_db_import` (`.amsdb` / `.db`) → **row copy, schema is *not* migrated** ⚠️

`full_db_sync.engine.import_snapshot()` (`engine.py:502`):

* **Target schema is authoritative.** Tables only in the target stay empty; tables only in the source are
  skipped; if the target needs a column the source lacks, the import is **refused** with a precise
  message. **[measured]**
  `Table 'pending_bill' in the target needs columns the source lacks: ['entry_seq'] … start the target app once so its schema bootstrap adds the columns.`
* `clean_replace` deletes every data row in **one `BEGIN IMMEDIATE` transaction** together with the load —
  all-or-nothing, no wiped-but-not-loaded state; automatic `pre_full_db_import_*.db` backup first
  (retention 3); then `integrity_check` + `foreign_key_check` + per-table count parity; UNIQUE indexes
  violated by legacy source data are dropped, and reported if they cannot be recreated.
* **No migration is run after the import.** The route only re-baselines the health snapshot
  (`_pages_full_db.py:330`). Consequence: a snapshot from an older build must be loaded into a target
  that has already booted the current schema — i.e. you must start the app once, then import. (This is
  the documented workaround, and it is safe, just not automatic.)

**Bookkeeping travels with the snapshot [measured].** `migration_history` and `schema_version` are
ordinary user tables, so `clean_replace` wipes them and then copies whatever the *source* carried:

```
source migration_history rows : 0   ->  target after import: 0   (schema_version 0)
next boot                     :  {'applied': 17, 'skipped': 0, 'files': 17, 'version': 17}
```

Harmless here (all 17 migrations are written to be re-runnable and the runner tolerates
"already exists"/"duplicate column"), but the risk is asymmetric: if a snapshot comes from a **newer**
build that already recorded e.g. `0018`–`0020`, this older code will read those filenames as applied and
**never run them** — and only column-adding migrations are caught by the column guard; table/index-only
migrations are not.

Recommended hardening (small):
1. exclude `migration_history` and `schema_version` from the clean+copy (add them to `NON_DATA_TABLES`
   handling) and let the boot re-derive them, **or** re-run `run_sql_migrations()` + `_stamp_version()`
   at the end of the import route;
2. keep the current column-guard message as-is — it is a good one.

### 5.3 Path C — legacy XLSX (`/import_export/full_raw_import`) → rows only

pandas-dependent, append/replace-per-tenant, transactional rollback on any failed row; no schema change.
Superseded by Path B and marked as a display format in the docs.

---

## 6. Prioritised actions

| # | Action | Why | Effort |
|---|---|---|---|
| 1 | Apply `PRAGMA journal_mode` to the database **file** at boot (§3.3), not only when creating it; log the outcome and show it on the DB status page | Removes the only failure mode that produces permanent 500s (WAL file on NFS) and the silent "we think it's WAL but it isn't" mode | ~20 lines |
| 2 | Run `run_sql_migrations()` after a successful `full_db_import` (or exclude the two migration tables from the copy) | Migration ledger currently inherits the donor file's claims; a newer snapshot can leave real migrations unapplied | small |
| 3 | Make the file-swap path re-baseline the health snapshot like the UI path does (or document deleting `health_snapshot.json`) | Otherwise a legitimate imported DB refuses to boot | doc/small |
| 4 | Put journal_mode, integrity, FK, migration ledger and pending-migration count on one visible status page (`tools/data_status.py` already has the shape) | Today only a 6-table row-count snapshot exists; WAL status is a log line | medium |
| 5 | Consider `PRAGMA optimize` / `wal_autocheckpoint` tuning and periodic `VACUUM` for long-running shops (freelist is 0 now, but wipe/import cycles fragment) | housekeeping | small |

**Bottom line:** the database layer is in good shape — clean file, complete migration ledger, four
layers of contention handling that genuinely work (reproduced). The one real defect is that the
journal-mode decision never gets applied to an existing file, which is precisely the case that matters
when a database arrives by import, restore or migration.

---

*Measurement tool:* `/home/user/db_inspect.py` (read-only; `python3 db_inspect.py --boot` boots the
factory first). Test databases created during this audit were deleted; only the runtime
`instance/ahmed_cement_v44_fresh.db` + `health_snapshot.json` remain.
