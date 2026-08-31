"""
Tests for snapadmin.restore (#BKP1d) — restoring a bundle snapadmin.backup produced:

  source resolution (local / <destination>:<name>) -> manifest fetch -> part selection
  -> checksum verification -> decrypt -> apply to db/media/env, plus the full
  backup -> wipe -> restore round-trip that proves the feature actually works.
"""
import gzip
import json
import shutil
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.test import override_settings

from snapadmin import backup as backup_module
from snapadmin import crypto
from snapadmin import restore as restore_module
from snapadmin.backup import (
    BACKUP_PREFIX,
    build_backup_bundle,
    get_backup_config,
    run_backup,
)
from snapadmin.restore import (
    RESTORE_STATE_FILENAME,
    RestoreError,
    check_version_compatibility,
    fetch_parts,
    identity_required_message,
    last_restore_run,
    list_bundles,
    parse_source,
    perform_restore,
    plan_restore,
    record_restore_run,
    resolve_source,
    restore_db,
    restore_env,
    restore_media,
    select_parts,
    verify_checksums,
)

AGE_INSTALLED = shutil.which("age") is not None

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
def media_root(tmp_path, settings):
    root = tmp_path / "media"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "photo.jpg").write_bytes(b"jpeg-bytes")
    settings.MEDIA_ROOT = str(root)
    return root


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "project.env"
    path.write_text("SECRET_KEY=super-secret\n")
    return path


class _BackupEnv:
    """Keeps SNAPADMIN_BACKUP_* settings active for a test's whole body.

    get_backup_config()/resolve_source()/perform_restore() all read
    SNAPADMIN_BACKUP_LOCAL_DIR at call time — calling them after an
    override_settings block has already exited silently reverts to the
    default local dir. Constructing this once per test and doing everything
    through it keeps the override active end to end.
    """

    def __init__(self, tmp_path, **overrides):
        self.local = tmp_path / "local"
        self.work = tmp_path / "work"
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

    def manifest_source(self):
        return _manifest_path(self.local)

    def resolve(self):
        return resolve_source(self.manifest_source(), self.work, self.config())


def _manifest_name(bundle_dir: Path) -> str:
    """The bare manifest filename in bundle_dir (for --list-style assertions)."""
    from snapadmin.backup import PART_PREFIXES

    names = [p.name for p in bundle_dir.glob(f"{PART_PREFIXES['manifest']}*")]
    assert len(names) == 1
    return names[0]


def _manifest_path(bundle_dir: Path) -> str:
    """The manifest's full path in bundle_dir — a valid local `source` for
    resolve_source() regardless of SNAPADMIN_BACKUP_LOCAL_DIR / cwd at call time."""
    return str(bundle_dir / _manifest_name(bundle_dir))


class FakeS3Client:
    """Minimal stand-in for a boto3 S3 client — the calls a real backup+restore
    round-trip through the s3 destination needs (#BKP1f): upload/download,
    a paginated list, and delete (retention)."""

    shared_objects: dict[str, bytes] = {}

    def __init__(self, **kwargs):
        # A shared reference, not a copy — every client talks to the same
        # bucket, so a write through one instance must be visible to the
        # next one, exactly like real S3.
        self.objects = FakeS3Client.shared_objects

    def upload_file(self, Filename, Bucket, Key):
        self.objects[Key] = Path(Filename).read_bytes()

    def download_file(self, Bucket, Key, Filename):
        if Key not in self.objects:
            raise Exception(f"NoSuchKey: {Key!r}")
        Path(Filename).write_bytes(self.objects[Key])

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys]} if keys else {}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


class FakeBoto3Module:
    @staticmethod
    def client(service_name, **kwargs):
        return FakeS3Client(**kwargs)


@pytest.fixture
def fake_s3(monkeypatch):
    FakeS3Client.shared_objects = {}
    monkeypatch.setattr(backup_module, "_load_boto3", lambda: FakeBoto3Module)
    return FakeBoto3Module


