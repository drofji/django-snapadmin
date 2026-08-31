"""
snapadmin/restore.py

Restoring a backup bundle produced by :mod:`snapadmin.backup` (#BKP1c/d).

A bundle is never trusted blindly: the manifest's per-part checksum is
verified before a single byte is written to the live database, media, or
``.env`` file, and every restore is **dry-run by default** — planning what
would happen and changing nothing until ``--confirm`` is passed. This mirrors
the fail-closed philosophy already established by ``SanitizedHtmlOnSaveMixin``
and the backup layer's own ``.env`` guard: a destructive step never runs on a
best-effort basis.

The pre-restore snapshot (#BKP1e, ``snapadmin.snapshot``) hooks in via
:func:`perform_restore`'s ``before_restore`` callback — this module knows
nothing about snapshots itself, keeping the two concerns independently
testable.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from django.conf import settings
from django.utils import timezone

from snapadmin import __version__, crypto
from snapadmin.backup import (
    PART_PREFIXES,
    BackupConfig,
    BackupError,
    FETCH_FUNCTIONS,
    LIST_FUNCTIONS,
    sha256_file,
)
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: Parts a restore may select via --only/--skip.
RESTORE_PARTS = ("db", "media", "env")

#: Records that a restore has completed at least once, for the `features` and
#: `snapadmin_info` "have you ever restored?" reports (#BKP1g) — lives next to
#: the backup state file, never touched by a dry-run plan.
RESTORE_STATE_FILENAME = ".snapadmin-restore-state.json"


class RestoreError(Exception):
    """Raised when a restore cannot proceed — a bad source, a failed
    checksum, a missing identity, or a failure applying a part."""


@dataclass(frozen=True)
class ResolvedSource:
    """Where a bundle's manifest (and its parts) were fetched from."""

    destination: str | None  # None means "source was a local path"
    manifest_path: Path
    manifest: dict


def parse_source(source: str) -> tuple[str | None, str]:
    """``"sftp:snapadmin-manifest-...json"`` -> ("sftp", name); a local path -> (None, path).

    A local path is anything without a recognised ``<destination>:`` prefix —
    including a bare filename in the current directory, or a path containing
    a colon that isn't one of the four destination names (e.g. a Windows
    drive letter never reaches this code, but a filename genuinely containing
    ':' would fall through to "local", which is the safer default).
    """
    if ":" in source:
        prefix, _, rest = source.partition(":")
        if prefix in ("local", "network", "remote", "sftp", "s3"):
            return prefix, rest
    return None, source


def list_bundles(destination: str | None, config: BackupConfig) -> list[str]:
    """Every manifest filename available at `destination` (or the local dir if None)."""
    if destination is None:
        from snapadmin.backup import list_local
        names = list_local(config)
    else:
        names = LIST_FUNCTIONS[destination](config)
    manifest_prefix = PART_PREFIXES["manifest"]
    return sorted(name for name in names if name.startswith(manifest_prefix))


def _fetch_one(destination: str | None, name: str, target_dir: Path, config: BackupConfig) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    if destination is None:
        source = Path(name)
        if not source.is_file():
            # Also accept a bare filename living in the configured local dir,
            # matching how a name from --list (always bare) round-trips.
            source = config.local_dir / name
        if not source.is_file():
            raise RestoreError(f"{name!r} not found locally.")
        target = target_dir / source.name
        if source != target:
            shutil.copy2(source, target)
        return target
    try:
        return FETCH_FUNCTIONS[destination](name, target_dir, config)
    except BackupError as exc:
        raise RestoreError(str(exc)) from exc


def resolve_source(source: str, target_dir: Path, config: BackupConfig) -> ResolvedSource:
    """Fetch and parse the manifest named by `source`. Does not fetch the parts yet."""
    destination, name = parse_source(source)
    manifest_path = _fetch_one(destination, name, target_dir, config)
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise RestoreError(f"{source!r} is not a readable manifest: {exc}") from exc
    return ResolvedSource(destination=destination, manifest_path=manifest_path, manifest=manifest)


