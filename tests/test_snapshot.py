"""
Tests for snapadmin.snapshot (#BKP1e) — the pre-restore safety net:

  take_snapshot() (reuses the backup builders, its own directory/retention)
  -> list_snapshots() / latest_snapshot_id() -> load_snapshot_manifest(),
  plus the failed-snapshot-aborts-the-restore guarantee and the
  snapadmin_rollback management command end to end.
"""
import json
import shutil
from dataclasses import replace
from io import StringIO
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from snapadmin import crypto
from snapadmin import restore as restore_module
from snapadmin.backup import BackupError, get_backup_config, run_backup
from snapadmin.restore import RestoreError
from snapadmin.snapshot import (
    SnapshotError,
    latest_snapshot_id,
    list_snapshots,
    load_snapshot_manifest,
    snapshot_dir,
    snapshot_keep,
    take_snapshot,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:Overriding setting DATABASES can lead to unexpected behavior"
)


@pytest.fixture
def sqlite_db(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.write_bytes(b"sqlite-payload")
    databases = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(db_file)}}
    with override_settings(DATABASES=databases):
        yield db_file


def _age_keypair():
    pyrage = crypto._load_pyrage()
    identity = pyrage.x25519.Identity.generate()
    return str(identity), str(identity.to_public())


@pytest.fixture
def age_keypair(tmp_path):
    identity, recipient = _age_keypair()
    path = tmp_path / "identity.txt"
    path.write_text(identity + "\n")
    return path, recipient


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "project.env"
    path.write_text("SECRET_KEY=super-secret\n")
    return path


class _BackupEnv:
    """See tests/test_restore.py's identical helper for why this exists —
    keeps SNAPADMIN_BACKUP_*/SNAPADMIN_RESTORE_* settings active for a test's
    whole body, since get_backup_config()/snapshot_dir() both read them at
    call time."""

    def __init__(self, tmp_path, **overrides):
        self.local = tmp_path / "local"
        self._settings = override_settings(
            SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(self.local), **overrides,
        )

    def __enter__(self):
        self._settings.enable()
        return self

    def __exit__(self, *exc_info):
        self._settings.disable()

    def config(self):
        return get_backup_config()