# ─────────────────────────────────────────────────────────────────────────────
# Source parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestParseSource:
    def test_local_path(self):
        assert parse_source("snapadmin-manifest-20260826-000000.json") == (None, "snapadmin-manifest-20260826-000000.json")

    @pytest.mark.parametrize("dest", ["local", "network", "remote", "sftp", "s3"])
    def test_destination_prefix(self, dest):
        assert parse_source(f"{dest}:snapadmin-manifest-x.json") == (dest, "snapadmin-manifest-x.json")

    def test_unrecognised_prefix_treated_as_local(self):
        # A colon that isn't one of the four known destinations falls through
        # to "local" (the safer default) rather than raising.
        assert parse_source("C:/backups/manifest.json") == (None, "C:/backups/manifest.json")


# ─────────────────────────────────────────────────────────────────────────────
# Listing and resolving
# ─────────────────────────────────────────────────────────────────────────────

class TestListAndResolve:
    def test_list_bundles_local(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            names = list_bundles(None, env.config())
        assert len(names) == 1
        assert names[0].startswith("snapadmin-manifest-")

    def test_resolve_source_local_missing_raises(self, tmp_path, sqlite_db):
        config = get_backup_config()
        with pytest.raises(RestoreError, match="not found"):
            resolve_source("nope.json", tmp_path, config)

    def test_resolve_source_unreadable_manifest_raises(self, tmp_path, sqlite_db):
        bad = tmp_path / "snapadmin-manifest-x.json"
        bad.write_text("{not json")
        config = get_backup_config()
        with pytest.raises(RestoreError, match="not a readable manifest"):
            resolve_source(str(bad), tmp_path / "work", config)

    def test_list_and_resolve_from_a_non_local_destination(self, tmp_path, sqlite_db):
        """`network` exercises the same LIST_FUNCTIONS/FETCH_FUNCTIONS branch
        a real remote/sftp destination would, without mocking FTP/SFTP."""
        network = tmp_path / "network"
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_NETWORK_DIR=str(network),
        ):
            run_backup(["network"])
            config = get_backup_config()
            names = list_bundles("network", config)
            assert len(names) == 1
            resolved = resolve_source(f"network:{names[0]}", tmp_path / "work", config)
        assert resolved.destination == "network"
        assert "db" in resolved.manifest["parts"]

    def test_list_and_resolve_from_s3(self, tmp_path, sqlite_db, fake_s3):
        """S3 exercises the exact same LIST_FUNCTIONS/FETCH_FUNCTIONS branch a
        real remote/sftp destination does — the restore-fetch side of #BKP1f's
        wiring requirement, proven end to end through a real backup+restore
        round-trip rather than by inspecting the dicts directly."""
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_S3_BUCKET="my-bucket",
        ):
            run_backup(["s3"])
            config = get_backup_config()
            names = list_bundles("s3", config)
            assert len(names) == 1
            resolved = resolve_source(f"s3:{names[0]}", tmp_path / "work", config)
        assert resolved.destination == "s3"
        assert "db" in resolved.manifest["parts"]

    def test_fetch_one_remote_wraps_backup_error(self, tmp_path, sqlite_db):
        config = get_backup_config()  # SNAPADMIN_BACKUP_NETWORK_DIR unset
        with pytest.raises(RestoreError, match="NETWORK_DIR"):
            resolve_source("network:whatever.json", tmp_path / "work", config)

    def test_resolve_source_reads_manifest(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            resolved = env.resolve()
        assert resolved.destination is None
        assert "db" in resolved.manifest["parts"]


# ─────────────────────────────────────────────────────────────────────────────
# Part selection
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectParts:
    MANIFEST = {"parts": {"db": {}, "media": {}, "env": {}}}

    def test_bare_selection_excludes_env(self):
        assert select_parts(self.MANIFEST, only=None, skip=None) == ["db", "media"]

    def test_only_env_opts_in_explicitly(self):
        assert select_parts(self.MANIFEST, only=["env"], skip=None) == ["env"]

    def test_only_restricts_to_named_parts(self):
        assert select_parts(self.MANIFEST, only=["db"], skip=None) == ["db"]

    def test_skip_removes_parts(self):
        assert select_parts(self.MANIFEST, only=None, skip=["media"]) == ["db"]

    def test_only_unknown_part_raises(self):
        with pytest.raises(RestoreError, match="nonsense"):
            select_parts(self.MANIFEST, only=["nonsense"], skip=None)

    def test_only_part_absent_from_manifest_is_silently_dropped(self):
        manifest = {"parts": {"db": {}}}
        assert select_parts(manifest, only=["db", "media"], skip=None) == ["db"]


# ─────────────────────────────────────────────────────────────────────────────
# Checksum verification
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyChecksums:
    def test_matching_checksum_passes(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            resolved = env.resolve()
            fetched = fetch_parts(resolved, ["db"], env.work, env.config())
            verify_checksums(resolved, fetched)  # must not raise

    def test_corrupted_part_raises(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            resolved = env.resolve()
            fetched = fetch_parts(resolved, ["db"], env.work, env.config())
            fetched["db"].write_bytes(b"corrupted garbage")
            with pytest.raises(RestoreError, match="Checksum mismatch"):
                verify_checksums(resolved, fetched)

    def test_part_absent_from_manifest_is_silently_skipped(self, tmp_path, sqlite_db):
        """fetch_parts() is asked for "media" on a db-only bundle — it has
        nothing to fetch for that part, so it's simply absent from the
        result, not an error (select_parts() already filtered against the
        manifest; this is the same guarantee at the fetch layer)."""
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            resolved = env.resolve()
            fetched = fetch_parts(resolved, ["db", "media"], env.work, env.config())
        assert set(fetched) == {"db"}


# ─────────────────────────────────────────────────────────────────────────────
# Version compatibility and the identity-required message
# ─────────────────────────────────────────────────────────────────────────────

class TestVersionCompatibility:
    """Pinned against a fixed running version, not the real installed one —
    running from a source checkout resolves __version__ to "0.0.0.dev0"
    (snapadmin/__init__.py's PackageNotFoundError fallback), which every
    real release number compares as "newer than"."""

    def test_older_bundle_is_fine(self, monkeypatch):
        monkeypatch.setattr(restore_module, "__version__", "1.0.0")
        assert check_version_compatibility({"snapadmin_version": "0.9.0"}) is None

    def test_same_version_is_fine(self, monkeypatch):
        monkeypatch.setattr(restore_module, "__version__", "1.0.0")
        assert check_version_compatibility({"snapadmin_version": "1.0.0"}) is None

    def test_newer_bundle_warns(self, monkeypatch):
        monkeypatch.setattr(restore_module, "__version__", "1.0.0")
        warning = check_version_compatibility({"snapadmin_version": "999.0.0"})
        assert warning is not None
        assert "999.0.0" in warning

    def test_missing_version_is_fine(self):
        assert check_version_compatibility({}) is None

    def test_non_numeric_version_is_fine(self, monkeypatch):
        monkeypatch.setattr(restore_module, "__version__", "1.0.0")
        assert check_version_compatibility({"snapadmin_version": "not-a-version"}) is None

    def test_empty_running_version_is_fine(self, monkeypatch):
        monkeypatch.setattr(restore_module, "__version__", "")
        assert check_version_compatibility({"snapadmin_version": "1.0.0"}) is None


class TestIdentityRequiredMessage:
    def test_names_recipient_count_and_fingerprints(self):
        manifest = {"recipients": ["age1a", "age1b"], "recipient_fingerprints": ["aaa", "bbb"]}
        message = identity_required_message(manifest)
        assert "2 recipient" in message
        assert "aaa, bbb" in message
        assert "--identity" in message


# ─────────────────────────────────────────────────────────────────────────────
# Applying a decrypted part
# ─────────────────────────────────────────────────────────────────────────────

class TestRestoreDb:
    def test_sqlite_overwrite(self, tmp_path, sqlite_db):
        gz_path = tmp_path / "dump.gz"
        with gzip.open(gz_path, "wb") as f:
            f.write(b"new-sqlite-content")
        restore_db(gz_path)
        assert sqlite_db.read_bytes() == b"new-sqlite-content"

    def test_unsupported_engine_raises(self, tmp_path):
        databases = {"default": {"ENGINE": "django.db.backends.oracle", "NAME": "x"}}
        with override_settings(DATABASES=databases):
            with pytest.raises(RestoreError, match="Unsupported"):
                restore_db(tmp_path / "dump.gz")

    def test_postgres_restore_runs_expected_commands(self, tmp_path, monkeypatch):
        gz_path = tmp_path / "dump.sql.gz"
        with gzip.open(gz_path, "wb") as f:
            f.write(b"CREATE TABLE t (x int);")

        calls = []

        class FakeCompleted:
            def __init__(self, returncode=0, stderr=b""):
                self.returncode = returncode
                self.stderr = stderr

        def fake_run(args, capture_output, env):
            calls.append(args)
            return FakeCompleted()

        class FakePopenProc:
            def __init__(self):
                self.stdin = mock.MagicMock()
                self.stderr = mock.MagicMock(read=lambda: b"")

            def wait(self):
                calls.append(["psql-stdin-wait"])
                return 0

        monkeypatch.setattr(restore_module.subprocess, "run", fake_run)
        monkeypatch.setattr(restore_module.subprocess, "Popen", lambda *a, **k: FakePopenProc())

        databases = {
            "default": {"ENGINE": "django.db.backends.postgresql", "NAME": "snap", "USER": "u"}
        }
        with override_settings(DATABASES=databases):
            restore_db(gz_path)

        assert any(c[0] == "psql" and "pg_terminate_backend" in c[-1] for c in calls if c[0] == "psql")
        assert any(c[0] == "dropdb" for c in calls)
        assert any(c[0] == "createdb" for c in calls)

    def test_postgres_command_failure_raises(self, tmp_path, monkeypatch):
        gz_path = tmp_path / "dump.sql.gz"
        with gzip.open(gz_path, "wb") as f:
            f.write(b"x")

        class FakeCompleted:
            returncode = 1
            stderr = b"boom"

        monkeypatch.setattr(restore_module.subprocess, "run", lambda *a, **k: FakeCompleted())
        databases = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "snap"}}
        with override_settings(DATABASES=databases):
            with pytest.raises(RestoreError, match="boom"):
                restore_db(gz_path)

    def test_postgres_psql_load_failure_raises(self, tmp_path, monkeypatch):
        gz_path = tmp_path / "dump.sql.gz"
        with gzip.open(gz_path, "wb") as f:
            f.write(b"x")

        class FakeCompleted:
            returncode = 0
            stderr = b""

        class FakePopenProc:
            def __init__(self):
                self.stdin = mock.MagicMock()
                self.stderr = mock.MagicMock(read=lambda: b"load failed")

            def wait(self):
                return 1

        monkeypatch.setattr(restore_module.subprocess, "run", lambda *a, **k: FakeCompleted())
        monkeypatch.setattr(restore_module.subprocess, "Popen", lambda *a, **k: FakePopenProc())
        databases = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "snap"}}
        with override_settings(DATABASES=databases):
            with pytest.raises(RestoreError, match="load failed"):
                restore_db(gz_path)


