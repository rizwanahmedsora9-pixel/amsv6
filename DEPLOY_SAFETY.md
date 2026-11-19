# AMS — live-host safety rules (read before every deploy)

This project runs on **PythonAnywhere network storage**. SQLite there locks
badly under load. The production host must never run heavy in-app work.

## NEVER run on the live host

| Command / task | Why |
|---|---|
| `pytest` / `python -m pytest` | ~6 min suite, thousands of DB writes against a throwaway DB — CPU + I/O storm on NFS |
| `python app/full_app_audit.py` (already removed from repo) | Seeds a full dataset through the UI, walks EVERY page, boots a second app instance |
| `python scratch/smoke/run_smoke.py --cycles N` | Two full end-to-end journeys with server restarts — same problem |
| `tools/maintenance.py restore` / `recon-audit --apply` | Rewrites balances; only in a planned maintenance window |
| Browser-driven big imports/exports during business hours | Long transactions hold SQLite's write lock; everyone else 500s |
| Any second copy of the app (`python main.py`, extra WSGI workers) | Two writers on one SQLite file on NFS = `database is locked` |

Tests belong on a **developer PC or CI** with a throwaway SQLite file. The
deployed host only serves the app and (optionally, off-peak) one cron entry:
`tools/maintenance.py backup-daily`.

## Before every deploy

1. `python main.py` (or your dev runner) locally and smoke it — never on PA.
2. Deploy, then reload the web app; open one page and save one small change.
3. Check `instance/logs/errorlog.txt` and the PA **Web tab → Error log**.

## If "database is locked" appears

Stop adding load. Follow `TROUBLESHOOT_500.md` and your rescue runbook:
backup first (`cp instance/ahmed_cement_v44_fresh.db`), then check Tasks tab
for stray test/backup jobs, then disk quota (`du -sh instance backups`).

## Re-adding test harnesses

`app/full_app_audit.py` and friends are in `.gitignore`. Re-adding them to
the live tree is a deliberate act — don't. Put new harnesses in `scratch/`
and run them against disposable databases only.
