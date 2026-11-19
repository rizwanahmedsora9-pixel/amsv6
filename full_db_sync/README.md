# full_db_sync — AMS full-database SQLite snapshots

Full-data moves in AMS use **SQLite database files** (`.amsdb`) instead of the
legacy ALLEXPORT XLSX workbook.  A snapshot is an exact, verifiable SQLite
copy of a whole AMS database; an import **cleans all data out of the target
first** (the target schema and migration history stay) and then copies the
file's data rows with their original primary keys.

The engine is pure stdlib (`sqlite3` only) — it runs inside the Flask app, in
the CLI, and on any machine that can read a SQLite file.  No pandas, no
openpyxl.

## Quick reference

```bash
# export the live (or any) database into a portable .amsdb snapshot
python -m full_db_sync export --db instance/ahmed_cement_v44_fresh.db

# read-only verification (integrity, row-count parity, FK check)
python -m full_db_sync verify --db AMS_FULL_xxx.amsdb

# FULL SYNC: clean the target first, then load the snapshot (users included)
python -m full_db_sync import \
    --source AMS_FULL_xxx.amsdb \
    --db instance/ahmed_cement_v44_fresh.db \
    --confirm

# clean all data rows out of a database (schema preserved)
python -m full_db_sync clean --db instance/ahmed_cement_v44_fresh.db --confirm

# consistent online backup copy
python -m full_db_sync backup --db instance/ahmed_cement_v44_fresh.db
```

Write commands (`clean`, `import`) require `--confirm`.

## Module layout

| File | Responsibility |
|---|---|
| `engine.py` | `export_snapshot`, `verify_snapshot`, `clean_all_data`, `import_snapshot`, `backup_database` |
| `cli.py` | command-line interface (`export | verify | clean | import | backup`) |
| `__main__.py` | lets the package run via `python -m full_db_sync` |

## Behaviour notes

* **Full replace**: import mode `clean_replace` (default) replaces target data,
  including users. `migration_history` and `schema_version` describe the
  target's physical schema and are never replaced by import. `--keep-users`
  excludes user rows; append keeps existing rows with the same primary keys.
* **Older snapshots**: known missing fields get explicit defaults. When
  `entry_seq` is absent, existing records receive permanent sequence numbers
  in original ID order, matching migration 0016. Append gives numbers only to
  newly inserted records. The JSON report lists these changes. Unsupported
  missing columns or unmapped populated source tables/columns abort the import
  rather than dropping data.
* **Automatic backup**: imports always back the target up first
  (`pre_full_db_import_*.db` next to the target unless `--backup-dir`).
* **Legacy duplicates**: if the source data violates a UNIQUE index the
  current schema enforces (known case: duplicate `entry.auto_bill_no`), the
  engine relaxes exactly that index before loading so no row is lost, and
  reports it (`unique_indexes_relaxed`).  All other indexes stay enforced.
* **Verification**: every import ends with `PRAGMA integrity_check`,
  `PRAGMA foreign_key_check`, and per-table row-count parity vs the source;
  every export ends with a `verify_snapshot` of the produced file.
* **Sidecar gate (2026-09-10)**: importing a **plain `.db`** source (CLI
  flow) requires the `.report.txt` sidecar with `RESULT: PASS` next to the
  file — the one the AMS migration tool writes — so a quarantined
  `*.INCOMPLETE` run cannot be imported by explicit path.  `.amsdb` snapshots
  are self-verifying and exempt; `--allow-no-sidecar` bypasses the gate for
  automation, and the app's upload UI passes `require_sidecar=False` because
  a browser upload is a single file by nature (it keeps its own admin-only +
  verify + backup protections).  The import report records the outcome under
  `"sidecar"`.

## Rehearsed production snapshot: `AMS_FULL_20261116-044312.amsdb`

Use **Import / Export Center → Import Database Snapshot → Full sync** (not
an Excel importer). This snapshot predates `entry_seq` and other newer columns.
The integration test uploads the exact repository file through the normal
admin route to a disposable database bootstrapped with the current schema.
It checks all 1,066 imported data rows across 81 shared tables against their
original values, plus the pre-import backup, SQLite integrity, foreign keys,
and a successful restart. Fourteen source rows are migration records; the
current target's migration records are kept instead.

Before doing this on a live instance, rehearse on staging, save a separate
backup, and pause other writers. Full sync replaces existing users and data;
make sure you can sign in with an administrator from the restored snapshot.

```bash
python -m pytest -q tests/test_full_db_snapshot_import.py
```