def fetch_parts(
    resolved: ResolvedSource, parts: list[str], target_dir: Path, config: BackupConfig,
) -> dict[str, Path]:
    """Fetch each requested part named in the manifest into target_dir."""
    fetched: dict[str, Path] = {}
    for part in parts:
        entry = resolved.manifest["parts"].get(part)
        if entry is None:
            continue
        fetched[part] = _fetch_one(resolved.destination, entry["filename"], target_dir, config)
    return fetched


def verify_checksums(resolved: ResolvedSource, fetched: dict[str, Path]) -> None:
    """Refuse a bundle whose ciphertext does not match the manifest — a
    truncated or corrupted upload is caught here, before any decrypt attempt
    or write to the live database."""
    for part, path in fetched.items():
        expected = resolved.manifest["parts"][part]["sha256"]
        actual = sha256_file(path)
        if actual != expected:
            raise RestoreError(
                f"Checksum mismatch for part {part!r} ({path.name}): expected {expected}, "
                f"got {actual}. The bundle may be truncated or corrupted — refusing to restore."
            )


def check_version_compatibility(manifest: dict) -> str | None:
    """None if the bundle's snapadmin version is safe to restore, else a warning string.

    A bundle from a *newer* snapadmin than the one running is the risky
    direction (it may contain a part shape this version doesn't understand
    yet) — warn rather than silently attempting it. An older or equal bundle
    is the common case and always fine.
    """
    bundle_version = str(manifest.get("snapadmin_version") or "")
    if not bundle_version or not __version__:
        return None
    bundle_parts = tuple(int(p) for p in bundle_version.split(".")[:3] if p.isdigit())
    current_parts = tuple(int(p) for p in __version__.split(".")[:3] if p.isdigit())
    if bundle_parts and current_parts and bundle_parts > current_parts:
        return (
            f"This bundle was created by snapadmin {bundle_version}, newer than the "
            f"running {__version__}. Restoring may not be fully supported — proceed with caution."
        )
    return None


def identity_required_message(manifest: dict) -> str:
    fingerprints = ", ".join(manifest.get("recipient_fingerprints") or [])
    count = len(manifest.get("recipients") or [])
    return (
        f"This bundle is encrypted to {count} recipient(s) (fingerprints: {fingerprints}); "
        f"pass --identity <path-to-identity-file>."
    )


def _decrypt_if_needed(path: Path, manifest: dict, identity_file: str, config: BackupConfig) -> Path:
    if not manifest.get("encrypted"):
        return path
    if not identity_file:
        raise RestoreError(identity_required_message(manifest))
    out = path.with_name(path.name.removesuffix(".age") + ".dec")
    with open(path, "rb") as reader, open(out, "wb") as writer:
        try:
            crypto.decrypt_stream(
                reader, writer, identity_file,
                backend=config.age_backend, binary_path=config.age_binary_path,
            )
        except crypto.AgeError as exc:
            out.unlink(missing_ok=True)
            raise RestoreError(f"Could not decrypt {path.name}: {exc}") from exc
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Applying a decrypted part to live state
# ─────────────────────────────────────────────────────────────────────────────

def _ungzip_to(src: Path, dst: Path) -> None:
    with gzip.open(src, "rb") as reader, open(dst, "wb") as writer:
        shutil.copyfileobj(reader, writer)


