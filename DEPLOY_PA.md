# Deploying AMS to PythonAnywhere (free tier)

This checkout is self-contained and PA-ready: `wsgi.py` already forces
`SQLITE_JOURNAL_MODE=DELETE` on PA (its `/home` is a network filesystem) and
disables background schedulers. Do the steps below in order. Nothing here
deletes or rewrites your live database.

## 1. Upload

Upload the app zip (e.g. `AMS_full-app-*.zip`) to your PA account
(Files tab) and unzip it into your project folder, e.g. `~/ams/`.

## 2. Virtualenv + dependencies

In a PA Bash console:

    cd ~/ams
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

## 3. Database file

The live DB is expected at `~/ams/instance/ahmed_cement_v44_fresh.db`
(wsgi.py sets `APP_DB_PATH` to that path). **Copy your existing production
DB into `instance/` yourself** (Files tab upload, or `cp` in a console).
The zip intentionally contains NO database files. If `instance/` is empty
the app boots against a fresh empty DB (`ALLOW_EMPTY_DB=1`) — fine for a
first smoke test, but you must replace it with the real DB for production.

## 4. WSGI configuration

PA Web tab → your site → WSGI configuration file. Point it at this app,
keeping PA's `sys.path` fix (wsgi.py does it itself):

    import sys, os
    project = os.path.expanduser("~/ams")
    if project not in sys.path:
        sys.path.insert(0, project)
    os.environ["PYTHONANYWHERE_DOMAIN"] = os.environ.get("PYTHONANYWHERE_DOMAIN") or "FREE"
    from wsgi import application

(Simply importing `wsgi.application` is enough — wsgi.py already sets the
schema version, DB path, journal mode and background-worker policy.)

## 5. Reload + verify

1. Web tab → **Reload**.
2. Open the site; log in.
3. Watch **Web tab → Error log** and `instance/logs/500-errors.jsonl` for
   the first minutes.
4. Boot log should show migrations 0001–0013 applied (or "already").
   Watch specifically for:
   `Skipping unique client-code index: N duplicated client-code value(s) exist.`
   - If you see it, the live DB has duplicate client codes; the index
     could not be created (the app still works). Rename the duplicate
     codes (never delete), then reload once more.
   - Otherwise the boot creates `uq_client_code` automatically.
5. If you ever import a legacy snapshot whose rows violate a named unique
   index, the import page / JSON report will list `unique_indexes_lost`
   (and the stored `full_db_import_report_*.json`). Resolve the duplicate
   values, then re-request the index or re-import from a cleaned snapshot.

## 6. Rollback

Before deploying, back up the live DB:

    cp instance/ahmed_cement_v44_fresh.db instance/backup-$(date +%Y%m%d-%H%M%S).db

To roll back code: replace the project files with the previous zip and
Reload. The schema changes in this release are purely additive (one
partial unique index, auto-created at boot); no downgrade migration is
required.

## Hard rules (see DEPLOY_SAFETY.md)

- Never run `pytest`, smoke journeys or audit harnesses on PA.
- Keep exactly ONE web worker; never start a second copy of the app.
- Run full DB imports from Settings during quiet hours only.