class TestRestoreMedia:
    def test_extracts_files_into_media_root(self, tmp_path, media_root, settings):
        bundle = build_backup_bundle(tmp_path / "out", replace(get_backup_config(), include=["media"]))
        new_root = tmp_path / "restored-media"
        settings.MEDIA_ROOT = str(new_root)
        count = restore_media(bundle["media"])
        assert count == 1
        assert (new_root / "sub" / "photo.jpg").read_bytes() == b"jpeg-bytes"

    def test_missing_media_root_raises(self, tmp_path, settings):
        settings.MEDIA_ROOT = ""
        with pytest.raises(RestoreError, match="MEDIA_ROOT"):
            restore_media(tmp_path / "whatever.tar.gz")

    def test_directory_entries_in_the_tar_are_skipped(self, tmp_path, settings):
        """Defensive: this package's own create_media_bundle() never adds a
        directory member (recursive=False, per-file), but a tar built by
        another tool could — extracting only real files must not choke on one."""
        import tarfile

        tar_path = tmp_path / "media.tar"
        with tarfile.open(tar_path, "w") as tar:
            directory_info = tarfile.TarInfo(name="a-directory")
            directory_info.type = tarfile.DIRTYPE
            tar.addfile(directory_info)
        gz_path = tmp_path / "media.tar.gz"
        with open(tar_path, "rb") as raw, gzip.open(gz_path, "wb") as gz:
            shutil.copyfileobj(raw, gz)

        new_root = tmp_path / "restored-media"
        settings.MEDIA_ROOT = str(new_root)
        count = restore_media(gz_path)
        assert count == 0


