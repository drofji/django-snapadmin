"""
snapadmin/backup.py

3-2-1 database backups for SnapAdmin.

The 3-2-1 rule — **3** copies of your data, on **2** different machines/media,
**1** of them offsite — maps onto four configurable destinations:

  1. ``local``   — a directory on the same server (``SNAPADMIN_BACKUP_LOCAL_DIR``).
                   Always active while backups are enabled; also stages the dump.
  2. ``network`` — a directory on another server on the same network, reachable
                   as a mounted share (NFS/SMB): ``SNAPADMIN_BACKUP_NETWORK_DIR``.
  3. ``remote``  — an offsite server via FTP/FTPS (``SNAPADMIN_BACKUP_FTP_*``).
  4. ``sftp``    — an offsite server over SSH/SFTP (``SNAPADMIN_BACKUP_SFTP_*``),
                   password or private-key auth. Requires the optional ``paramiko``
                   dependency (``pip install django-snapadmin[backup]``). Use this
                   instead of — or alongside — plain FTP for an encrypted offsite copy.

Each destination has its own frequency (``SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS`` /
``_NETWORK_EVERY_HOURS`` / ``_REMOTE_EVERY_HOURS`` / ``_SFTP_EVERY_HOURS``):
``run_due_backups()`` — called
by the ``snapadmin.run_db_backups`` Celery Beat task or from cron via
``manage.py snapadmin_db_backup`` — creates one dump and ships it only to the destinations
whose interval has elapsed. Last-run times persist in a small JSON state file
inside the local backup directory, and every destination keeps at most
``SNAPADMIN_BACKUP_KEEP`` dumps (oldest pruned first).

Dumps are gzip-compressed: a file copy for SQLite, ``pg_dump`` for PostgreSQL.

**Encryption at rest** — set ``SNAPADMIN_BACKUP_AGE_RECIPIENTS`` (a list of one or more
AGE/SSH public keys) and every dump is streamed straight through :mod:`snapadmin.crypto`
before a single byte reaches disk: ``pg_dump``/SQLite → gzip → age → the ``.age``-suffixed
file. No plaintext or plain-gzip artefact is ever written, even transiently. With the
setting empty (the default) nothing changes — this is the exact behaviour described above.
"""

from __future__ import annotations

import ftplib
import functools
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Callable

import django
from django.conf import settings
from django.utils import timezone

from snapadmin import __version__, crypto
from snapadmin.conf import get_setting
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

DESTINATIONS = ("local", "network", "remote", "sftp")
BACKUP_PREFIX = "snapadmin-db-"
STATE_FILENAME = ".snapadmin-backup-state.json"

#: Every bundle part's own filename prefix (#BKP1a-4's "loose files" design).
#: "keep N" retention runs once per prefix, independently, so a run that
#: includes media doesn't starve the db dump's retention headroom.
PART_PREFIXES = {
    "db": BACKUP_PREFIX,
    "media": "snapadmin-media-",
    "env": "snapadmin-env-",
    "manifest": "snapadmin-manifest-",
}

#: Parts SNAPADMIN_BACKUP_INCLUDE may name.
BACKUP_PARTS = ("db", "media", "env")


class BackupError(Exception):
    """Raised when a database dump cannot be produced."""


@dataclass(frozen=True)
class BackupConfig:
    """Snapshot of all SNAPADMIN_BACKUP_* settings with their defaults."""

    enabled: bool
    keep: int
    local_dir: Path
    local_every_hours: int
    network_dir: str
    network_every_hours: int
    ftp_host: str
    ftp_port: int
    ftp_user: str
    ftp_password: str
    ftp_dir: str
    ftp_tls: bool
    remote_every_hours: int
    sftp_host: str
    sftp_port: int
    sftp_user: str
    sftp_password: str
    sftp_key_file: str
    sftp_dir: str
    sftp_every_hours: int
    age_recipients: list[str]
    age_identity_file: str
    age_backend: str
    age_binary_path: str
    include: list[str]
    media_exclude: list[str]
    env_file: str
    media_size_warning_bytes: int


