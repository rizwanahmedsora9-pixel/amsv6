"""Optional authenticated encryption for backup payloads (AUDIT-111 / R16).

Backups made by ``app.services.daily_backup`` are byte-copies of a database

that contains — among business data — every user credential the app knows

about (Werkzeug hashes, plus any legacy plaintext residue).  Emailed or

downloaded in the clear, any mailbox or download folder that holds the

attachment is a full copy of that credential store at rest.

When ``AMS_BACKUP_ENCRYPTION_ENABLED=1`` and a passphrase is present in

``AMS_BACKUP_ENCRYPTION_PASSWORD``, attachments are encrypted before they

leave the process:

    container := MAGIC("AMSENC1") || salt(16) || fernet_token
    key       := urlsafe_b64encode(PBKDF2-HMAC-SHA256(password, salt, 200_000))

The format is self-describing, key-stretching, and authenticated — a wrong

passphrase or a tampered byte fails decryption loudly (Fernet HMAC), never

silently.  Decryption requires the matching ``tools/restore_drill.py``

(or any implementation of this exact format) and the passphrase, which must

be shared out-of-band and must NOT live in the same mailbox as the backups.

Encryption is opt-in: with the environment off, behaviour is byte-identical

to before.  With it on, a missing ``cryptography`` package or passphrase is a

hard failure for the *email attachment* path — the backup file itself is

still created and kept on disk, and nothing in the clear is sent.
"""
from __future__ import annotations

import importlib.util
import os
import secrets
import tempfile
from pathlib import Path

MAGIC = b"AMSENC1"
SALT_LEN = 16
PBKDF2_ITERATIONS = 200_000

class BackupCryptoError(RuntimeError):
    """Raised when an encrypted backup cannot be made or opened."""


def crypto_available() -> bool:
    return importlib.util.find_spec("cryptography") is not None


def encryption_enabled() -> bool:
    return (os.environ.get("AMS_BACKUP_ENCRYPTION_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def encryption_passphrase() -> str | None:
    passphrase = (os.environ.get("AMS_BACKUP_ENCRYPTION_PASSWORD") or "").strip()
    return passphrase or None


def _key_material(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from base64 import urlsafe_b64encode

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    return urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    if not passphrase:
        raise BackupCryptoError("Refusing to encrypt with an empty passphrase.")
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:  # pragma: no cover - depends on environment
        raise BackupCryptoError(
            "The 'cryptography' package is not installed. Install the optional "
            "extra: pip install -r requirements-crypto.txt"
        ) from exc
    salt = secrets.token_bytes(SALT_LEN)
    fernet = Fernet(_key_material(passphrase, salt))
    return MAGIC + salt + fernet.encrypt(data)


def decrypt_bytes(container: bytes, passphrase: str) -> bytes:
    if not passphrase:
        raise BackupCryptoError("A passphrase is required to decrypt a backup.")
    if not container.startswith(MAGIC):
        raise BackupCryptoError("Not an AMS encrypted backup (missing AMSENC1 magic).")
    salt = container[len(MAGIC):len(MAGIC) + SALT_LEN]
    token = container[len(MAGIC) + SALT_LEN:]
    if len(salt) != SALT_LEN or not token:
        raise BackupCryptoError("Truncated or corrupt AMSENC1 container.")
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except Exception as exc:  # pragma: no cover - depends on environment
        raise BackupCryptoError(
            "The 'cryptography' package is not installed. Install the optional "
            "extra: pip install -r requirements-crypto.txt"
        ) from exc
    try:
        return Fernet(_key_material(passphrase, salt)).decrypt(token)
    except InvalidToken as exc:
        raise BackupCryptoError(
            "Decryption failed — wrong passphrase or a corrupted/tampered file. "
            "No data was recovered."
        ) from exc


def encrypt_file(source: Path, target: Path, passphrase: str) -> None:
    target.write_bytes(encrypt_bytes(Path(source).read_bytes(), passphrase))


def decrypt_file(source: Path, target: Path, passphrase: str) -> None:
    target.write_bytes(decrypt_bytes(Path(source).read_bytes(), passphrase))


def prepare_attachment(source: Path, work_dir: Path | None = None) -> tuple[Path, list[Path]]:
    """Return ``(path_to_send, cleanup_paths)`` for an outbound backup attachment.

    * Encryption disabled  -> the original path, no temp files.
    * Encryption enabled   -> an ``.amsENC`` temp file next to the backup (or in
      *work_dir*), which the caller must delete after sending.  If encryption is
      enabled but cannot be performed, BackupCryptoError is raised and the
      cleartext file is NEVER returned for sending.
    """
    if not encryption_enabled():
        return Path(source), []
    passphrase = encryption_passphrase()
    if not passphrase:
        raise BackupCryptoError(
            "AMS_BACKUP_ENCRYPTION_ENABLED=1 but AMS_BACKUP_ENCRYPTION_PASSWORD is not set. "
            "Refusing to send a cleartext backup."
        )
    if not crypto_available():
        raise BackupCryptoError(
            "AMS_BACKUP_ENCRYPTION_ENABLED=1 but the 'cryptography' package is missing. "
            "Install it with: pip install -r requirements-crypto.txt — refusing to send a cleartext backup."
        )
    folder = Path(work_dir) if work_dir else Path(source).parent
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=Path(source).stem + "-", suffix=".amsenc", dir=str(folder))
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.chmod(0o600)
    try:
        encrypt_file(Path(source), tmp, passphrase)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, [tmp]