class TestRestoreEnv:
    def test_overwrites_env_file(self, tmp_path):
        source = tmp_path / "restored.env"
        source.write_text("SECRET_KEY=restored\n")
        target = tmp_path / "project.env"
        target.write_text("SECRET_KEY=old\n")
        restore_env(source, str(target))
        assert target.read_text() == "SECRET_KEY=restored\n"

    def test_missing_env_file_setting_raises(self, tmp_path):
        with pytest.raises(RestoreError, match="ENV_FILE"):
            restore_env(tmp_path / "x", "")


class TestRestoreState:
    def test_none_when_never_run(self, tmp_path):
        config = replace(get_backup_config(), local_dir=tmp_path)
        assert last_restore_run(config) is None

    def test_record_and_read_back(self, tmp_path):
        config = replace(get_backup_config(), local_dir=tmp_path)
        record_restore_run(config)
        assert last_restore_run(config) is not None

    def test_corrupt_state_file_reads_as_none(self, tmp_path):
        config = replace(get_backup_config(), local_dir=tmp_path)
        (tmp_path / RESTORE_STATE_FILENAME).write_text("{not json")
        assert last_restore_run(config) is None


# ─────────────────────────────────────────────────────────────────────────────
# perform_restore — decryption, identity handling
# ─────────────────────────────────────────────────────────────────────────────