def get_backup_config() -> BackupConfig:
    """Read the SNAPADMIN_BACKUP_* settings, applying documented defaults."""
    return BackupConfig(
        enabled=bool(get_setting("SNAPADMIN_BACKUP_ENABLED", False)),
        keep=int(get_setting("SNAPADMIN_BACKUP_KEEP", 7)),
        local_dir=Path(get_setting("SNAPADMIN_BACKUP_LOCAL_DIR", "backups")),
        local_every_hours=int(get_setting("SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS", 24)),
        network_dir=str(get_setting("SNAPADMIN_BACKUP_NETWORK_DIR", "")),
        network_every_hours=int(get_setting("SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS", 24)),
        ftp_host=str(get_setting("SNAPADMIN_BACKUP_FTP_HOST", "")),
        ftp_port=int(get_setting("SNAPADMIN_BACKUP_FTP_PORT", 21)),
        ftp_user=str(get_setting("SNAPADMIN_BACKUP_FTP_USER", "")),
        ftp_password=str(get_setting("SNAPADMIN_BACKUP_FTP_PASSWORD", "")),
        ftp_dir=str(get_setting("SNAPADMIN_BACKUP_FTP_DIR", "/")),
        ftp_tls=bool(get_setting("SNAPADMIN_BACKUP_FTP_TLS", False)),
        remote_every_hours=int(get_setting("SNAPADMIN_BACKUP_REMOTE_EVERY_HOURS", 168)),
        sftp_host=str(get_setting("SNAPADMIN_BACKUP_SFTP_HOST", "")),
        sftp_port=int(get_setting("SNAPADMIN_BACKUP_SFTP_PORT", 22)),
        sftp_user=str(get_setting("SNAPADMIN_BACKUP_SFTP_USER", "")),
        sftp_password=str(get_setting("SNAPADMIN_BACKUP_SFTP_PASSWORD", "")),
        sftp_key_file=str(get_setting("SNAPADMIN_BACKUP_SFTP_KEY_FILE", "")),
        sftp_dir=str(get_setting("SNAPADMIN_BACKUP_SFTP_DIR", "/")),
        sftp_every_hours=int(get_setting("SNAPADMIN_BACKUP_SFTP_EVERY_HOURS", 168)),
        age_recipients=list(get_setting("SNAPADMIN_BACKUP_AGE_RECIPIENTS", None) or []),
        age_identity_file=str(get_setting("SNAPADMIN_BACKUP_AGE_IDENTITY_FILE", "")),
        age_backend=str(get_setting("SNAPADMIN_BACKUP_AGE_BACKEND", "auto")),
        age_binary_path=str(get_setting("SNAPADMIN_BACKUP_AGE_BINARY_PATH", "")),
        include=list(get_setting("SNAPADMIN_BACKUP_INCLUDE", None) or ["db"]),
        media_exclude=list(get_setting("SNAPADMIN_BACKUP_MEDIA_EXCLUDE", None) or []),
        env_file=str(get_setting("SNAPADMIN_BACKUP_ENV_FILE", "") or ""),
        media_size_warning_bytes=int(
            get_setting("SNAPADMIN_BACKUP_MEDIA_SIZE_WARNING_BYTES", 10 * 1024**3)
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dump creation
# ─────────────────────────────────────────────────────────────────────────────

def _sqlite_source_path(db: dict) -> str:
    source = str(db["NAME"])
    if source == ":memory:" or "mode=memory" in source:
        raise BackupError("Cannot back up an in-memory SQLite database.")
    return source


def _copy_sqlite_into(db: dict, writer: BinaryIO) -> None:
    with open(_sqlite_source_path(db), "rb") as src:
        shutil.copyfileobj(src, writer)


def _postgres_dump_command(db: dict) -> tuple[list[str], dict]:
    command = [
        "pg_dump",
        "--no-password",
        "-h", str(db.get("HOST") or "localhost"),
        "-p", str(db.get("PORT") or "5432"),
        "-U", str(db.get("USER") or ""),
        str(db.get("NAME") or ""),
    ]
    env = {**os.environ, "PGPASSWORD": str(db.get("PASSWORD") or "")}
    return command, env


def _copy_postgres_into(db: dict, writer: BinaryIO) -> None:
    command, env = _postgres_dump_command(db)
    # Stream pg_dump's stdout straight into the writer so the whole uncompressed
    # dump never has to fit in memory at once — a large database would OOM the
    # worker otherwise.
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    shutil.copyfileobj(process.stdout, writer)
    stderr_output = process.stderr.read()
    returncode = process.wait()
    if returncode != 0:
        raise BackupError(f"pg_dump failed: {stderr_output.decode(errors='replace')}")


def create_db_dump(target_dir: Path) -> Path:
    """Produce a gzip-compressed dump of the default database in target_dir."""
    db = settings.DATABASES["default"]
    engine = db["ENGINE"]
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    target_dir.mkdir(parents=True, exist_ok=True)

    if "sqlite" in engine:
        out = target_dir / f"{BACKUP_PREFIX}{stamp}.sqlite3.gz"
        with gzip.open(out, "wb") as dst:
            _copy_sqlite_into(db, dst)
        return out

    if "postgresql" in engine:
        out = target_dir / f"{BACKUP_PREFIX}{stamp}.sql.gz"
        try:
            with gzip.open(out, "wb") as dst:
                _copy_postgres_into(db, dst)
        except BackupError:
            out.unlink(missing_ok=True)
            raise
        return out

    raise BackupError(f"Unsupported database engine for backups: {engine}")


# ─────────────────────────────────────────────────────────────────────────────
# Encrypted dump creation — #BKP1b
#
# The encrypted path never materializes a plaintext or plain-gzip artefact on
# disk, not even transiently: the dump is gzip-compressed and AGE-encrypted in
# one continuous stream, using a real OS pipe (`os.pipe()`) so memory stays
# bounded exactly like the pg_dump-into-gzip streaming above. A background
# thread produces gzip-compressed bytes into the pipe's write end; the read
# end is handed straight to `crypto.encrypt_stream` as its source. The output
# is written to a `.tmp` file and only `os.replace()`d into its final name once
# the whole pipeline has completed without error — any failure anywhere (the
# dump itself, gzip, or encryption) leaves no file at all behind, not a
# corrupt or partial one.
# ─────────────────────────────────────────────────────────────────────────────

class _ProducerThread(threading.Thread):
    """Runs `producer(write_end)` in the background, capturing any exception
    so the reader side can re-raise it via `check()` once done reading."""

    def __init__(self, producer: Callable[[BinaryIO], None], write_fh: BinaryIO) -> None:
        super().__init__(daemon=True)
        self._producer = producer
        self._write_fh = write_fh
        self.exception: BaseException | None = None

    def run(self) -> None:
        try:
            self._producer(self._write_fh)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the reader side via check()
            self.exception = exc
        finally:
            try:
                self._write_fh.close()
            except BrokenPipeError:
                # The reader side may already be gone — e.g. encrypt_stream
                # failed (bad recipient, missing backend) before it ever read
                # anything, and create_encrypted_db_dump's cleanup closed the
                # read end while we were still writing. Nothing was listening
                # any more, so there is nothing left to flush; the producer's
                # own exception (if any) is still captured above regardless.
                pass

    def check(self) -> None:
        """Join the thread and re-raise its exception, if any."""
        self.join()
        if self.exception is not None:
            raise self.exception


def _stream_through_pipe(producer: Callable[[BinaryIO], None]) -> tuple[BinaryIO, _ProducerThread]:
    """Return (reader, thread): bytes `producer` writes become readable from `reader`.

    A real OS pipe applies natural backpressure (its own small buffer), so
    nothing is buffered in full — the same streaming guarantee the
    pg_dump-into-gzip path above already has.
    """
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb")
    writer = os.fdopen(write_fd, "wb")
    thread = _ProducerThread(producer, writer)
    thread.start()
    return reader, thread


def _gzip_into(writer: BinaryIO, copy_fn: Callable[[BinaryIO], None]) -> None:
    with gzip.GzipFile(fileobj=writer, mode="wb") as gz:
        copy_fn(gz)


def create_encrypted_db_dump(target_dir: Path, config: BackupConfig) -> Path:
    """Like :func:`create_db_dump`, but gzip-then-AGE-encrypted in one stream.

    Requires ``config.age_recipients`` to be non-empty (checked by the caller,
    which is what decides whether to call this function or the plain one at
    all — see :func:`run_backup`).
    """
    db = settings.DATABASES["default"]
    engine = db["ENGINE"]
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    target_dir.mkdir(parents=True, exist_ok=True)

    if "sqlite" in engine:
        _sqlite_source_path(db)  # validate early, before the pipe thread starts
        copy_fn = functools.partial(_copy_sqlite_into, db)
        suffix = "sqlite3.gz.age"
    elif "postgresql" in engine:
        copy_fn = functools.partial(_copy_postgres_into, db)
        suffix = "sql.gz.age"
    else:
        raise BackupError(f"Unsupported database engine for backups: {engine}")

    out = target_dir / f"{BACKUP_PREFIX}{stamp}.{suffix}"
    tmp = out.with_name(out.name + ".tmp")
    reader, thread = _stream_through_pipe(lambda w: _gzip_into(w, copy_fn))
    try:
        with open(tmp, "wb") as dst:
            crypto.encrypt_stream(
                reader,
                dst,
                config.age_recipients,
                backend=config.age_backend,
                binary_path=config.age_binary_path,
            )
        thread.check()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        reader.close()
        thread.join(timeout=5)
    tmp.replace(out)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Bundle parts — media and env — #BKP1c
#
# Same atomic-write discipline as the encrypted db dump above: written to a
# `.tmp` file in target_dir, `os.replace()`d into place only once the whole
# part has been produced without error. When recipients are configured both
# parts are streamed through the same OS-pipe + `crypto.encrypt_stream`
# plumbing `create_encrypted_db_dump` already established; unencrypted, they
# are written directly.
# ─────────────────────────────────────────────────────────────────────────────

def _iter_media_files(media_root: Path, exclude: list[str]) -> list[Path]:
    """Every regular file under media_root, skipping any glob-matched path.

    A glob is matched against the path relative to media_root (e.g.
    "cache/**" or "*.tmp"), mirroring how `.gitignore`-style excludes read.
    Called only once :func:`create_media_bundle` has confirmed media_root is
    an existing directory.
    """
    files = sorted(p for p in media_root.rglob("*") if p.is_file())
    if not exclude:
        return files
    return [
        p for p in files
        if not any(p.relative_to(media_root).match(pattern) for pattern in exclude)
    ]


def _tar_media_into(media_root: Path, files: list[Path], writer: BinaryIO, warning_bytes: int) -> None:
    """Write every file in `files` into a tar stream on `writer`.

    An unreadable file (permission error, vanished between listing and read,
    a broken symlink) is skipped with a warning — a broken thumbnail must not
    cost you the database. Total size is tracked purely to log a warning past
    `warning_bytes`; nothing is buffered to compute it, each file's own size
    (from `stat()`) is added as it streams.
    """
    import tarfile

    total_bytes = 0
    warned = False
    with tarfile.open(fileobj=writer, mode="w|") as tar:
        for path in files:
            try:
                arcname = str(path.relative_to(media_root))
                tar.add(path, arcname=arcname, recursive=False)
                total_bytes += path.stat().st_size
            except OSError as exc:
                logger.warning("media_backup_file_skipped", path=str(path), error=str(exc))
                continue
            if not warned and total_bytes > warning_bytes:
                warned = True
                logger.warning(
                    "media_backup_size_warning",
                    total_bytes=total_bytes,
                    threshold_bytes=warning_bytes,
                )


def create_media_bundle(target_dir: Path, config: BackupConfig, stamp: str) -> Path | None:
    """Tar (and optionally encrypt) MEDIA_ROOT into target_dir. None if MEDIA_ROOT is unset/missing.

    Streamed throughout — the tar is built directly into the pipe/file, never
    assembled in memory or as an intermediate plaintext file on disk before
    encryption, exactly like :func:`create_encrypted_db_dump`.
    """
    media_root_setting = str(getattr(settings, "MEDIA_ROOT", "") or "")
    if not media_root_setting or not Path(media_root_setting).is_dir():
        logger.warning("media_backup_skipped", reason="MEDIA_ROOT not set or missing")
        return None
    media_root = Path(media_root_setting)

    files = _iter_media_files(media_root, config.media_exclude)
    tar_fn = functools.partial(_tar_media_into, media_root, files, warning_bytes=config.media_size_warning_bytes)

    if config.age_recipients:
        out = target_dir / f"{PART_PREFIXES['media']}{stamp}.tar.gz.age"
        tmp = out.with_name(out.name + ".tmp")
        reader, thread = _stream_through_pipe(lambda w: _gzip_into(w, tar_fn))
        try:
            with open(tmp, "wb") as dst:
                crypto.encrypt_stream(
                    reader, dst, config.age_recipients,
                    backend=config.age_backend, binary_path=config.age_binary_path,
                )
            thread.check()
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            reader.close()
            thread.join(timeout=5)
        tmp.replace(out)
        return out

    out = target_dir / f"{PART_PREFIXES['media']}{stamp}.tar.gz"
    tmp = out.with_name(out.name + ".tmp")
    try:
        with gzip.open(tmp, "wb") as dst:
            tar_fn(dst)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(out)
    return out


def check_env_requires_encryption(config: BackupConfig) -> None:
    """Runtime twin of ``snapadmin.E007`` — fails closed even if reached another way.

    The startup system check catches the common case; this guard exists for
    the case the check's own docstring calls out: recipients configured, then
    cleared, without a restart. Never let a plaintext ``.env`` reach a backup
    destination.
    """
    if "env" in config.include and not config.age_recipients:
        raise BackupError(
            "SNAPADMIN_BACKUP_INCLUDE includes 'env' but SNAPADMIN_BACKUP_AGE_RECIPIENTS "
            "is empty — refusing to write an unencrypted .env file to a backup destination."
        )


def create_env_bundle(target_dir: Path, config: BackupConfig, stamp: str) -> Path | None:
    """Encrypt the configured .env file into target_dir. None if it doesn't exist.

    Always encrypted — :func:`check_env_requires_encryption` (called by
    :func:`build_backup_bundle` before this ever runs) guarantees recipients
    are configured whenever this function is reached at all.
    """
    check_env_requires_encryption(config)
    env_path = Path(config.env_file) if config.env_file else None
    if not env_path or not env_path.is_file():
        logger.warning("env_backup_skipped", reason="SNAPADMIN_BACKUP_ENV_FILE not set or missing")
        return None

    out = target_dir / f"{PART_PREFIXES['env']}{stamp}.age"
    tmp = out.with_name(out.name + ".tmp")
    try:
        with open(env_path, "rb") as reader, open(tmp, "wb") as writer:
            crypto.encrypt_stream(
                reader, writer, config.age_recipients,
                backend=config.age_backend, binary_path=config.age_binary_path,
            )
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(out)
    return out


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(target_dir: Path, config: BackupConfig, stamp: str, parts: dict[str, Path]) -> Path:
    """Write the always-unencrypted manifest.json sidecar for one backup run.

    Never encrypted — it must be readable without an identity, for
    `snapadmin_restore --list` and for the "this bundle needs --identity"
    message. Contains no secrets: part names, per-part *ciphertext* checksum
    (so a truncated upload is caught before any decrypt attempt), versions,
    timestamp, and the full recipient list (public keys, safe to print).
    """
    manifest_name = f"{PART_PREFIXES['manifest']}{stamp}.json"
    manifest = {
        "snapadmin_version": __version__,
        "django_version": django.get_version(),
        "db_engine": settings.DATABASES["default"]["ENGINE"],
        "timestamp": stamp,
        "encrypted": bool(config.age_recipients),
        "recipients": list(config.age_recipients),
        "recipient_fingerprints": [crypto.fingerprint(r) for r in config.age_recipients],
        "parts": {
            name: {"filename": path.name, "sha256": sha256_file(path)}
            for name, path in parts.items()
        },
        # How to get this bundle's data back — printed as-is by `snapadmin_restore
        # --list` so an operator with no docs open still knows the next command.
        "restore_hint": f"python manage.py snapadmin_restore {manifest_name}"
        + (" --identity <path-to-identity-file>" if config.age_recipients else ""),
    }
    out = target_dir / f"{PART_PREFIXES['manifest']}{stamp}.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return out


def build_backup_bundle(target_dir: Path, config: BackupConfig) -> dict[str, Path]:
    """Produce every part named in config.include, plus the manifest.

    Returns a dict keyed by part name ("db", "media", "env", "manifest") to
    the produced file. A part whose builder returns None (media/env with
    nothing to back up) is simply absent from the result — the manifest still
    lists only what actually exists.
    """
    check_env_requires_encryption(config)  # fail closed before any part is built
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    target_dir.mkdir(parents=True, exist_ok=True)

    parts: dict[str, Path] = {}
    for name in config.include:
        if name == "db":
            dump = create_encrypted_db_dump(target_dir, config) if config.age_recipients else create_db_dump(target_dir)
            parts["db"] = dump
        elif name == "media":
            media = create_media_bundle(target_dir, config, stamp)
            if media is not None:
                parts["media"] = media
        elif name == "env":
            env = create_env_bundle(target_dir, config, stamp)
            if env is not None:
                parts["env"] = env
        else:
            raise BackupError(f"Unknown SNAPADMIN_BACKUP_INCLUDE part: {name!r} (expected one of {BACKUP_PARTS}).")

    manifest = write_manifest(target_dir, config, stamp, parts)
    parts["manifest"] = manifest
    return parts


# ─────────────────────────────────────────────────────────────────────────────
# Destinations
# ─────────────────────────────────────────────────────────────────────────────

def _part_prefix_for(name: str) -> str:
    """The stored filename's own part prefix, e.g. "snapadmin-media-20260826….tar.gz.age" -> "snapadmin-media-".

    Retention prunes per part (#BKP1a-4): a run that includes media must not
    starve the db dump's retention headroom, so "keep N" means "keep N of
    each part", not N total. Falls back to BACKUP_PREFIX for any name that
    (unexpectedly) matches none of the known prefixes, preserving today's
    only-`db`-exists behaviour exactly.
    """
    for prefix in PART_PREFIXES.values():
        if name.startswith(prefix):
            return prefix
    return BACKUP_PREFIX


def _prune_directory(directory: Path, keep: int, prefix: str) -> int:
    """Keep the newest `keep` files matching `prefix` in a directory, delete the rest."""
    dumps = sorted(directory.glob(f"{prefix}*"))
    stale = dumps[:-keep] if keep > 0 else dumps
    for path in stale:
        path.unlink()
    return len(stale)


def store_local(dump: Path, config: BackupConfig) -> str:
    config.local_dir.mkdir(parents=True, exist_ok=True)
    target = config.local_dir / dump.name
    if dump.parent != config.local_dir:
        shutil.copy2(dump, target)
    _prune_directory(config.local_dir, config.keep, _part_prefix_for(dump.name))
    return str(target)


def store_network(dump: Path, config: BackupConfig) -> str:
    if not config.network_dir:
        raise BackupError("SNAPADMIN_BACKUP_NETWORK_DIR is not configured.")
    directory = Path(config.network_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / dump.name
    shutil.copy2(dump, target)
    _prune_directory(directory, config.keep, _part_prefix_for(dump.name))
    return str(target)


def store_remote_ftp(dump: Path, config: BackupConfig) -> str:
    if not config.ftp_host:
        raise BackupError("SNAPADMIN_BACKUP_FTP_HOST is not configured.")
    ftp_class = ftplib.FTP_TLS if config.ftp_tls else ftplib.FTP
    # A hung offsite server must not block the worker forever.
    ftp = ftp_class(timeout=60)
    ftp.connect(config.ftp_host, config.ftp_port)
    ftp.login(config.ftp_user, config.ftp_password)
    if config.ftp_tls:
        ftp.prot_p()
    try:
        try:
            ftp.cwd(config.ftp_dir)
        except ftplib.error_perm:
            ftp.mkd(config.ftp_dir)
            ftp.cwd(config.ftp_dir)
        with open(dump, "rb") as fh:
            ftp.storbinary(f"STOR {dump.name}", fh)
        # Retention on the remote end: timestamped names sort chronologically,
        # pruned per part prefix so media/env/manifest don't share db's budget.
        prefix = _part_prefix_for(dump.name)
        dumps = sorted(name for name in ftp.nlst() if name.startswith(prefix))
        for name in dumps[:-config.keep] if config.keep > 0 else dumps:
            ftp.delete(name)
    finally:
        ftp.quit()
    return f"ftp://{config.ftp_host}:{config.ftp_port}{config.ftp_dir.rstrip('/')}/{dump.name}"


def store_remote_sftp(dump: Path, config: BackupConfig) -> str:
    """Upload the dump to an offsite server over SSH/SFTP (encrypted transport).

    Authenticates with a private key (``SNAPADMIN_BACKUP_SFTP_KEY_FILE``) when set,
    otherwise with ``SNAPADMIN_BACKUP_SFTP_PASSWORD``. Requires the optional
    ``paramiko`` dependency — install ``django-snapadmin[backup]``.

    Host keys are verified against ``~/.ssh/known_hosts`` (loaded via
    ``load_system_host_keys()``): a host whose key is not already known is
    rejected rather than silently trusted, so an operator must pre-populate
    ``known_hosts`` for the SFTP target before offsite backups will work — e.g.
    ``ssh-keyscan -H offsite.example.com >> ~/.ssh/known_hosts`` during deployment,
    or a one-off ``ssh`` connection as the service user. This closes the
    man-in-the-middle window that a trust-on-first-use policy would leave open.
    """
    if not config.sftp_host:
        raise BackupError("SNAPADMIN_BACKUP_SFTP_HOST is not configured.")
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise BackupError(
            "SFTP backups require paramiko — install django-snapadmin[backup]."
        ) from exc

    client = paramiko.SSHClient()
    client.load_system_host_keys()  # honour ~/.ssh/known_hosts if present
    # Reject unknown host keys instead of trust-on-first-use: silently accepting
    # any key on first connect would hand a man-in-the-middle a permanent foothold
    # on the offsite copy. The operator must pre-populate known_hosts.
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kwargs = {
        "hostname": config.sftp_host,
        "port": config.sftp_port,
        "username": config.sftp_user,
        # A hung offsite server must not block the worker forever.
        "timeout": 60,
    }
    if config.sftp_key_file:
        connect_kwargs["key_filename"] = config.sftp_key_file
    else:
        connect_kwargs["password"] = config.sftp_password
    client.connect(**connect_kwargs)
    try:
        sftp = client.open_sftp()
        try:
            sftp.chdir(config.sftp_dir)
        except IOError:
            sftp.mkdir(config.sftp_dir)
            sftp.chdir(config.sftp_dir)
        sftp.put(str(dump), dump.name)
        # Retention on the remote end: timestamped names sort chronologically,
        # pruned per part prefix so media/env/manifest don't share db's budget.
        prefix = _part_prefix_for(dump.name)
        dumps = sorted(name for name in sftp.listdir() if name.startswith(prefix))
        for name in dumps[:-config.keep] if config.keep > 0 else dumps:
            sftp.remove(name)
        sftp.close()
    finally:
        client.close()
    return f"sftp://{config.sftp_host}:{config.sftp_port}{config.sftp_dir.rstrip('/')}/{dump.name}"


_STORE_FUNCTIONS = {
    "local": store_local,
    "network": store_network,
    "remote": store_remote_ftp,
    "sftp": store_remote_sftp,
}


# ─────────────────────────────────────────────────────────────────────────────
# Fetching — the restore-side mirror of the store functions above — #BKP1d
# ─────────────────────────────────────────────────────────────────────────────

def list_local(config: BackupConfig) -> list[str]:
    if not config.local_dir.is_dir():
        return []
    return sorted(p.name for p in config.local_dir.iterdir() if p.is_file())


def fetch_local(name: str, target_dir: Path, config: BackupConfig) -> Path:
    source = config.local_dir / name
    if not source.is_file():
        raise BackupError(f"{name!r} not found in {config.local_dir}.")
    target = target_dir / name
    if source != target:
        shutil.copy2(source, target)
    return target


def list_network(config: BackupConfig) -> list[str]:
    if not config.network_dir:
        raise BackupError("SNAPADMIN_BACKUP_NETWORK_DIR is not configured.")
    directory = Path(config.network_dir)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def fetch_network(name: str, target_dir: Path, config: BackupConfig) -> Path:
    if not config.network_dir:
        raise BackupError("SNAPADMIN_BACKUP_NETWORK_DIR is not configured.")
    source = Path(config.network_dir) / name
    if not source.is_file():
        raise BackupError(f"{name!r} not found in {config.network_dir}.")
    target = target_dir / name
    shutil.copy2(source, target)
    return target


def list_remote_ftp(config: BackupConfig) -> list[str]:
    if not config.ftp_host:
        raise BackupError("SNAPADMIN_BACKUP_FTP_HOST is not configured.")
    ftp_class = ftplib.FTP_TLS if config.ftp_tls else ftplib.FTP
    ftp = ftp_class(timeout=60)
    ftp.connect(config.ftp_host, config.ftp_port)
    ftp.login(config.ftp_user, config.ftp_password)
    if config.ftp_tls:
        ftp.prot_p()
    try:
        try:
            ftp.cwd(config.ftp_dir)
        except ftplib.error_perm:
            return []
        return sorted(ftp.nlst())
    finally:
        ftp.quit()


def fetch_remote_ftp(name: str, target_dir: Path, config: BackupConfig) -> Path:
    if not config.ftp_host:
        raise BackupError("SNAPADMIN_BACKUP_FTP_HOST is not configured.")
    ftp_class = ftplib.FTP_TLS if config.ftp_tls else ftplib.FTP
    ftp = ftp_class(timeout=60)
    ftp.connect(config.ftp_host, config.ftp_port)
    ftp.login(config.ftp_user, config.ftp_password)
    if config.ftp_tls:
        ftp.prot_p()
    target = target_dir / name
    try:
        ftp.cwd(config.ftp_dir)
        with open(target, "wb") as fh:
            ftp.retrbinary(f"RETR {name}", fh.write)
    except ftplib.all_errors as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"Could not fetch {name!r} from FTP: {exc}") from exc
    finally:
        ftp.quit()
    return target


def list_remote_sftp(config: BackupConfig) -> list[str]:
    if not config.sftp_host:
        raise BackupError("SNAPADMIN_BACKUP_SFTP_HOST is not configured.")
    client, sftp = _connect_sftp(config)
    try:
        try:
            sftp.chdir(config.sftp_dir)
        except IOError:
            return []
        return sorted(sftp.listdir())
    finally:
        client.close()


def fetch_remote_sftp(name: str, target_dir: Path, config: BackupConfig) -> Path:
    if not config.sftp_host:
        raise BackupError("SNAPADMIN_BACKUP_SFTP_HOST is not configured.")
    client, sftp = _connect_sftp(config)
    target = target_dir / name
    try:
        sftp.chdir(config.sftp_dir)
        sftp.get(name, str(target))
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"Could not fetch {name!r} from SFTP: {exc}") from exc
    finally:
        client.close()
    return target


def _connect_sftp(config: BackupConfig):
    """Shared connect step for the SFTP list/fetch pair above — same host-key
    verification policy as :func:`store_remote_sftp` (reject unknown keys)."""
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise BackupError(
            "SFTP backups require paramiko — install django-snapadmin[backup]."
        ) from exc

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kwargs = {
        "hostname": config.sftp_host,
        "port": config.sftp_port,
        "username": config.sftp_user,
        "timeout": 60,
    }
    if config.sftp_key_file:
        connect_kwargs["key_filename"] = config.sftp_key_file
    else:
        connect_kwargs["password"] = config.sftp_password
    client.connect(**connect_kwargs)
    return client, client.open_sftp()


LIST_FUNCTIONS = {
    "local": list_local,
    "network": list_network,
    "remote": list_remote_ftp,
    "sftp": list_remote_sftp,
}

FETCH_FUNCTIONS = {
    "local": fetch_local,
    "network": fetch_network,
    "remote": fetch_remote_ftp,
    "sftp": fetch_remote_sftp,
}


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling state
# ─────────────────────────────────────────────────────────────────────────────

def _state_path(config: BackupConfig) -> Path:
    return config.local_dir / STATE_FILENAME


def _load_state(config: BackupConfig) -> dict:
    try:
        return json.loads(_state_path(config).read_text())
    except (OSError, ValueError):
        return {}


def _save_state(config: BackupConfig, state: dict) -> None:
    config.local_dir.mkdir(parents=True, exist_ok=True)
    _state_path(config).write_text(json.dumps(state))


def _is_due(last_run_iso: str | None, every_hours: int, now: datetime) -> bool:
    if not last_run_iso:
        return True
    last_run = datetime.fromisoformat(last_run_iso)
    return now - last_run >= timedelta(hours=every_hours)


def _active_destinations(config: BackupConfig) -> list[str]:
    active = ["local"]
    if config.network_dir:
        active.append("network")
    if config.ftp_host:
        active.append("remote")
    if config.sftp_host:
        active.append("sftp")
    return active


def due_destinations(config: BackupConfig | None = None) -> list[str]:
    """Active destinations whose per-destination interval has elapsed."""
    config = config or get_backup_config()
    state = _load_state(config)
    now = timezone.now()
    intervals = {
        "local": config.local_every_hours,
        "network": config.network_every_hours,
        "remote": config.remote_every_hours,
        "sftp": config.sftp_every_hours,
    }
    return [
        dest
        for dest in _active_destinations(config)
        if _is_due(state.get(dest), intervals[dest], now)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────

def run_backup(destinations: list[str], *, config: BackupConfig | None = None) -> dict:
    """Build one backup bundle and ship every part of it to the given destinations.

    Per-destination failures are logged and reported in the summary — one
    unreachable FTP server must not cancel the local copy. Only destinations
    that succeed on every part get their last-run time updated, so a failed
    one is retried in full on the next scheduler pass. With
    SNAPADMIN_BACKUP_INCLUDE at its default (["db"]), this ships exactly the
    db dump plus its manifest — the sole new artefact next to today's dump,
    the dump itself is byte-for-byte what create_db_dump()/
    create_encrypted_db_dump() has always produced.
    """
    config = config or get_backup_config()
    results: dict[str, str] = {}

    staging = Path(tempfile.mkdtemp(prefix="snapadmin-backup-"))
    try:
        try:
            parts = build_backup_bundle(staging, config)
        except BackupError as exc:
            logger.error("db_backup_dump_failed", error=str(exc))
            return {"ran": False, "reason": str(exc), "results": {}}

        # The part named in the summary's "results" entry per destination —
        # "db" when present (matches every existing caller/test's expectation
        # of one location string), else whichever part actually shipped.
        primary_part = "db" if "db" in parts else next(iter(parts))

        state = _load_state(config)
        for dest in destinations:
            try:
                locations = {}
                for part_name, part_path in parts.items():
                    locations[part_name] = _STORE_FUNCTIONS[dest](part_path, config)
                    logger.info(
                        "db_backup_stored", destination=dest, part=part_name,
                        location=locations[part_name],
                    )
                state[dest] = timezone.now().isoformat()
                results[dest] = locations[primary_part]
            except Exception as exc:
                results[dest] = f"error: {exc}"
                logger.error("db_backup_store_failed", destination=dest, error=str(exc))
        _save_state(config, state)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {"ran": True, "dump": parts[primary_part].name, "results": results}


def run_due_backups() -> dict:
    """Back up to every destination whose interval has elapsed (Beat/cron hook)."""
    config = get_backup_config()
    if not config.enabled:
        return {"ran": False, "reason": "disabled", "results": {}}
    due = due_destinations(config)
    if not due:
        return {"ran": False, "reason": "not_due", "results": {}}
    return run_backup(due, config=config)