def restore_db(decrypted_path: Path) -> None:
    """Overwrite the live database with the dump at `decrypted_path` (gzipped).

    SQLite: the dump *is* the database file, gzip-compressed — decompress
    straight over the configured file, after closing the current connection
    so nothing still has the old file open. PostgreSQL: the dump is plain SQL
    (``pg_dump`` with no ``-Fc``), so restoring is ``psql`` reading it from
    stdin — existing connections are terminated first so the reload isn't
    fighting live traffic, then the database is dropped and recreated empty.
    Both are inherently **not** live-safe; run this in a maintenance window.
    """
    from django.db import connections

    db = settings.DATABASES["default"]
    engine = db["ENGINE"]
    connections["default"].close()

    if "sqlite" in engine:
        target = str(db["NAME"])
        _ungzip_to(decrypted_path, Path(target))
        return

    if "postgresql" in engine:
        _restore_postgres(db, decrypted_path)
        return

    raise RestoreError(f"Unsupported database engine for restore: {engine}")


def _restore_postgres(db: dict, decrypted_path: Path) -> None:
    host, port = str(db.get("HOST") or "localhost"), str(db.get("PORT") or "5432")
    user, name = str(db.get("USER") or ""), str(db.get("NAME") or "")
    env = {**os.environ, "PGPASSWORD": str(db.get("PASSWORD") or "")}

    def run(args: list[str]) -> None:
        process = subprocess.run(args, capture_output=True, env=env)
        if process.returncode != 0:
            raise RestoreError(f"{args[0]} failed: {process.stderr.decode(errors='replace')}")

    # Terminate other connections, then drop and recreate empty — a restore
    # replaces the database wholesale rather than trying to reconcile with
    # whatever is already there.
    terminate_sql = (
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{name}' AND pid <> pg_backend_pid();"
    )
    run(["psql", "--no-password", "-h", host, "-p", port, "-U", user, "-d", "postgres", "-c", terminate_sql])
    run(["dropdb", "--no-password", "-h", host, "-p", port, "-U", user, name])
    run(["createdb", "--no-password", "-h", host, "-p", port, "-U", user, name])

    with gzip.open(decrypted_path, "rb") as sql:
        process = subprocess.Popen(
            ["psql", "--no-password", "-h", host, "-p", port, "-U", user, "-d", name],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        shutil.copyfileobj(sql, process.stdin)
        process.stdin.close()
        stderr_output = process.stderr.read()
        if process.wait() != 0:
            raise RestoreError(f"psql restore failed: {stderr_output.decode(errors='replace')}")


def restore_media(decrypted_path: Path, media_exclude_existing: bool = False) -> int:
    """Extract the media tar over MEDIA_ROOT. Returns the number of files written.

    Existing files with the same relative path are overwritten; nothing
    outside the tar is deleted — a restore adds/overwrites, it does not prune
    files the current MEDIA_ROOT has that the bundle doesn't.
    """
    media_root_setting = str(getattr(settings, "MEDIA_ROOT", "") or "")
    if not media_root_setting:
        raise RestoreError("MEDIA_ROOT is not configured — cannot restore media.")
    media_root = Path(media_root_setting)
    media_root.mkdir(parents=True, exist_ok=True)

    count = 0
    with gzip.open(decrypted_path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        for member in tar:
            if not member.isfile():
                continue
            # tarfile.data_filter (not the "data" string, backported only to
            # later 3.10/3.11 point releases) works identically on every
            # Python this package supports (>=3.10) — refuses a member that
            # would escape media_root or set setuid/device bits.
            tar.extract(member, path=media_root, filter=tarfile.data_filter)
            count += 1
    return count


def restore_env(decrypted_path: Path, env_file: str) -> None:
    if not env_file:
        raise RestoreError("SNAPADMIN_BACKUP_ENV_FILE is not configured — cannot restore env.")
    shutil.copy2(decrypted_path, env_file)


# ─────────────────────────────────────────────────────────────────────────────
# Planning and orchestration
# ─────────────────────────────────────────────────────────────────────────────

def select_parts(manifest: dict, *, only: list[str] | None, skip: list[str] | None) -> list[str]:
    """Which parts (present in the manifest) a restore run will touch.

    `env` is never included by a bare selection (`only`/`skip` both None) —
    restoring secrets is opt-in, explicit, never a side effect of a plain
    `--confirm` run. Naming `env` in `--only` is exactly how you opt in.
    """
    available = [p for p in RESTORE_PARTS if p in manifest.get("parts", {})]
    if only is not None:
        unknown = set(only) - set(RESTORE_PARTS)
        if unknown:
            raise RestoreError(f"--only names unknown part(s): {sorted(unknown)}")
        selected = [p for p in available if p in only]
    else:
        selected = [p for p in available if p != "env"]
    if skip:
        selected = [p for p in selected if p not in skip]
    return selected


def plan_restore(resolved: ResolvedSource, parts: list[str]) -> list[str]:
    """Human-readable lines describing what --confirm would do. Touches nothing."""
    manifest = resolved.manifest
    lines = [
        f"Bundle: {resolved.manifest_path.name}",
        f"snapadmin {manifest.get('snapadmin_version')} / Django {manifest.get('django_version')} "
        f"/ engine {manifest.get('db_engine')}",
        f"Encrypted: {'yes' if manifest.get('encrypted') else 'no'}"
        + (f" ({len(manifest.get('recipients') or [])} recipient(s))" if manifest.get("encrypted") else ""),
    ]
    warning = check_version_compatibility(manifest)
    if warning:
        lines.append(f"WARNING: {warning}")
    for part in parts:
        entry = manifest["parts"][part]
        if part == "db":
            db_name = settings.DATABASES["default"].get("NAME")
            lines.append(f"  db: {entry['filename']} -> would replace database {db_name!r}")
        elif part == "media":
            lines.append(f"  media: {entry['filename']} -> would extract into MEDIA_ROOT")
        elif part == "env":
            lines.append(f"  env: {entry['filename']} -> would OVERWRITE the configured .env file")
    if not parts:
        lines.append("  (nothing selected)")
    return lines


def _restore_state_path(config: BackupConfig) -> Path:
    return config.local_dir / RESTORE_STATE_FILENAME


def record_restore_run(config: BackupConfig) -> None:
    """Persist that a restore has completed, for the feature-adoption report.

    Written once :func:`perform_restore` finishes without raising — a
    dry-run plan (no ``--confirm``) never reaches this, only a real, applied
    restore does. Lives next to :mod:`snapadmin.backup`'s own state file.
    """
    config.local_dir.mkdir(parents=True, exist_ok=True)
    _restore_state_path(config).write_text(json.dumps({"last_run": timezone.now().isoformat()}))


def last_restore_run(config: BackupConfig) -> str | None:
    """ISO timestamp of the most recently completed restore, or None if none has run."""
    try:
        return json.loads(_restore_state_path(config).read_text()).get("last_run")
    except (OSError, ValueError):
        return None


def perform_restore(
    resolved: ResolvedSource,
    parts: list[str],
    config: BackupConfig,
    *,
    identity_file: str = "",
    before_restore: Callable[[list[str]], None] | None = None,
) -> dict[str, str]:
    """Verify, decrypt and apply every part in `parts`. Only called after --confirm.

    `before_restore` — the pre-restore snapshot hook (#BKP1e) — runs after
    fetch+checksum verification (no point snapshotting before knowing the
    bundle is even usable) but before any part is actually applied.
    """
    results: dict[str, str] = {}
    work_dir = Path(tempfile.mkdtemp(prefix="snapadmin-restore-"))
    try:
        fetched = fetch_parts(resolved, parts, work_dir, config)
        verify_checksums(resolved, fetched)

        if before_restore is not None:
            before_restore(parts)

        for part in parts:
            if part not in fetched:
                continue
            decrypted = _decrypt_if_needed(fetched[part], resolved.manifest, identity_file, config)
            if part == "db":
                restore_db(decrypted)
                results["db"] = "restored"
            elif part == "media":
                count = restore_media(decrypted)
                results["media"] = f"restored ({count} files)"
            elif part == "env":
                restore_env(decrypted, config.env_file)
                results["env"] = "restored"
            logger.info("restore_part_applied", part=part)
        record_restore_run(config)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return results