class TestPerformRestore:
    def test_plaintext_bundle_restores_db(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            original_content = sqlite_db.read_bytes()
            sqlite_db.write_bytes(b"corrupted-live-state")
            resolved = env.resolve()
            results = perform_restore(resolved, ["db"], env.config())

        assert results["db"] == "restored"
        assert sqlite_db.read_bytes() == original_content

    def test_records_that_a_restore_ran(self, tmp_path, sqlite_db):
        """The "have you ever restored?" report (#BKP1g) is written only once
        a restore actually completes — never for a dry-run plan, which never
        calls perform_restore() at all."""
        with _BackupEnv(tmp_path) as env:
            config = env.config()
            assert last_restore_run(config) is None
            run_backup(["local"])
            resolved = env.resolve()
            perform_restore(resolved, ["db"], config)
        assert last_restore_run(config) is not None

    def test_part_absent_from_manifest_is_skipped_not_applied(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])  # db-only bundle
            resolved = env.resolve()
            results = perform_restore(resolved, ["db", "media"], env.config())
        assert set(results) == {"db"}

    def test_encrypted_bundle_without_identity_raises_with_helpful_message(
        self, tmp_path, sqlite_db, age_keypair,
    ):
        _identity_path, recipient = age_keypair
        with _BackupEnv(tmp_path, SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient]) as env:
            run_backup(["local"])
            resolved = env.resolve()
            with pytest.raises(RestoreError, match="--identity"):
                perform_restore(resolved, ["db"], env.config())

    def test_encrypted_bundle_with_identity_restores(self, tmp_path, sqlite_db, age_keypair):
        identity_path, recipient = age_keypair
        with _BackupEnv(tmp_path, SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient]) as env:
            run_backup(["local"])
            original_content = sqlite_db.read_bytes()
            sqlite_db.write_bytes(b"corrupted-live-state")
            resolved = env.resolve()
            results = perform_restore(resolved, ["db"], env.config(), identity_file=str(identity_path))

        assert results["db"] == "restored"
        assert sqlite_db.read_bytes() == original_content

    def test_wrong_identity_raises_cleanly(self, tmp_path, sqlite_db, age_keypair):
        _identity_path, recipient = age_keypair
        wrong_identity, _wrong_recipient = _age_keypair()
        wrong_path = tmp_path / "wrong.txt"
        wrong_path.write_text(wrong_identity + "\n")

        with _BackupEnv(tmp_path, SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient]) as env:
            run_backup(["local"])
            resolved = env.resolve()
            with pytest.raises(RestoreError, match="Could not decrypt"):
                perform_restore(resolved, ["db"], env.config(), identity_file=str(wrong_path))

    def test_before_restore_hook_runs_after_verification_before_apply(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            resolved = env.resolve()

            calls = []

            def before_restore(parts):
                calls.append(("snapshot", tuple(parts)))
                assert sqlite_db.exists()  # live state is untouched at this point

            perform_restore(resolved, ["db"], env.config(), before_restore=before_restore)
        assert calls == [("snapshot", ("db",))]

    def test_before_restore_failure_aborts_before_any_part_is_applied(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            original_content = sqlite_db.read_bytes()
            resolved = env.resolve()

            def failing_snapshot(parts):
                raise RuntimeError("snapshot disk full")

            with pytest.raises(RuntimeError, match="snapshot disk full"):
                perform_restore(resolved, ["db"], env.config(), before_restore=failing_snapshot)
        assert sqlite_db.read_bytes() == original_content  # untouched


# ─────────────────────────────────────────────────────────────────────────────
# plan_restore — dry-run output
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanRestore:
    def test_plan_lists_every_selected_part(self, tmp_path, sqlite_db, media_root, env_file, age_keypair):
        _identity_path, recipient = age_keypair
        with _BackupEnv(
            tmp_path, SNAPADMIN_BACKUP_INCLUDE=["db", "media", "env"],
            SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient], SNAPADMIN_BACKUP_ENV_FILE=str(env_file),
        ) as env:
            run_backup(["local"])
            resolved = env.resolve()
            lines = plan_restore(resolved, ["db", "media", "env"])
        joined = "\n".join(lines)
        assert "db:" in joined and "media:" in joined and "env:" in joined
        assert "OVERWRITE" in joined
        assert "Encrypted: yes" in joined

    def test_plan_with_no_parts_says_so(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            resolved = env.resolve()
            lines = plan_restore(resolved, [])
        assert any("nothing selected" in line for line in lines)

    def test_plan_warns_on_newer_bundle_version(self, tmp_path, sqlite_db):
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])
            resolved = env.resolve()
            resolved.manifest["snapadmin_version"] = "999.0.0"
            lines = plan_restore(resolved, ["db"])
        assert any("WARNING" in line for line in lines)


