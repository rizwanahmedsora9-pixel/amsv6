#!/usr/bin/env python3
"""AMS encrypted-backup restore drill (AUDIT-111 / R16).

Proves — on *any* machine, without touching production — that an AMSENC1
backup can actually be opened and passes SQLite integrity checks.  This is
the drill that turns "we have backups" into "we have a restore path".

Usage:
    AMS_BACKUP_ENCRYPTION_PASSWORD='your-passphrase' \
        python tools/restore_drill.py /path/to/backup.amsdb.amsenc

    # or pass the passphrase interactively (prompted, no shell history):
    python tools/restore_drill.py /path/to/backup.amsdb.amsenc

Exit code 0 = restored view verified OK.  Non-zero = drill failed.
Nothing is written to the source file; the decrypted copy goes to a temp
path which is shown (kept) only when --keep is passed.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import backup_crypto  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", help="Path to the encrypted backup (.amsenc / AMSENC1 container)")
    parser.add_argument("--keep", action="store_true", help="Keep the decrypted file next to the backup (default: delete after checks)")
    args = parser.parse_args()

    src = Path(args.backup)
    if not src.is_file():
        print(f"FAIL: backup not found: {src}")
        return 2

    passphrase = os.environ.get("AMS_BACKUP_ENCRYPTION_PASSWORD") or getpass.getpass("Backup passphrase: ")
    try:
        plaintext = backup_crypto.decrypt_bytes(src.read_bytes(), passphrase)
    except backup_crypto.BackupCryptoError as exc:
        print(f"FAIL: {exc}")
        return 3

    target = src.with_suffix(src.suffix + ".drill-restored.db") if args.keep else None
    data = plaintext
    if target is None:
        import tempfile
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".drill.db")
        handle.write(data)
        handle.close()
        target = Path(handle.name)
        cleanup = True
    else:
        target.write_bytes(data)
        cleanup = False

    try:
        digest = hashlib.sha256(data).hexdigest()
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchall()
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
            rows = {}
            for t in tables:
                if t.startswith("sqlite_"):
                    continue
                try:
                    rows[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                except sqlite3.Error:
                    rows[t] = "?"
            fk_violations = con.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            con.close()
        ok = integrity and integrity[0][0] == "ok"
        print(f"Backup   : {src}")
        print(f"Size     : {len(data)} bytes")
        print(f"SHA-256  : {digest}")
        print(f"Integrity: {'ok' if ok else integrity}")
        print(f"Tables   : {len(rows)} total, {sum(v for v in rows.values() if isinstance(v, int))} rows")
        print(f"FK check : {'ok' if not fk_violations else str(len(fk_violations)) + ' violation(s)'}")
        if ok:
            print("PASS: encrypted backup decrypts and passes SQLite integrity_check.")
            return 0
        print("FAIL: integrity_check reported problems — do NOT rely on this backup.")
        return 4
    finally:
        if cleanup:
            try:
                target.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
