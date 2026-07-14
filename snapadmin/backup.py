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
``manage.py db_backup`` — creates one dump and ships it only to the destinations
whose interval has elapsed. Last-run times persist in a small JSON state file
inside the local backup directory, and every destination keeps at most
``SNAPADMIN_BACKUP_KEEP`` dumps (oldest pruned first).

Dumps are gzip-compressed: a file copy for SQLite, ``pg_dump`` for PostgreSQL.
"""

from __future__ import annotations

import ftplib
import gzip
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

DESTINATIONS = ("local", "network", "remote", "sftp")
BACKUP_PREFIX = "snapadmin-db-"
STATE_FILENAME = ".snapadmin-backup-state.json"


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


def get_backup_config() -> BackupConfig:
    """Read the SNAPADMIN_BACKUP_* settings, applying documented defaults."""
    return BackupConfig(
        enabled=bool(getattr(settings, "SNAPADMIN_BACKUP_ENABLED", False)),
        keep=int(getattr(settings, "SNAPADMIN_BACKUP_KEEP", 7)),
        local_dir=Path(getattr(settings, "SNAPADMIN_BACKUP_LOCAL_DIR", "backups")),
        local_every_hours=int(getattr(settings, "SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS", 24)),
        network_dir=str(getattr(settings, "SNAPADMIN_BACKUP_NETWORK_DIR", "")),
        network_every_hours=int(getattr(settings, "SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS", 24)),
        ftp_host=str(getattr(settings, "SNAPADMIN_BACKUP_FTP_HOST", "")),
        ftp_port=int(getattr(settings, "SNAPADMIN_BACKUP_FTP_PORT", 21)),
        ftp_user=str(getattr(settings, "SNAPADMIN_BACKUP_FTP_USER", "")),
        ftp_password=str(getattr(settings, "SNAPADMIN_BACKUP_FTP_PASSWORD", "")),
        ftp_dir=str(getattr(settings, "SNAPADMIN_BACKUP_FTP_DIR", "/")),
        ftp_tls=bool(getattr(settings, "SNAPADMIN_BACKUP_FTP_TLS", False)),
        remote_every_hours=int(getattr(settings, "SNAPADMIN_BACKUP_REMOTE_EVERY_HOURS", 168)),
        sftp_host=str(getattr(settings, "SNAPADMIN_BACKUP_SFTP_HOST", "")),
        sftp_port=int(getattr(settings, "SNAPADMIN_BACKUP_SFTP_PORT", 22)),
        sftp_user=str(getattr(settings, "SNAPADMIN_BACKUP_SFTP_USER", "")),
        sftp_password=str(getattr(settings, "SNAPADMIN_BACKUP_SFTP_PASSWORD", "")),
        sftp_key_file=str(getattr(settings, "SNAPADMIN_BACKUP_SFTP_KEY_FILE", "")),
        sftp_dir=str(getattr(settings, "SNAPADMIN_BACKUP_SFTP_DIR", "/")),
        sftp_every_hours=int(getattr(settings, "SNAPADMIN_BACKUP_SFTP_EVERY_HOURS", 168)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dump creation
# ─────────────────────────────────────────────────────────────────────────────

def create_db_dump(target_dir: Path) -> Path:
    """Produce a gzip-compressed dump of the default database in target_dir."""
    db = settings.DATABASES["default"]
    engine = db["ENGINE"]
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    target_dir.mkdir(parents=True, exist_ok=True)

    if "sqlite" in engine:
        source = str(db["NAME"])
        if source == ":memory:" or "mode=memory" in source:
            raise BackupError("Cannot back up an in-memory SQLite database.")
        out = target_dir / f"{BACKUP_PREFIX}{stamp}.sqlite3.gz"
        with open(source, "rb") as src, gzip.open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return out

    if "postgresql" in engine:
        out = target_dir / f"{BACKUP_PREFIX}{stamp}.sql.gz"
        command = [
            "pg_dump",
            "--no-password",
            "-h", str(db.get("HOST") or "localhost"),
            "-p", str(db.get("PORT") or "5432"),
            "-U", str(db.get("USER") or ""),
            str(db.get("NAME") or ""),
        ]
        # Stream pg_dump's stdout straight into gzip so the whole uncompressed
        # dump never has to fit in memory at once — a large database would OOM
        # the worker otherwise.
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PGPASSWORD": str(db.get("PASSWORD") or "")},
        )
        with gzip.open(out, "wb") as dst:
            shutil.copyfileobj(process.stdout, dst)
        stderr_output = process.stderr.read()
        returncode = process.wait()
        if returncode != 0:
            out.unlink(missing_ok=True)
            raise BackupError(f"pg_dump failed: {stderr_output.decode(errors='replace')}")
        return out

    raise BackupError(f"Unsupported database engine for backups: {engine}")


# ─────────────────────────────────────────────────────────────────────────────
# Destinations
# ─────────────────────────────────────────────────────────────────────────────

def _prune_directory(directory: Path, keep: int) -> int:
    """Keep the newest `keep` dumps in a directory, delete the rest."""
    dumps = sorted(directory.glob(f"{BACKUP_PREFIX}*"))
    stale = dumps[:-keep] if keep > 0 else dumps
    for path in stale:
        path.unlink()
    return len(stale)


def store_local(dump: Path, config: BackupConfig) -> str:
    config.local_dir.mkdir(parents=True, exist_ok=True)
    target = config.local_dir / dump.name
    if dump.parent != config.local_dir:
        shutil.copy2(dump, target)
    _prune_directory(config.local_dir, config.keep)
    return str(target)


def store_network(dump: Path, config: BackupConfig) -> str:
    if not config.network_dir:
        raise BackupError("SNAPADMIN_BACKUP_NETWORK_DIR is not configured.")
    directory = Path(config.network_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / dump.name
    shutil.copy2(dump, target)
    _prune_directory(directory, config.keep)
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
        # Retention on the remote end: timestamped names sort chronologically.
        dumps = sorted(name for name in ftp.nlst() if name.startswith(BACKUP_PREFIX))
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
        # Retention on the remote end: timestamped names sort chronologically.
        dumps = sorted(name for name in sftp.listdir() if name.startswith(BACKUP_PREFIX))
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
    """Dump the database once and ship it to the given destinations.

    Per-destination failures are logged and reported in the summary — one
    unreachable FTP server must not cancel the local copy. Only destinations
    that succeed get their last-run time updated, so a failed one is retried
    on the next scheduler pass.
    """
    config = config or get_backup_config()
    results: dict[str, str] = {}

    staging = Path(tempfile.mkdtemp(prefix="snapadmin-backup-"))
    try:
        try:
            dump = create_db_dump(staging)
        except BackupError as exc:
            logger.error("db_backup_dump_failed", error=str(exc))
            return {"ran": False, "reason": str(exc), "results": {}}

        state = _load_state(config)
        for dest in destinations:
            try:
                results[dest] = _STORE_FUNCTIONS[dest](dump, config)
                state[dest] = timezone.now().isoformat()
                logger.info("db_backup_stored", destination=dest, location=results[dest])
            except Exception as exc:
                results[dest] = f"error: {exc}"
                logger.error("db_backup_store_failed", destination=dest, error=str(exc))
        _save_state(config, state)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {"ran": True, "dump": dump.name, "results": results}


def run_due_backups() -> dict:
    """Back up to every destination whose interval has elapsed (Beat/cron hook)."""
    config = get_backup_config()
    if not config.enabled:
        return {"ran": False, "reason": "disabled", "results": {}}
    due = due_destinations(config)
    if not due:
        return {"ran": False, "reason": "not_due", "results": {}}
    return run_backup(due, config=config)