# ─────────────────────────────────────────────────────────────────────────────
# The round-trip test that proves the feature
# ─────────────────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_backup_wipe_restore_sqlite_data_is_identical(self, tmp_path, sqlite_db):
        original_bytes = sqlite_db.read_bytes()
        with _BackupEnv(tmp_path) as env:
            run_backup(["local"])

            # "Wipe": simulate data loss / corruption of the live database.
            sqlite_db.write_bytes(b"")

            resolved = env.resolve()
            results = perform_restore(resolved, ["db"], env.config())

        assert results["db"] == "restored"
        assert sqlite_db.read_bytes() == original_bytes

    @pytest.mark.parametrize("backend", ["pyrage"] + (["binary"] if AGE_INSTALLED else []))
    def test_backup_wipe_restore_encrypted_media_and_env(
        self, backend, tmp_path, sqlite_db, media_root, env_file, age_keypair, settings,
    ):
        identity_path, recipient = age_keypair
        original_db = sqlite_db.read_bytes()
        original_env = env_file.read_text()

        with _BackupEnv(
            tmp_path, SNAPADMIN_BACKUP_INCLUDE=["db", "media", "env"],
            SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient], SNAPADMIN_BACKUP_AGE_BACKEND=backend,
            SNAPADMIN_BACKUP_ENV_FILE=str(env_file),
        ) as env:
            run_backup(["local"])

            # Wipe everything the backup covers.
            sqlite_db.write_bytes(b"")
            shutil.rmtree(media_root)
            env_file.write_text("")

            resolved = env.resolve()
            results = perform_restore(
                resolved, ["db", "media", "env"], env.config(), identity_file=str(identity_path),
            )

        assert results["db"] == "restored"
        assert sqlite_db.read_bytes() == original_db
        assert (media_root / "sub" / "photo.jpg").read_bytes() == b"jpeg-bytes"
        assert env_file.read_text() == original_env


