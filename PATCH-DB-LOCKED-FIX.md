# Fix: `POST /add_client` → 500 `database is locked` after 15 s (PythonAnywhere)

## What the error actually is

Your log is unambiguous:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
  ... app/blueprints/masters/add_client.py, line 55, in add_client
        db.session.commit()
"POST /add_client" 500 15151.0ms
```

Two facts in that one line tell the whole story:

1. **It fails at `commit()`**, not at the `INSERT`. The row was inserted
   successfully; SQLite refused to *finish* the transaction.
2. **It took 15,151 ms.** That is exactly the driver's `busy_timeout` of
   15,000 ms set in `create_app()`. The save waited the full 15 seconds for the
   lock and then gave up.

### Why the COMMIT cannot get the lock

`wsgi.py` deliberately forces `SQLITE_JOURNAL_MODE=DELETE` on PythonAnywhere,
because PA's `/home` is a network filesystem and WAL needs shared memory
(`-shm`) that NFS cannot provide. That decision is correct — but the rollback
journal has a consequence the save path never handled:

> In rollback-journal mode a writer needs the **EXCLUSIVE** lock to write, and
> **an open reader blocks it**. Every open read transaction has to finish before
> any COMMIT can complete.

Your app keeps read transactions open for the length of a page render — a large
`/ledger/<id>` or `/grn` page takes seconds. Any save that lands during a render
(any user, any tab) waits out the full 15 s and dies — exactly what you see, over
and over, on the same screen.

Reproduced locally with PA's exact settings (`DELETE` journal, one process):

| condition | before the patch |
|---|---|
| save with no contention | 302 in 11 ms |
| save while one reader holds a lock for 20 s | **500 after 15.0 s** |

`POST /add_client` is the endpoint that *always* commits (every other master
screen you visit less often), which is why it shows up first.

The existing `begin_write_transaction()` helper was written for this class of
problem, but it is wired into **one** route (`add_direct_sale`) — 34 other
`db.session.commit()` calls in the masters blueprint alone go straight to
`commit` with no protection.

## What the fix does

`app/services/db_busy.py` adds one layer, installed in `create_app()` after all
blueprints are registered:

* every **mutating** route (POST/PUT/PATCH/DELETE) is wrapped automatically —
  268 handlers across 400+ endpoint URLs, including the short `add_client`
  aliases, which previously bypassed any per-route guard;
* when SQLAlchemy raises lock contention, the failed transaction is rolled back
  (nothing was committed, so the save is safe to re-run) and the handler is
  retried for a wall-clock budget of **30 s** with growing, jittered pauses;
* if the lock is still held when the budget runs out, the user gets
  *"The database was busy for a moment … nothing was saved … press Save again"*
  (or `503 {busy: true, retryable: true}` for API calls) instead of a 500;
* real database faults (`no such column`, constraint violations, corruption)
  are **not** retried — they still surface as errors;
* imports, backups, restores, wipes, exports and mail are **excluded**: they do
  work outside the transaction, so replaying them would be wrong.

Verified against the same conditions:

| case | before | after |
|---|---|---|
| reader holds the lock 20 s | 500 at 15.0 s | **saved**, 302 at 19.9 s |
| lock never released | 500 at 15.0 s | 302 with "press Save again", 26.0 s, no 500 |
| no contention | 302 at 11 ms | 302 at 7 ms |
| genuine DB error | 500 | 500 (correct — not retried) |

## How to deploy (5 minutes)

Your PA project is `~/app` (the traceback shows `/home/rizwanahmed/app/...`).

1. **Back up first** (always):

       cp ~/app/instance/ahmed_cement_v44_fresh.db \
          ~/app/instance/backup-before-lockfix-$(date +%Y%m%d-%H%M%S).db

2. **Add the new file** `app/services/db_busy.py` (from
   `amsv5-lockfix-patch.zip`) into `~/app/app/services/`.

3. **Edit one file** — `~/app/app/__init__.py`. After the two lines

       from app.hooks import register_hooks

       register_hooks(app)

   insert:

       from app.services.db_busy import install_busy_retry

       install_busy_retry(app)

   (Or unzip the patch zip over your project — it contains only these two files.)

4. **Web tab → Reload**, then confirm in the error log:

       Busy-retry enabled on 268 mutating handler(s) across ... endpoint(s)

5. Try the save that used to fail. It should now either go through or tell you
   to press Save again — never a 500 after 15 s.

### Optional tuning (Web tab → environment variables)

| variable | default | meaning |
|---|---|---|
| `AMS_BUSY_RETRY_BUDGET_SECONDS` | `30` | how long one save may wait for the lock |
| `AMS_BUSY_RETRY=0` | on | disable the layer entirely |
| `AMS_BUSY_RETRY_SKIP` | — | extra endpoint substrings to exclude |

Keep `SQLITE_JOURNAL_MODE=DELETE` on PA. **Do not switch to WAL** on
PythonAnywhere: `/home` is NFS, WAL needs shared memory there, and the app's own
comment in `wsgi.py` records that it fails or corrupts on that host.

## The real cure (do this when you can)

The retry turns a visible 500 into a short wait, but the underlying cause is that
**a page render holds a read lock long enough to block every save**. Two changes
remove the contention instead of absorbing it:

1. **Paginate the big pages.** `FIXES-2026-11-16.md` already flags this:
   `/grn` is 1.05 MB and `/ledger/2` is **13 MB** in a single response, and under
   load they take ~20 s. Those renders are the readers that block your saves.
2. **Close read transactions at the end of a render.** SQLAlchemy 2.x leaves a
   `SELECT` transaction open until the session is removed; adding
   `db.session.remove()` in a `teardown_appcontext` hook releases the read lock
   as soon as the response is built (this is the single biggest structural win,
   and it is safe — the response is already rendered at that point).

Also worth checking, because each of these can hold the lock for 15+ s on PA:

* **A second process on the same file.** If a Bash console, a cron job
  (`tools/maintenance.py`), or a second web app has the DB open, you will see
  this intermittently. Run `ps aux | grep -i python` in a console; exactly one
  web worker is the rule in `DEPLOY_SAFETY.md`.
* **Files > 100 MB** in `~/app/backups/` or `instance/logs/` can make NFS
  locking slow. Prune them.
