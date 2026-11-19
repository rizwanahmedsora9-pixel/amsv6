# Run AMS on a local PC (test install)

Fresh, empty database — your real PythonAnywhere data is **not** included and is untouched.

## Requirements
- Python **3.11+** (tested on 3.13). No Node.js needed.
- ~150 MB free disk.

## Linux / macOS
```bash
unzip AMS-full-app-<date>-<sha>.zip -d ams && cd ams
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-lock.txt
python main.py
```

## Windows (Command Prompt)
```bat
expand or right-click > Extract All  AMS-full-app-<date>-<sha>.zip
cd AMS-full-app-<date>-<sha>
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-lock.txt
python main.py
```

Then open **http://127.0.0.1:5000** and sign in with the default admin
**Admin / Admin@fbm12345** — change it immediately (Settings → Users).

## Self-check (optional)
Run `python tests/run_all.py` to re-verify the app after any change (see `tests/README.md`).
It uses throwaway databases in a temp directory and never touches `instance/`.

## Notes
- First boot creates `instance/` (database, secret key, logs) next to the app. Delete that folder to start fresh.
- Optional extras (heavy, not required): `requirements-pdf.txt` (PDF receipts), `requirements-crypto.txt` (encrypted backups), `requirements-data.txt` (pandas exports).
- To test a backup restore: `python tools/restore_drill.py <backup.amsenc>` (needs `requirements-crypto.txt` + the backup passphrase).
- Everything is environment-driven; nothing is sent anywhere. The app makes no outbound network calls except when you configure SMTP for backups/emails.