# ─────────────────────────────────────────────────────────────────────────────
# The management command
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRestoreCommand:
    def test_list_with_no_bundles(self, tmp_path, sqlite_db):
        with override_settings(SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path / "empty")):
            from io import StringIO
            out = StringIO()
            call_command("snapadmin_restore", "--list", stdout=out)
        assert "No backup bundles found" in out.getvalue()

    def test_list_shows_manifest(self, tmp_path, sqlite_db):
        local = tmp_path / "local"
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local)):
            run_backup(["local"])
            from io import StringIO
            out = StringIO()
            call_command("snapadmin_restore", "--list", stdout=out)
        assert "snapadmin-manifest-" in out.getvalue()

    def test_requires_source_without_list(self):
        with pytest.raises(CommandError, match="source"):
            call_command("snapadmin_restore")

    def test_dry_run_by_default(self, tmp_path, sqlite_db):
        local = tmp_path / "local"
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local)):
            run_backup(["local"])
            name = _manifest_name(local)
            from io import StringIO
            out = StringIO()
            call_command("snapadmin_restore", str(local / name), stdout=out)
        assert "Dry run" in out.getvalue()
        # Nothing was actually restored — the live db is untouched (still real content).
        assert sqlite_db.read_bytes() != b""

    def test_confirm_performs_restore_and_snapshot(self, tmp_path, sqlite_db):
        local = tmp_path / "local"
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local),
        ):
            run_backup(["local"])
            original = sqlite_db.read_bytes()
            sqlite_db.write_bytes(b"corrupted")
            name = _manifest_name(local)
            from io import StringIO
            out = StringIO()
            call_command("snapadmin_restore", str(local / name), "--confirm", stdout=out)
        assert "Restore complete" in out.getvalue()
        assert "Pre-restore snapshot taken" in out.getvalue()
        assert sqlite_db.read_bytes() == original

    def test_no_snapshot_flag_warns_and_skips_snapshot(self, tmp_path, sqlite_db):
        local = tmp_path / "local"
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local)):
            run_backup(["local"])
            name = _manifest_name(local)
            from io import StringIO
            out = StringIO()
            call_command("snapadmin_restore", str(local / name), "--confirm", "--no-snapshot", stdout=out)
        assert "WITHOUT a pre-restore safety net" in out.getvalue()
        assert "Pre-restore snapshot taken" not in out.getvalue()

    def test_encrypted_bundle_without_identity_warns_in_plan(self, tmp_path, sqlite_db, age_keypair):
        _identity_path, recipient = age_keypair
        local = tmp_path / "local"
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local),
            SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient],
        ):
            run_backup(["local"])
            name = _manifest_name(local)
            from io import StringIO
            out = StringIO()
            call_command("snapadmin_restore", str(local / name), stdout=out)
        assert "--identity" in out.getvalue()

    def test_bad_only_reports_command_error(self, tmp_path, sqlite_db):
        local = tmp_path / "local"
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local)):
            run_backup(["local"])
            name = _manifest_name(local)
            with pytest.raises(CommandError, match="nonsense"):
                call_command("snapadmin_restore", str(local / name), "--only", "nonsense")

    def test_bad_source_reports_command_error(self, sqlite_db):
        with pytest.raises(CommandError, match="not found"):
            call_command("snapadmin_restore", "nope.json")

    def test_list_destination_error_reports_command_error(self, sqlite_db):
        # SNAPADMIN_BACKUP_NETWORK_DIR is unset -> list_remote's own BackupError
        with pytest.raises(CommandError, match="NETWORK_DIR"):
            call_command("snapadmin_restore", "--list", "--destination", "network")

    def test_confirm_failure_reports_command_error(self, tmp_path, sqlite_db, monkeypatch):
        local = tmp_path / "local"
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local)):
            run_backup(["local"])
            name = _manifest_name(local)

            def failing_restore_db(*args, **kwargs):
                raise RestoreError("simulated apply failure")

            monkeypatch.setattr(restore_module, "restore_db", failing_restore_db)
            with pytest.raises(CommandError, match="simulated apply failure"):
                call_command(
                    "snapadmin_restore", str(local / name), "--confirm", "--no-snapshot",
                )