class TestSnapshotDirAndKeep:
    def test_default_dir_is_rollback_subdir_of_local(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            config = env.config()
            assert snapshot_dir(config) == config.local_dir / "rollback"

    def test_explicit_dir_setting_wins(self, tmp_path, sqlite_db):
        custom = tmp_path / "custom-snapshots"
        with _BackupEnv(tmp_path, SNAPADMIN_RESTORE_SNAPSHOT_DIR=str(custom)) as env:
            assert snapshot_dir(env.config()) == custom

    def test_default_keep_is_three(self, sqlite_db):
        assert snapshot_keep() == 3

    def test_explicit_keep_setting(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path, SNAPADMIN_RESTORE_SNAPSHOT_KEEP=5):
            assert snapshot_keep() == 5


class TestTakeSnapshot:
    def test_snapshots_current_db_state(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            snapshot_id = take_snapshot(["db"], env.config())
            run_dir, manifest = load_snapshot_manifest(snapshot_id, env.config())
        assert "db" in manifest["parts"]
        dump_path = run_dir / manifest["parts"]["db"]["filename"]
        assert dump_path.is_file()

    def test_snapshot_id_is_a_timestamp_string(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            snapshot_id = take_snapshot(["db"], env.config())
        assert len(snapshot_id) == 15  # "YYYYMMDD-HHMMSS"
        assert "-" in snapshot_id

    def test_encrypted_when_recipients_configured(self, tmp_path, sqlite_db, age_keypair):
        _identity_path, recipient = age_keypair
        with _BackupEnv(tmp_path, SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient]) as env:
            snapshot_id = take_snapshot(["db"], env.config())
            _run_dir, manifest = load_snapshot_manifest(snapshot_id, env.config())
        assert manifest["encrypted"] is True

    def test_media_and_env_included_when_requested(self, tmp_path, sqlite_db, settings, env_file, age_keypair):
        _identity_path, recipient = age_keypair
        media_root = tmp_path / "media"
        (media_root / "sub").mkdir(parents=True)
        (media_root / "sub" / "photo.jpg").write_bytes(b"jpeg-bytes")
        settings.MEDIA_ROOT = str(media_root)

        # env is always encrypted (the .env fail-closed rule) — a snapshot
        # that includes it needs recipients configured just like a real backup.
        with _BackupEnv(
            tmp_path, SNAPADMIN_BACKUP_ENV_FILE=str(env_file), SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient],
        ) as env:
            snapshot_id = take_snapshot(["db", "media", "env"], env.config())
            _run_dir, manifest = load_snapshot_manifest(snapshot_id, env.config())
        assert set(manifest["parts"]) == {"db", "media", "env"}

    def test_env_without_media_root_or_env_file_is_silently_absent(self, tmp_path, sqlite_db, settings):
        settings.MEDIA_ROOT = ""
        with _BackupEnv(tmp_path) as env:
            snapshot_id = take_snapshot(["db", "media"], env.config())
            _run_dir, manifest = load_snapshot_manifest(snapshot_id, env.config())
        assert set(manifest["parts"]) == {"db"}

    def test_failed_snapshot_raises_and_leaves_no_partial_directory(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            with mock.patch(
                "snapadmin.snapshot.create_db_dump",
                side_effect=BackupError("disk full"),
            ):
                with pytest.raises(SnapshotError, match="disk full"):
                    take_snapshot(["db"], env.config())
            root = snapshot_dir(env.config())
            assert list(root.iterdir()) == [] if root.is_dir() else True

    def test_retention_prunes_oldest_first(self, tmp_path, sqlite_db):
        import datetime

        from django.utils import timezone as timezone_module

        with _BackupEnv(tmp_path, SNAPADMIN_RESTORE_SNAPSHOT_KEEP=2) as env:
            base = timezone_module.now().replace(microsecond=0)
            ids = []
            for i in range(3):
                with mock.patch(
                    "snapadmin.snapshot.timezone.now", return_value=base + datetime.timedelta(seconds=i),
                ):
                    ids.append(take_snapshot(["db"], env.config()))
            snapshots = list_snapshots(env.config())
        kept_ids = {s["id"] for s in snapshots}
        assert len(kept_ids) == 2
        assert ids[0] not in kept_ids  # oldest pruned first

    def test_keep_zero_or_less_disables_pruning(self, tmp_path, sqlite_db):
        import datetime

        from django.utils import timezone as timezone_module

        with _BackupEnv(tmp_path, SNAPADMIN_RESTORE_SNAPSHOT_KEEP=0) as env:
            base = timezone_module.now().replace(microsecond=0)
            for i in range(3):
                with mock.patch(
                    "snapadmin.snapshot.timezone.now", return_value=base + datetime.timedelta(seconds=i),
                ):
                    take_snapshot(["db"], env.config())
            snapshots = list_snapshots(env.config())
        assert len(snapshots) == 3


class TestListSnapshots:
    def test_empty_reads_sensibly(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            assert list_snapshots(env.config()) == []

    def test_lists_newest_first(self, tmp_path, sqlite_db):
        import datetime

        from django.utils import timezone as timezone_module

        with _BackupEnv(tmp_path) as env:
            base = timezone_module.now().replace(microsecond=0)
            with mock.patch("snapadmin.snapshot.timezone.now", return_value=base):
                first = take_snapshot(["db"], env.config())
            with mock.patch(
                "snapadmin.snapshot.timezone.now", return_value=base + datetime.timedelta(seconds=1),
            ):
                second = take_snapshot(["db"], env.config())
            snapshots = list_snapshots(env.config())
        assert [s["id"] for s in snapshots] == [second, first]

    def test_ignores_non_snapshot_directories(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            take_snapshot(["db"], env.config())
            root = snapshot_dir(env.config())
            (root / "not-a-snapshot").mkdir()
            snapshots = list_snapshots(env.config())
        assert len(snapshots) == 1

    def test_corrupt_manifest_is_skipped(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            snapshot_id = take_snapshot(["db"], env.config())
            root = snapshot_dir(env.config())
            (root / snapshot_id / "manifest.json").write_text("{not json")
            assert list_snapshots(env.config()) == []


class TestLatestSnapshotId:
    def test_none_when_empty(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            assert latest_snapshot_id(env.config()) is None

    def test_returns_the_newest(self, tmp_path, sqlite_db):
        import datetime

        from django.utils import timezone as timezone_module

        with _BackupEnv(tmp_path) as env:
            base = timezone_module.now().replace(microsecond=0)
            with mock.patch("snapadmin.snapshot.timezone.now", return_value=base):
                take_snapshot(["db"], env.config())
            with mock.patch(
                "snapadmin.snapshot.timezone.now", return_value=base + datetime.timedelta(seconds=1),
            ):
                second = take_snapshot(["db"], env.config())
            assert latest_snapshot_id(env.config()) == second


class TestLoadSnapshotManifest:
    def test_missing_snapshot_raises(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            with pytest.raises(SnapshotError, match="No snapshot"):
                load_snapshot_manifest("nope", env.config())

    def test_unreadable_manifest_raises(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            snapshot_id = take_snapshot(["db"], env.config())
            root = snapshot_dir(env.config())
            (root / snapshot_id / "manifest.json").write_text("{not json")
            with pytest.raises(SnapshotError, match="unreadable"):
                load_snapshot_manifest(snapshot_id, env.config())


# ─────────────────────────────────────────────────────────────────────────────
# snapadmin_rollback — the management command
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRollbackCommand:
    def test_list_with_no_snapshots(self, tmp_path, sqlite_db):
        with override_settings(SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path / "local")):
            out = StringIO()
            call_command("snapadmin_rollback", "--list", stdout=out)
        assert "No snapshots found" in out.getvalue()

    def test_list_shows_snapshot_parts(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            take_snapshot(["db"], env.config())
            out = StringIO()
            call_command("snapadmin_rollback", "--list", stdout=out)
        assert "parts=db" in out.getvalue()

    def test_no_snapshot_id_and_none_exist_raises(self, tmp_path, sqlite_db):
        with override_settings(SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path / "local")):
            with pytest.raises(CommandError, match="No snapshots"):
                call_command("snapadmin_rollback")

    def test_dry_run_uses_most_recent_snapshot(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            snapshot_id = take_snapshot(["db"], env.config())
            out = StringIO()
            call_command("snapadmin_rollback", stdout=out)
        assert f"Using most recent snapshot: {snapshot_id}" in out.getvalue()
        assert "Dry run" in out.getvalue()

    def test_confirm_restores_the_snapshotted_state(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            original = sqlite_db.read_bytes()
            snapshot_id = take_snapshot(["db"], env.config())
            sqlite_db.write_bytes(b"a bad restore happened")

            out = StringIO()
            call_command("snapadmin_rollback", snapshot_id, "--confirm", stdout=out)
        assert "Rollback to" in out.getvalue()
        assert sqlite_db.read_bytes() == original

    def test_encrypted_snapshot_without_identity_warns(self, tmp_path, sqlite_db, age_keypair):
        _identity_path, recipient = age_keypair
        with _BackupEnv(tmp_path, SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient]) as env:
            snapshot_id = take_snapshot(["db"], env.config())
            out = StringIO()
            call_command("snapadmin_rollback", snapshot_id, stdout=out)
        assert "--identity" in out.getvalue()

    def test_encrypted_snapshot_with_identity_restores(self, tmp_path, sqlite_db, age_keypair):
        identity_path, recipient = age_keypair
        with _BackupEnv(tmp_path, SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient]) as env:
            original = sqlite_db.read_bytes()
            snapshot_id = take_snapshot(["db"], env.config())
            sqlite_db.write_bytes(b"corrupted")

            out = StringIO()
            call_command(
                "snapadmin_rollback", snapshot_id, "--identity", str(identity_path), "--confirm",
                stdout=out,
            )
        assert sqlite_db.read_bytes() == original

    def test_unknown_snapshot_id_reports_command_error(self, tmp_path, sqlite_db):
        with override_settings(SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path / "local")):
            with pytest.raises(CommandError, match="No snapshot"):
                call_command("snapadmin_rollback", "nope")

    def test_confirm_failure_reports_command_error(self, tmp_path, sqlite_db, monkeypatch):
        with _BackupEnv(tmp_path) as env:
            snapshot_id = take_snapshot(["db"], env.config())

            def failing_restore_db(*args, **kwargs):
                raise RestoreError("simulated apply failure")

            monkeypatch.setattr(restore_module, "restore_db", failing_restore_db)
            with pytest.raises(CommandError, match="simulated apply failure"):
                call_command("snapadmin_rollback", snapshot_id, "--confirm")


# ─────────────────────────────────────────────────────────────────────────────
# The end-to-end guarantee: snapadmin_restore takes a snapshot automatically,
# and a failed restore can be undone by rolling back to it.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRestoreThenRollback:
    def test_bad_restore_is_undone_by_rollback_to_the_auto_snapshot(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            original_content = sqlite_db.read_bytes()

            # Corrupt the live db, then restore from the (identical) backup —
            # the auto pre-restore snapshot captures the *corrupted* state,
            # exactly as a real "restore went wrong, undo it" story would.
            sqlite_db.write_bytes(b"corrupted-before-restore")

            from snapadmin.backup import PART_PREFIXES

            manifest_name = next(
                p.name for p in env.local.glob(f"{PART_PREFIXES['manifest']}*")
            )
            out = StringIO()
            call_command(
                "snapadmin_restore", str(env.local / manifest_name), "--confirm", stdout=out,
            )
            assert sqlite_db.read_bytes() == original_content  # the restore itself succeeded

            # Now simulate "the restore was wrong" and roll back to what was
            # live immediately before it ran.
            rollback_out = StringIO()
            call_command("snapadmin_rollback", "--confirm", stdout=rollback_out)
        assert sqlite_db.read_bytes() == b"corrupted-before-restore"
