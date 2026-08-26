"""
snapadmin/snapshot.py

The pre-restore safety net (#BKP1e): before ``snapadmin_restore --confirm`` overwrites
anything, it captures a complete snapshot of the *current* live state of every part it
is about to replace, so a bad restore can be undone with ``snapadmin_rollback``.

Snapshots reuse the exact same dump/bundle builders as a real backup
(:func:`snapadmin.backup.create_db_dump` and friends) — a snapshot *is* a backup, just
one taken automatically, stored separately, and kept for a much shorter time. They get
their own retention (``SNAPADMIN_RESTORE_SNAPSHOT_KEEP``, default 3) so a burst of
restore attempts never competes with the real backup policy for disk, and their own
directory (``SNAPADMIN_RESTORE_SNAPSHOT_DIR``) so they are never mistaken for — or
pruned alongside — real backups.

If the snapshot itself fails, the caller (``snapadmin_restore``) must abort the restore
rather than proceeding on a best-effort basis — :func:`take_snapshot` raises
:class:`SnapshotError` instead of returning a partial result for exactly that reason.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from django.utils import timezone

from snapadmin.backup import (
    BackupConfig,
    BackupError,
    create_db_dump,
    create_encrypted_db_dump,
    create_env_bundle,
    create_media_bundle,
    get_backup_config,
    write_manifest,
)
from snapadmin.conf import get_setting
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

MANIFEST_FILENAME = "manifest.json"


class SnapshotError(Exception):
    """Raised when a snapshot cannot be taken, or a rollback cannot proceed."""


def snapshot_dir(config: BackupConfig) -> Path:
    configured = get_setting("SNAPADMIN_RESTORE_SNAPSHOT_DIR", "") or ""
    if configured:
        return Path(configured)
    return config.local_dir / "rollback"


def snapshot_keep(config: BackupConfig | None = None) -> int:
    return int(get_setting("SNAPADMIN_RESTORE_SNAPSHOT_KEEP", 3))


def take_snapshot(parts: list[str], config: BackupConfig | None = None) -> str:
    """Snapshot the current live state of every part in `parts`. Returns the snapshot id.

    Raises :class:`SnapshotError` (never returns partially) if any part fails
    to snapshot — the caller must treat that as "abort the restore", not
    "proceed anyway". Encrypts each part exactly like a real backup does when
    recipients are configured, since a plaintext rollback snapshot containing
    ``env`` would quietly undo the backup layer's own encryption guarantee.
    """
    config = config or get_backup_config()
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    root = snapshot_dir(config)
    run_dir = root / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    produced: dict[str, Path] = {}
    try:
        for part in parts:
            if part == "db":
                dump = create_encrypted_db_dump(run_dir, config) if config.age_recipients else create_db_dump(run_dir)
                produced["db"] = dump
            elif part == "media":
                media = create_media_bundle(run_dir, config, stamp)
                if media is not None:
                    produced["media"] = media
            elif part == "env":
                env = create_env_bundle(run_dir, config, stamp)
                if env is not None:
                    produced["env"] = env
        manifest = write_manifest(run_dir, config, stamp, produced)
        # write_manifest names it "snapadmin-manifest-<stamp>.json" (the same
        # part-prefix scheme a real backup uses); snapshots additionally keep
        # a fixed-name copy so list_snapshots()/restore_snapshot() don't need
        # to know that naming scheme at all.
        shutil.copy2(manifest, run_dir / MANIFEST_FILENAME)
    except (BackupError, OSError) as exc:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise SnapshotError(f"Pre-restore snapshot failed: {exc}") from exc

    logger.info("restore_snapshot_taken", snapshot_id=stamp, parts=list(produced))
    _prune_snapshots(root, snapshot_keep(config))
    return stamp


def _prune_snapshots(root: Path, keep: int) -> None:
    if not root.is_dir() or keep <= 0:
        return
    snapshots = sorted(p for p in root.iterdir() if p.is_dir())
    for stale in snapshots[:-keep] if keep > 0 else snapshots:
        shutil.rmtree(stale, ignore_errors=True)


def list_snapshots(config: BackupConfig | None = None) -> list[dict]:
    """Every snapshot on disk, newest first, with its id, timestamp and parts."""
    config = config or get_backup_config()
    root = snapshot_dir(config)
    if not root.is_dir():
        return []
    result = []
    for entry in sorted(root.iterdir(), reverse=True):
        manifest_path = entry / MANIFEST_FILENAME
        if not entry.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            continue
        result.append({
            "id": entry.name,
            "timestamp": manifest.get("timestamp", entry.name),
            "parts": sorted(manifest.get("parts", {})),
            "encrypted": bool(manifest.get("encrypted")),
        })
    return result


def load_snapshot_manifest(snapshot_id: str, config: BackupConfig | None = None) -> tuple[Path, dict]:
    config = config or get_backup_config()
    root = snapshot_dir(config)
    run_dir = root / snapshot_id
    manifest_path = run_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise SnapshotError(f"No snapshot {snapshot_id!r} found in {root}.")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"Snapshot {snapshot_id!r}'s manifest is unreadable: {exc}") from exc
    return run_dir, manifest


def latest_snapshot_id(config: BackupConfig | None = None) -> str | None:
    snapshots = list_snapshots(config)
    return snapshots[0]["id"] if snapshots else None
