"""
Tests for the 3-2-1 database backup stack (v0.1.0a5):

  create_db_dump (sqlite / pg_dump) → local / network / remote-FTP destinations
  → per-destination schedules → retention pruning → command + Celery task.
"""

import gzip
import json
import shutil
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from snapadmin import backup as backup_module
from snapadmin import crypto
from snapadmin.backup import (
    BACKUP_PREFIX,
    PART_PREFIXES,
    STATE_FILENAME,
    BackupError,
    build_backup_bundle,
    check_env_requires_encryption,
    create_db_dump,
    create_encrypted_db_dump,
    create_env_bundle,
    create_media_bundle,
    due_destinations,
    get_backup_config,
    run_backup,
    run_due_backups,
    store_local,
    store_network,
    store_remote_ftp,
    store_remote_sftp,
    write_manifest,
)

AGE_INSTALLED = shutil.which("age") is not None

# These tests deliberately swap out DATABASES to exercise different backup
# engines (sqlite / pg_dump); Django's generic "Overriding setting DATABASES"
# warning is expected here and only adds noise to the run.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Overriding setting DATABASES can lead to unexpected behavior"
)


@pytest.fixture
def sqlite_db(tmp_path):
    """A real on-disk SQLite file registered as the default database."""
    db_file = tmp_path / "db.sqlite3"
    db_file.write_bytes(b"sqlite-payload")
    databases = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(db_file)}
    }
    with override_settings(DATABASES=databases):
        yield db_file


def _age_keypair():
    pyrage = crypto._load_pyrage()
    identity = pyrage.x25519.Identity.generate()
    return str(identity), str(identity.to_public())


@pytest.fixture
def age_keypairs(tmp_path):
    """Three independent (identity_file_path, recipient_str) age keypairs."""
    pairs = []
    for i in range(3):
        identity, recipient = _age_keypair()
        path = tmp_path / f"identity{i}.txt"
        path.write_text(identity + "\n")
        pairs.append((path, recipient))
    return pairs


@pytest.fixture
def backup_env(tmp_path, sqlite_db):
    """Enabled backup config staging into tmp dirs."""
    local = tmp_path / "local"
    network = tmp_path / "network"
    with override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        SNAPADMIN_BACKUP_LOCAL_DIR=str(local),
        SNAPADMIN_BACKUP_NETWORK_DIR=str(network),
        SNAPADMIN_BACKUP_KEEP=3,
    ):
        yield {"local": local, "network": network}


class FakePopen:
    """Stand-in for subprocess.Popen streaming a fixed stdout/stderr."""

    def __init__(self, stdout, stderr, returncode):
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)
        self._returncode = returncode

    def wait(self):
        return self._returncode


class FakeFTP:
    """Stand-in for ftplib.FTP capturing every call.

    ``existing``/``contents``/``fail_cwd_once`` seed from the *class-level*
    ``shared_*`` attributes so a test can prime server-side state before the
    code under test constructs its own instance (e.g. list/fetch, which never
    hand the test a reference to construct from) — the same "flip a class
    toggle before the call" pattern ``FakeSSHClient.fail_chdir_once`` already
    uses below.
    """

    instances: list["FakeFTP"] = []
    shared_existing: list[str] = [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]
    shared_contents: dict = {}
    shared_fail_cwd_once = False

    def __init__(self, timeout=None):
        self.timeout = timeout
        self.calls = []
        self.stored = []
        self.deleted = []
        self.existing = list(FakeFTP.shared_existing)
        self.contents = dict(FakeFTP.shared_contents)
        self.fail_cwd_once = FakeFTP.shared_fail_cwd_once
        FakeFTP.instances.append(self)

    def connect(self, host, port):
        self.calls.append(("connect", host, port))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def prot_p(self):
        self.calls.append(("prot_p",))

    def cwd(self, directory):
        if self.fail_cwd_once:
            self.fail_cwd_once = False
            import ftplib
            raise ftplib.error_perm("550 no such directory")
        self.calls.append(("cwd", directory))

    def mkd(self, directory):
        self.calls.append(("mkd", directory))

    def storbinary(self, command, fh):
        self.stored.append(command)
        self.contents[command.split(" ", 1)[1]] = fh.read()

    def retrbinary(self, command, write_fn):
        name = command.split(" ", 1)[1]
        if name not in self.contents:
            import ftplib
            raise ftplib.error_perm(f"550 {name}: No such file")
        write_fn(self.contents[name])

    def nlst(self):
        return self.existing + [cmd.split(" ", 1)[1] for cmd in self.stored]

    def delete(self, name):
        self.deleted.append(name)

    def quit(self):
        self.calls.append(("quit",))


@pytest.fixture
def fake_ftp(monkeypatch):
    FakeFTP.instances = []
    FakeFTP.shared_existing = [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]
    FakeFTP.shared_contents = {}
    FakeFTP.shared_fail_cwd_once = False
    monkeypatch.setattr(backup_module.ftplib, "FTP", FakeFTP)
    monkeypatch.setattr(backup_module.ftplib, "FTP_TLS", FakeFTP)
    return FakeFTP


class FakeSFTP:
    """Stand-in for a paramiko SFTPClient.

    ``existing``/``contents`` seed from the class-level ``shared_*``
    attributes so a test can prime server-side state before the code under
    test opens its own SFTP session (list/fetch never hand the test a
    reference to construct from) — same pattern as ``FakeFTP`` above.
    """

    shared_existing: list[str] = [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]
    shared_contents: dict = {}

    def __init__(self, fail_chdir_once):
        self.fail_chdir_once = fail_chdir_once
        self.put_calls = []
        self.get_calls = []
        self.removed = []
        self.chdir_calls = []
        self.mkdir_calls = []
        self.closed = False
        self.existing = list(FakeSFTP.shared_existing)
        self.contents = dict(FakeSFTP.shared_contents)

    def chdir(self, path):
        if self.fail_chdir_once:
            self.fail_chdir_once = False
            raise IOError("No such file or directory")
        self.chdir_calls.append(path)

    def mkdir(self, path):
        self.mkdir_calls.append(path)

    def put(self, local, remote):
        self.put_calls.append((local, remote))
        self.contents[remote] = Path(local).read_bytes()

    def get(self, remote, local):
        self.get_calls.append((remote, local))
        if remote not in self.contents:
            raise OSError(f"No such file: {remote!r}")
        Path(local).write_bytes(self.contents[remote])

    def listdir(self):
        return self.existing + [remote for _local, remote in self.put_calls]

    def remove(self, name):
        self.removed.append(name)

    def close(self):
        self.closed = True


class FakeSSHClient:
    """Stand-in for paramiko.SSHClient capturing every call."""

    instances: list["FakeSSHClient"] = []
    fail_chdir_once = False  # class-level toggle a test flips before the call
    connect_error: Exception | None = None  # set to raise from connect()

    def __init__(self):
        self.connect_kwargs = None
        self.host_keys_loaded = False
        self.policy = None
        self.sftp = None
        self.closed = False
        FakeSSHClient.instances.append(self)

    def load_system_host_keys(self):
        self.host_keys_loaded = True

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        if FakeSSHClient.connect_error is not None:
            raise FakeSSHClient.connect_error

    def open_sftp(self):
        self.sftp = FakeSFTP(FakeSSHClient.fail_chdir_once)
        return self.sftp

    def close(self):
        self.closed = True


@pytest.fixture
def fake_sftp(monkeypatch):
    import paramiko

    FakeSSHClient.instances = []
    FakeSSHClient.fail_chdir_once = False
    FakeSSHClient.connect_error = None
    FakeSFTP.shared_existing = [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]
    FakeSFTP.shared_contents = {}
    monkeypatch.setattr(paramiko, "SSHClient", FakeSSHClient)
    monkeypatch.setattr(paramiko, "RejectPolicy", lambda: "reject-policy")
    return FakeSSHClient


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def test_config_defaults():
    config = get_backup_config()
    assert config.enabled is False
    assert config.keep == 7
    assert config.local_every_hours == 24
    assert config.network_every_hours == 24
    assert config.remote_every_hours == 168  # offsite weekly by default
    assert config.ftp_port == 21
    assert config.ftp_dir == "/"
    assert config.ftp_tls is False
    # SFTP offsite destination
    assert config.sftp_host == ""
    assert config.sftp_port == 22
    assert config.sftp_dir == "/"
    assert config.sftp_key_file == ""
    assert config.sftp_every_hours == 168
    # AGE encryption — disabled by default, byte-for-byte today's behaviour
    assert config.age_recipients == []
    assert config.age_identity_file == ""
    assert config.age_backend == "auto"
    assert config.age_binary_path == ""


# ─────────────────────────────────────────────────────────────────────────────
# Dump creation
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateDump:
    def test_sqlite_dump_is_gzipped_copy(self, tmp_path, sqlite_db):
        dump = create_db_dump(tmp_path / "out")
        assert dump.name.startswith(BACKUP_PREFIX)
        assert dump.name.endswith(".sqlite3.gz")
        assert gzip.decompress(dump.read_bytes()) == b"sqlite-payload"

    def test_sqlite_in_memory_rejected(self, tmp_path):
        with pytest.raises(BackupError, match="in-memory"):
            create_db_dump(tmp_path)

    def test_postgres_dump_uses_pg_dump(self, tmp_path, monkeypatch):
        recorded = {}

        def fake_popen(command, stdout, stderr, env):
            recorded["command"] = command
            recorded["env"] = env
            return FakePopen(stdout=b"PG SQL", stderr=b"", returncode=0)

        monkeypatch.setattr(backup_module.subprocess, "Popen", fake_popen)
        databases = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "snap", "USER": "u", "PASSWORD": "pw",
                "HOST": "db", "PORT": "5433",
            }
        }
        with override_settings(DATABASES=databases):
            dump = create_db_dump(tmp_path)
        assert dump.name.endswith(".sql.gz")
        # stdout streamed straight into gzip, byte-for-byte identical to the old
        # buffer-then-write path
        assert gzip.decompress(dump.read_bytes()) == b"PG SQL"
        assert recorded["command"][0] == "pg_dump"
        assert recorded["command"][-1] == "snap"
        assert "-h" in recorded["command"] and "db" in recorded["command"]
        assert recorded["env"]["PGPASSWORD"] == "pw"

    def test_postgres_dump_failure_raises_and_cleans_up_partial(self, tmp_path, monkeypatch):
        # pg_dump can emit some bytes on stdout before failing; the streaming
        # writer will have created a partial .gz that must not be left behind.
        monkeypatch.setattr(
            backup_module.subprocess,
            "Popen",
            lambda command, stdout, stderr, env: FakePopen(
                stdout=b"partial dump", stderr=b"connection refused", returncode=1
            ),
        )
        databases = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "snap"}}
        with override_settings(DATABASES=databases):
            with pytest.raises(BackupError, match="connection refused"):
                create_db_dump(tmp_path)
        # the corrupt partial dump was removed
        assert list(tmp_path.glob(f"{BACKUP_PREFIX}*")) == []

    def test_unsupported_engine_raises(self, tmp_path):
        databases = {"default": {"ENGINE": "django.db.backends.oracle", "NAME": "x"}}
        with override_settings(DATABASES=databases):
            with pytest.raises(BackupError, match="Unsupported"):
                create_db_dump(tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# Encrypted dump creation — #BKP1b
# ─────────────────────────────────────────────────────────────────────────────

BACKENDS = ["pyrage"] + (["binary"] if AGE_INSTALLED else [])


class TestProducerThread:
    def test_broken_pipe_on_close_is_swallowed_when_reader_already_gone(self):
        """create_encrypted_db_dump() closes the reader as soon as it is done
        (success or failure) without waiting for the producer thread — if the
        thread's own deferred write-buffer flush (inside its `_write_fh.close()`)
        only then discovers the reader is gone, that must not become an
        unhandled thread exception (pytest fails a run that produces one)."""
        import os as os_module

        read_fd, write_fd = os_module.pipe()
        reader = os_module.fdopen(read_fd, "rb")
        writer = os_module.fdopen(write_fd, "wb")
        reader.close()  # nobody will ever read from this pipe

        def producer(w):
            w.write(b"x" * 100)  # buffered at the Python io layer, not yet flushed

        thread = backup_module._ProducerThread(producer, writer)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        # The producer itself "succeeded" from its own point of view — only the
        # deferred close-time flush failed, and that failure is swallowed, not
        # surfaced as thread.exception.
        assert thread.exception is None


def _fake_pg_dump_popen(monkeypatch, stdout: bytes, stderr: bytes, returncode: int):
    """Mock only the pg_dump invocation; everything else (notably the `age`
    binary backend's own subprocess.run -> Popen call) still runs for real —
    both share the same `subprocess` module object, so a blanket patch would
    also break the binary-backend tests running in the same process."""
    real_popen = backup_module.subprocess.Popen

    def fake_popen(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if command and command[0] == "pg_dump":
            return FakePopen(stdout=stdout, stderr=stderr, returncode=returncode)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(backup_module.subprocess, "Popen", fake_popen)


@pytest.fixture
def out_dir(tmp_path):
    """A target_dir for create_encrypted_db_dump(), kept separate from
    wherever fixtures (age_keypairs, sqlite_db) write their own files, so
    "this directory has nothing in it" assertions are unambiguous."""
    directory = tmp_path / "out"
    directory.mkdir()
    return directory


class TestCreateEncryptedDump:
    def test_filename_has_age_suffix(self, out_dir, sqlite_db, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        config = _with_age(get_backup_config(), recipients=[recipient])
        dump = create_encrypted_db_dump(out_dir, config)
        assert dump.name.startswith(BACKUP_PREFIX)
        assert dump.name.endswith(".sqlite3.gz.age")

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_sqlite_roundtrips(self, backend, out_dir, sqlite_db, age_keypairs):
        identity_path, recipient = age_keypairs[0]
        config = _with_age(get_backup_config(), recipients=[recipient], backend=backend)
        dump = create_encrypted_db_dump(out_dir, config)

        out = out_dir / "decrypted.gz"
        with open(dump, "rb") as reader, open(out, "wb") as writer:
            crypto.decrypt_stream(reader, writer, str(identity_path), backend=backend)
        assert gzip.decompress(out.read_bytes()) == b"sqlite-payload"

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_postgres_roundtrips(self, backend, out_dir, monkeypatch, age_keypairs):
        identity_path, recipient = age_keypairs[0]
        _fake_pg_dump_popen(monkeypatch, stdout=b"PG SQL DUMP", stderr=b"", returncode=0)
        databases = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "snap", "USER": "u", "PASSWORD": "pw",
                "HOST": "db", "PORT": "5433",
            }
        }
        with override_settings(DATABASES=databases):
            config = _with_age(get_backup_config(), recipients=[recipient], backend=backend)
            dump = create_encrypted_db_dump(out_dir, config)
        assert dump.name.endswith(".sql.gz.age")

        out = out_dir / "decrypted.gz"
        with open(dump, "rb") as reader, open(out, "wb") as writer:
            crypto.decrypt_stream(reader, writer, str(identity_path), backend=backend)
        assert gzip.decompress(out.read_bytes()) == b"PG SQL DUMP"

    def test_three_recipients_each_identity_decrypts_alone(self, out_dir, sqlite_db, age_keypairs):
        recipients = [recipient for _path, recipient in age_keypairs]
        config = _with_age(get_backup_config(), recipients=recipients)
        dump = create_encrypted_db_dump(out_dir, config)

        for i, (identity_path, _recipient) in enumerate(age_keypairs):
            out = out_dir / f"decrypted{i}.gz"
            with open(dump, "rb") as reader, open(out, "wb") as writer:
                crypto.decrypt_stream(reader, writer, str(identity_path))
            assert gzip.decompress(out.read_bytes()) == b"sqlite-payload"

    def test_wrong_identity_fails_cleanly(self, out_dir, sqlite_db, age_keypairs, tmp_path):
        _identity_path, recipient = age_keypairs[0]
        config = _with_age(get_backup_config(), recipients=[recipient])
        dump = create_encrypted_db_dump(out_dir, config)

        wrong_identity, _wrong_recipient = _age_keypair()
        wrong_path = tmp_path / "wrong.txt"
        wrong_path.write_text(wrong_identity + "\n")

        with pytest.raises(crypto.AgeError):
            with open(dump, "rb") as reader, open(out_dir / "out.gz", "wb") as writer:
                crypto.decrypt_stream(reader, writer, str(wrong_path))

    def test_sqlite_failure_leaves_no_artefact_at_all(self, out_dir, sqlite_db, age_keypairs):
        """The design's core guarantee: a mid-stream failure leaves no file behind,
        not a partial ciphertext and not a plaintext/plain-gzip one either —
        stronger than "no *readable* dump", the target directory has nothing
        in it at all afterwards."""
        _identity_path, recipient = age_keypairs[0]
        config = _with_age(get_backup_config(), recipients=[recipient])

        def failing_copy(db, writer):
            writer.write(b"partial garbage")
            raise BackupError("simulated mid-stream failure")

        with mock.patch.object(backup_module, "_copy_sqlite_into", failing_copy):
            with pytest.raises(BackupError, match="simulated mid-stream failure"):
                create_encrypted_db_dump(out_dir, config)

        assert list(out_dir.iterdir()) == []

    def test_postgres_failure_leaves_no_artefact_at_all(self, out_dir, monkeypatch, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        _fake_pg_dump_popen(monkeypatch, stdout=b"partial dump", stderr=b"connection refused", returncode=1)
        databases = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "snap"}}
        with override_settings(DATABASES=databases):
            config = _with_age(get_backup_config(), recipients=[recipient])
            with pytest.raises(BackupError, match="connection refused"):
                create_encrypted_db_dump(out_dir, config)

        assert list(out_dir.iterdir()) == []

    def test_unsupported_engine_raises_before_any_pipe_work(self, out_dir, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        databases = {"default": {"ENGINE": "django.db.backends.oracle", "NAME": "x"}}
        with override_settings(DATABASES=databases):
            config = _with_age(get_backup_config(), recipients=[recipient])
            with pytest.raises(BackupError, match="Unsupported"):
                create_encrypted_db_dump(out_dir, config)
        assert list(out_dir.iterdir()) == []

    def test_missing_backend_dependency_raises_improperly_configured(self, out_dir, sqlite_db, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        config = _with_age(
            get_backup_config(), recipients=[recipient], backend="binary", binary_path="/no/such/age"
        )
        with pytest.raises(ImproperlyConfigured, match="PATH"):
            create_encrypted_db_dump(out_dir, config)
        assert list(out_dir.iterdir()) == []


def _with_age(config, *, recipients, backend="auto", binary_path=""):
    from dataclasses import replace

    return replace(config, age_recipients=recipients, age_backend=backend, age_binary_path=binary_path)


def _with_bundle(config, **overrides):
    from dataclasses import replace

    return replace(config, **overrides)


# ─────────────────────────────────────────────────────────────────────────────
# Bundle parts: media, env, manifest — #BKP1c
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def media_root(tmp_path, settings):
    """A populated MEDIA_ROOT with a nested file and a to-be-excluded file."""
    root = tmp_path / "media"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "photo.jpg").write_bytes(b"jpeg-bytes")
    (root / "cache.tmp").write_bytes(b"scratch")
    settings.MEDIA_ROOT = str(root)
    return root


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "project.env"
    path.write_text("SECRET_KEY=super-secret\nDB_PASSWORD=hunter2\n")
    return path


class TestCreateMediaBundle:
    def test_missing_media_root_returns_none(self, out_dir, settings):
        settings.MEDIA_ROOT = str(out_dir / "does-not-exist")
        config = get_backup_config()
        assert create_media_bundle(out_dir, config, "20260826-000000") is None

    def test_unset_media_root_returns_none(self, out_dir, settings):
        settings.MEDIA_ROOT = ""
        config = get_backup_config()
        assert create_media_bundle(out_dir, config, "20260826-000000") is None

    def test_tars_files_and_applies_excludes(self, out_dir, media_root):
        config = _with_bundle(get_backup_config(), media_exclude=["*.tmp"])
        bundle = create_media_bundle(out_dir, config, "20260826-000000")
        assert bundle.name == f"{PART_PREFIXES['media']}20260826-000000.tar.gz"

        import tarfile
        with tarfile.open(bundle, "r:gz") as tar:
            names = tar.getnames()
        assert "sub/photo.jpg" in names
        assert "cache.tmp" not in names

    def test_no_excludes_includes_everything(self, out_dir, media_root):
        config = get_backup_config()
        bundle = create_media_bundle(out_dir, config, "20260826-000000")
        import tarfile
        with tarfile.open(bundle, "r:gz") as tar:
            names = set(tar.getnames())
        assert names == {"sub/photo.jpg", "cache.tmp"}

    def test_unreadable_file_is_skipped_with_warning_not_aborted(self, out_dir, media_root):
        config = get_backup_config()
        # Simulate one file becoming unreadable mid-walk: patch TarFile.add to
        # raise only for the target file, so the tar step must skip just it.
        target = media_root / "sub" / "photo.jpg"
        import tarfile as tarfile_module

        original_tarfile_add = tarfile_module.TarFile.add

        def flaky_add(self, name, arcname=None, recursive=True, **kwargs):
            if str(name) == str(target):
                raise OSError("permission denied")
            return original_tarfile_add(self, name, arcname=arcname, recursive=recursive, **kwargs)

        with mock.patch.object(tarfile_module.TarFile, "add", flaky_add):
            bundle = create_media_bundle(out_dir, config, "20260826-000000")

        assert bundle is not None
        with tarfile_module.open(bundle, "r:gz") as tar:
            names = set(tar.getnames())
        assert "sub/photo.jpg" not in names
        assert "cache.tmp" in names

    def test_size_guard_warns_past_threshold_but_does_not_abort(self, out_dir, media_root, caplog):
        config = _with_bundle(get_backup_config(), media_size_warning_bytes=1)
        bundle = create_media_bundle(out_dir, config, "20260826-000000")
        assert bundle is not None  # never aborts, only warns

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_encrypted_media_bundle_roundtrips(self, backend, out_dir, media_root, age_keypairs):
        identity_path, recipient = age_keypairs[0]
        config = _with_bundle(
            get_backup_config(), age_recipients=[recipient], age_backend=backend,
        )
        bundle = create_media_bundle(out_dir, config, "20260826-000000")
        assert bundle.name.endswith(".tar.gz.age")

        out = out_dir / "decrypted.tar.gz"
        with open(bundle, "rb") as reader, open(out, "wb") as writer:
            crypto.decrypt_stream(reader, writer, str(identity_path), backend=backend)
        import tarfile
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())
        assert names == {"sub/photo.jpg", "cache.tmp"}

    def test_encrypted_media_failure_leaves_no_artefact(self, out_dir, media_root, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        config = _with_bundle(get_backup_config(), age_recipients=[recipient])

        def failing_tar(*args, **kwargs):
            raise BackupError("simulated failure")

        with mock.patch.object(backup_module, "_tar_media_into", failing_tar):
            with pytest.raises(Exception):
                create_media_bundle(out_dir, config, "20260826-000000")
        assert list(out_dir.iterdir()) == []

    def test_unencrypted_media_failure_leaves_no_artefact(self, out_dir, media_root):
        config = get_backup_config()

        def failing_tar(*args, **kwargs):
            raise BackupError("simulated failure")

        with mock.patch.object(backup_module, "_tar_media_into", failing_tar):
            with pytest.raises(BackupError, match="simulated failure"):
                create_media_bundle(out_dir, config, "20260826-000000")
        assert list(out_dir.iterdir()) == []


class TestCheckEnvRequiresEncryption:
    def test_env_not_included_never_raises(self):
        config = get_backup_config()
        check_env_requires_encryption(config)  # must not raise

    def test_env_included_with_recipients_ok(self):
        config = _with_bundle(get_backup_config(), include=["db", "env"], age_recipients=["age1x"])
        check_env_requires_encryption(config)  # must not raise

    def test_env_included_without_recipients_raises(self):
        config = _with_bundle(get_backup_config(), include=["env"], age_recipients=[])
        with pytest.raises(BackupError, match="env"):
            check_env_requires_encryption(config)


class TestCreateEnvBundle:
    def test_missing_env_file_returns_none(self, out_dir, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        config = _with_bundle(
            get_backup_config(), include=["env"], age_recipients=[recipient],
            env_file=str(out_dir / "does-not-exist.env"),
        )
        assert create_env_bundle(out_dir, config, "20260826-000000") is None

    def test_encrypts_env_file(self, out_dir, env_file, age_keypairs):
        identity_path, recipient = age_keypairs[0]
        config = _with_bundle(
            get_backup_config(), include=["env"], age_recipients=[recipient],
            env_file=str(env_file),
        )
        bundle = create_env_bundle(out_dir, config, "20260826-000000")
        assert bundle.name == f"{PART_PREFIXES['env']}20260826-000000.age"
        assert b"SECRET_KEY" not in bundle.read_bytes()  # never plaintext on disk

        out = out_dir / "decrypted.env"
        with open(bundle, "rb") as reader, open(out, "wb") as writer:
            crypto.decrypt_stream(reader, writer, str(identity_path))
        assert out.read_text() == env_file.read_text()

    def test_refuses_without_recipients_even_if_called_directly(self, out_dir, env_file):
        """The runtime guard fires even if a caller reaches this function some
        other way — recipients configured then cleared without a restart."""
        config = _with_bundle(
            get_backup_config(), include=["env"], age_recipients=[], env_file=str(env_file),
        )
        with pytest.raises(BackupError, match="env"):
            create_env_bundle(out_dir, config, "20260826-000000")
        assert list(out_dir.iterdir()) == []

    def test_encryption_failure_leaves_no_artefact(self, out_dir, env_file, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        config = _with_bundle(
            get_backup_config(), include=["env"], age_recipients=[recipient],
            env_file=str(env_file), age_backend="binary", age_binary_path="/no/such/age",
        )
        with pytest.raises(ImproperlyConfigured):
            create_env_bundle(out_dir, config, "20260826-000000")
        assert list(out_dir.iterdir()) == []


class TestWriteManifest:
    def test_unencrypted_manifest_lists_parts_and_checksums(self, out_dir, sqlite_db):
        config = get_backup_config()
        dump = create_db_dump(out_dir)
        manifest_path = write_manifest(out_dir, config, "20260826-000000", {"db": dump})
        data = json.loads(manifest_path.read_text())

        assert data["encrypted"] is False
        assert data["recipients"] == []
        assert data["parts"]["db"]["filename"] == dump.name
        assert data["parts"]["db"]["sha256"] == backup_module.sha256_file(dump)
        assert "snapadmin_restore" in data["restore_hint"]
        assert "--identity" not in data["restore_hint"]

    def test_encrypted_manifest_lists_recipients_and_fingerprints(self, out_dir, sqlite_db, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        config = _with_age(get_backup_config(), recipients=[recipient])
        dump = create_encrypted_db_dump(out_dir, config)
        manifest_path = write_manifest(out_dir, config, "20260826-000000", {"db": dump})
        data = json.loads(manifest_path.read_text())

        assert data["encrypted"] is True
        assert data["recipients"] == [recipient]
        assert data["recipient_fingerprints"] == [crypto.fingerprint(recipient)]
        assert "--identity" in data["restore_hint"]

    def test_manifest_is_never_encrypted_even_with_recipients(self, out_dir, sqlite_db, age_keypairs):
        """Readable without an identity — for --list and the "pass --identity" message."""
        _identity_path, recipient = age_keypairs[0]
        config = _with_age(get_backup_config(), recipients=[recipient])
        dump = create_encrypted_db_dump(out_dir, config)
        manifest_path = write_manifest(out_dir, config, "20260826-000000", {"db": dump})
        json.loads(manifest_path.read_text())  # plain JSON, no decryption needed


class TestBuildBackupBundle:
    def test_default_include_produces_db_and_manifest_only(self, out_dir, sqlite_db):
        config = get_backup_config()
        parts = build_backup_bundle(out_dir, config)
        assert set(parts) == {"db", "manifest"}
        assert parts["db"].name.startswith(BACKUP_PREFIX)
        assert parts["db"].name.endswith(".sqlite3.gz")  # byte-identical naming to today

    def test_db_bytes_are_unchanged_from_the_old_direct_call(self, out_dir, sqlite_db):
        """The default bundle's db part is byte-for-byte what create_db_dump()
        alone has always produced — this is the "today's behaviour stays the
        default" guarantee, pinned at the bundle level."""
        direct = create_db_dump(out_dir / "direct")
        config = get_backup_config()
        parts = build_backup_bundle(out_dir / "bundle", config)
        assert parts["db"].read_bytes() == direct.read_bytes()

    def test_media_and_env_included_produces_all_four_parts(self, out_dir, sqlite_db, media_root, env_file, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        config = _with_bundle(
            get_backup_config(), include=["db", "media", "env"],
            age_recipients=[recipient], env_file=str(env_file),
        )
        parts = build_backup_bundle(out_dir, config)
        assert set(parts) == {"db", "media", "env", "manifest"}

    def test_missing_media_and_env_are_simply_absent_not_erroring(self, out_dir, sqlite_db, settings):
        settings.MEDIA_ROOT = ""
        config = _with_bundle(get_backup_config(), include=["db", "media"])
        parts = build_backup_bundle(out_dir, config)
        assert set(parts) == {"db", "manifest"}

    def test_env_without_recipients_refuses_before_any_part_is_built(self, out_dir, sqlite_db):
        config = _with_bundle(get_backup_config(), include=["env"], age_recipients=[])
        with pytest.raises(BackupError, match="env"):
            build_backup_bundle(out_dir, config)
        assert list(out_dir.iterdir()) == []  # refused before db (or anything) was even touched

    def test_unknown_part_name_raises(self, out_dir, sqlite_db):
        config = _with_bundle(get_backup_config(), include=["nonsense"])
        with pytest.raises(BackupError, match="nonsense"):
            build_backup_bundle(out_dir, config)


# ─────────────────────────────────────────────────────────────────────────────
# Destinations + retention
# ─────────────────────────────────────────────────────────────────────────────

class TestPartPrefixFor:
    def test_recognizes_every_known_part_prefix(self):
        for name, prefix in PART_PREFIXES.items():
            assert backup_module._part_prefix_for(f"{prefix}20260826-000000.ext") == prefix

    def test_unknown_prefix_falls_back_to_db(self):
        """Defensive fallback for a filename that predates PART_PREFIXES ever
        existing, or one placed by something other than this module — treated
        as the db prefix so it is neither silently ignored nor mis-pruned."""
        assert backup_module._part_prefix_for("some-other-file.txt") == BACKUP_PREFIX


class TestDestinations:
    def test_store_local_copies_and_prunes(self, tmp_path, backup_env):
        config = get_backup_config()
        for i in range(5):
            source = tmp_path / f"{BACKUP_PREFIX}2026010{i}-000000.sqlite3.gz"
            source.write_bytes(b"x")
            store_local(source, config)
        kept = sorted(p.name for p in backup_env["local"].glob(f"{BACKUP_PREFIX}*"))
        assert len(kept) == 3  # SNAPADMIN_BACKUP_KEEP=3
        assert kept[0].startswith(f"{BACKUP_PREFIX}20260102")  # oldest two pruned

    def test_store_network_requires_configuration(self, tmp_path, sqlite_db):
        source = tmp_path / f"{BACKUP_PREFIX}x.gz"
        source.write_bytes(b"x")
        with pytest.raises(BackupError, match="NETWORK_DIR"):
            store_network(source, get_backup_config())

    def test_store_network_copies(self, tmp_path, backup_env):
        source = tmp_path / f"{BACKUP_PREFIX}20260101-000000.sqlite3.gz"
        source.write_bytes(b"net")
        location = store_network(source, get_backup_config())
        assert Path(location).read_bytes() == b"net"
        assert Path(location).parent == backup_env["network"]

    def test_store_remote_requires_configuration(self, tmp_path, sqlite_db):
        source = tmp_path / f"{BACKUP_PREFIX}x.gz"
        source.write_bytes(b"x")
        with pytest.raises(BackupError, match="FTP_HOST"):
            store_remote_ftp(source, get_backup_config())

    @override_settings(
        SNAPADMIN_BACKUP_FTP_HOST="backup.example.com",
        SNAPADMIN_BACKUP_FTP_USER="u",
        SNAPADMIN_BACKUP_FTP_PASSWORD="pw",
        SNAPADMIN_BACKUP_FTP_DIR="/dumps",
        SNAPADMIN_BACKUP_KEEP=1,
    )
    def test_store_remote_uploads_and_prunes(self, tmp_path, fake_ftp):
        source = tmp_path / f"{BACKUP_PREFIX}20260101-000000.sql.gz"
        source.write_bytes(b"x")
        location = store_remote_ftp(source, get_backup_config())
        ftp = fake_ftp.instances[0]
        assert ("connect", "backup.example.com", 21) in ftp.calls
        assert ("login", "u", "pw") in ftp.calls
        assert ("cwd", "/dumps") in ftp.calls
        assert ftp.stored == [f"STOR {source.name}"]
        # keep=1 → the pre-existing older dump is deleted
        assert ftp.deleted == [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]
        assert ("quit",) in ftp.calls
        assert location == f"ftp://backup.example.com:21/dumps/{source.name}"

    @override_settings(
        SNAPADMIN_BACKUP_FTP_HOST="backup.example.com",
        SNAPADMIN_BACKUP_FTP_TLS=True,
    )
    def test_store_remote_tls_and_missing_dir(self, tmp_path, fake_ftp):
        source = tmp_path / f"{BACKUP_PREFIX}20260101-000000.sql.gz"
        source.write_bytes(b"x")
        # First cwd fails → mkd + retry
        original_init = FakeFTP.__init__

        def failing_init(self, timeout=None):
            original_init(self, timeout=timeout)
            self.fail_cwd_once = True

        FakeFTP.__init__ = failing_init
        try:
            store_remote_ftp(source, get_backup_config())
        finally:
            FakeFTP.__init__ = original_init
        ftp = fake_ftp.instances[0]
        assert ("prot_p",) in ftp.calls  # FTPS branch
        assert ("mkd", "/") in ftp.calls
        assert ("cwd", "/") in ftp.calls

    def test_store_sftp_requires_configuration(self, tmp_path, sqlite_db):
        source = tmp_path / f"{BACKUP_PREFIX}x.gz"
        source.write_bytes(b"x")
        with pytest.raises(BackupError, match="SFTP_HOST"):
            store_remote_sftp(source, get_backup_config())

    @override_settings(
        SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com",
        SNAPADMIN_BACKUP_SFTP_USER="u",
        SNAPADMIN_BACKUP_SFTP_PASSWORD="pw",
        SNAPADMIN_BACKUP_SFTP_DIR="/dumps",
        SNAPADMIN_BACKUP_KEEP=1,
    )
    def test_store_sftp_uploads_and_prunes(self, tmp_path, fake_sftp):
        source = tmp_path / f"{BACKUP_PREFIX}20260101-000000.sql.gz"
        source.write_bytes(b"x")
        location = store_remote_sftp(source, get_backup_config())
        client = fake_sftp.instances[0]
        # host-key verification wired up, password auth (no key_filename)
        assert client.host_keys_loaded is True
        # unknown host keys are rejected, not trust-on-first-use (MITM hardening)
        assert client.policy == "reject-policy"
        assert client.connect_kwargs["hostname"] == "offsite.example.com"
        assert client.connect_kwargs["port"] == 22
        assert client.connect_kwargs["password"] == "pw"
        assert "key_filename" not in client.connect_kwargs
        sftp = client.sftp
        assert sftp.chdir_calls == ["/dumps"]
        assert sftp.put_calls == [(str(source), source.name)]
        # keep=1 → the pre-existing older dump is removed
        assert sftp.removed == [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]
        assert sftp.closed is True
        assert client.closed is True
        assert location == f"sftp://offsite.example.com:22/dumps/{source.name}"

    @override_settings(
        SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com",
        SNAPADMIN_BACKUP_SFTP_KEY_FILE="/keys/id_ed25519",
    )
    def test_store_sftp_key_auth_and_missing_dir(self, tmp_path, fake_sftp):
        source = tmp_path / f"{BACKUP_PREFIX}20260101-000000.sql.gz"
        source.write_bytes(b"x")
        # First chdir fails → mkdir + retry
        FakeSSHClient.fail_chdir_once = True
        store_remote_sftp(source, get_backup_config())
        client = fake_sftp.instances[0]
        assert client.connect_kwargs["key_filename"] == "/keys/id_ed25519"
        assert "password" not in client.connect_kwargs  # key auth branch
        assert client.sftp.mkdir_calls == ["/"]
        assert client.sftp.chdir_calls == ["/"]

    @override_settings(SNAPADMIN_BACKUP_SFTP_HOST="unknown.example.com")
    def test_store_sftp_unknown_host_rejected(self, tmp_path, fake_sftp):
        # RejectPolicy makes paramiko raise SSHException for a host whose key is
        # not already in known_hosts; the store function must propagate it.
        import paramiko

        source = tmp_path / f"{BACKUP_PREFIX}20260101-000000.sql.gz"
        source.write_bytes(b"x")
        FakeSSHClient.connect_error = paramiko.SSHException(
            "Server 'unknown.example.com' not found in known_hosts"
        )
        with pytest.raises(paramiko.SSHException, match="known_hosts"):
            store_remote_sftp(source, get_backup_config())
        # nothing was uploaded to the untrusted host
        assert fake_sftp.instances[0].sftp is None


# ─────────────────────────────────────────────────────────────────────────────
# Fetching — the restore-side mirror of storing — #BKP1d
# ─────────────────────────────────────────────────────────────────────────────

class TestFetching:
    def test_list_local_empty_and_missing_dir(self, tmp_path, sqlite_db):
        config = _with_bundle(get_backup_config(), local_dir=tmp_path / "nope")
        assert backup_module.list_local(config) == []

    def test_list_and_fetch_local(self, tmp_path, backup_env):
        source = backup_env["local"] / f"{BACKUP_PREFIX}20260101-000000.sql.gz"
        backup_env["local"].mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"dbdata")
        config = get_backup_config()
        assert source.name in backup_module.list_local(config)

        dest_dir = tmp_path / "restore-work"
        dest_dir.mkdir()
        fetched = backup_module.fetch_local(source.name, dest_dir, config)
        assert fetched.read_bytes() == b"dbdata"

    def test_fetch_local_missing_raises(self, tmp_path, backup_env):
        config = get_backup_config()
        with pytest.raises(BackupError, match="not found"):
            backup_module.fetch_local("nope.gz", tmp_path, config)

    def test_list_network_requires_configuration(self, sqlite_db):
        with pytest.raises(BackupError, match="NETWORK_DIR"):
            backup_module.list_network(get_backup_config())

    def test_list_network_missing_dir_reads_empty(self, tmp_path, sqlite_db):
        config = _with_bundle(get_backup_config(), network_dir=str(tmp_path / "nope"))
        assert backup_module.list_network(config) == []

    def test_list_and_fetch_network(self, tmp_path, backup_env):
        source = backup_env["network"] / f"{BACKUP_PREFIX}20260101-000000.sql.gz"
        backup_env["network"].mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"netdata")
        config = get_backup_config()
        assert source.name in backup_module.list_network(config)

        dest_dir = tmp_path / "restore-work"
        dest_dir.mkdir()
        fetched = backup_module.fetch_network(source.name, dest_dir, config)
        assert fetched.read_bytes() == b"netdata"

    def test_fetch_network_missing_raises(self, tmp_path, backup_env):
        config = get_backup_config()
        with pytest.raises(BackupError, match="not found"):
            backup_module.fetch_network("nope.gz", tmp_path, config)

    def test_fetch_network_requires_configuration(self, tmp_path, sqlite_db):
        with pytest.raises(BackupError, match="NETWORK_DIR"):
            backup_module.fetch_network("x.gz", tmp_path, get_backup_config())

    @override_settings(SNAPADMIN_BACKUP_FTP_HOST="backup.example.com", SNAPADMIN_BACKUP_FTP_TLS=True)
    def test_list_and_fetch_remote_ftp_tls(self, tmp_path, fake_ftp):
        config = get_backup_config()
        FakeFTP.shared_contents = {f"{BACKUP_PREFIX}20260101-000000.sql.gz": b"ftpsdata"}
        FakeFTP.shared_existing = [f"{BACKUP_PREFIX}20260101-000000.sql.gz"]

        names = backup_module.list_remote_ftp(config)
        assert f"{BACKUP_PREFIX}20260101-000000.sql.gz" in names
        fetched = backup_module.fetch_remote_ftp(
            f"{BACKUP_PREFIX}20260101-000000.sql.gz", tmp_path, config,
        )
        assert fetched.read_bytes() == b"ftpsdata"
        assert ("prot_p",) in fake_ftp.instances[-1].calls

    def test_fetch_remote_ftp_requires_configuration(self, tmp_path, sqlite_db):
        with pytest.raises(BackupError, match="FTP_HOST"):
            backup_module.fetch_remote_ftp("x.gz", tmp_path, get_backup_config())

    @override_settings(SNAPADMIN_BACKUP_FTP_HOST="backup.example.com")
    def test_list_and_fetch_remote_ftp(self, tmp_path, fake_ftp):
        config = get_backup_config()
        FakeFTP.shared_contents = {f"{BACKUP_PREFIX}20260101-000000.sql.gz": b"ftpdata"}
        FakeFTP.shared_existing = [f"{BACKUP_PREFIX}20260101-000000.sql.gz"]

        names = backup_module.list_remote_ftp(config)
        assert f"{BACKUP_PREFIX}20260101-000000.sql.gz" in names

        fetched = backup_module.fetch_remote_ftp(
            f"{BACKUP_PREFIX}20260101-000000.sql.gz", tmp_path, config,
        )
        assert fetched.read_bytes() == b"ftpdata"

    @override_settings(SNAPADMIN_BACKUP_FTP_HOST="backup.example.com")
    def test_fetch_remote_ftp_missing_raises(self, tmp_path, fake_ftp):
        config = get_backup_config()
        with pytest.raises(BackupError, match="Could not fetch"):
            backup_module.fetch_remote_ftp("nope.gz", tmp_path, config)

    def test_list_remote_ftp_requires_configuration(self, sqlite_db):
        with pytest.raises(BackupError, match="FTP_HOST"):
            backup_module.list_remote_ftp(get_backup_config())

    @override_settings(SNAPADMIN_BACKUP_FTP_HOST="backup.example.com")
    def test_list_remote_ftp_missing_dir_reads_empty(self, fake_ftp):
        FakeFTP.shared_fail_cwd_once = True
        assert backup_module.list_remote_ftp(get_backup_config()) == []

    def test_list_remote_sftp_requires_configuration(self, sqlite_db):
        with pytest.raises(BackupError, match="SFTP_HOST"):
            backup_module.list_remote_sftp(get_backup_config())

    @override_settings(SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com")
    def test_list_and_fetch_remote_sftp(self, tmp_path, fake_sftp):
        config = get_backup_config()
        FakeSFTP.shared_contents = {f"{BACKUP_PREFIX}20260101-000000.sql.gz": b"sftpdata"}
        FakeSFTP.shared_existing = [f"{BACKUP_PREFIX}20260101-000000.sql.gz"]

        names = backup_module.list_remote_sftp(config)
        assert f"{BACKUP_PREFIX}20260101-000000.sql.gz" in names

        fetched = backup_module.fetch_remote_sftp(
            f"{BACKUP_PREFIX}20260101-000000.sql.gz", tmp_path, config,
        )
        assert fetched.read_bytes() == b"sftpdata"

    @override_settings(SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com")
    def test_fetch_remote_sftp_missing_raises(self, tmp_path, fake_sftp):
        config = get_backup_config()
        with pytest.raises(BackupError, match="Could not fetch"):
            backup_module.fetch_remote_sftp("nope.gz", tmp_path, config)

    @override_settings(SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com")
    def test_list_remote_sftp_missing_dir_reads_empty(self, fake_sftp):
        FakeSSHClient.fail_chdir_once = True
        assert backup_module.list_remote_sftp(get_backup_config()) == []

    def test_fetch_remote_sftp_requires_configuration(self, tmp_path, sqlite_db):
        with pytest.raises(BackupError, match="SFTP_HOST"):
            backup_module.fetch_remote_sftp("x.gz", tmp_path, get_backup_config())

    @override_settings(
        SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com",
        SNAPADMIN_BACKUP_SFTP_KEY_FILE="/keys/id_ed25519",
    )
    def test_fetch_remote_sftp_uses_key_auth(self, tmp_path, fake_sftp):
        FakeSFTP.shared_contents = {f"{BACKUP_PREFIX}20260101-000000.sql.gz": b"keyed"}
        fetched = backup_module.fetch_remote_sftp(
            f"{BACKUP_PREFIX}20260101-000000.sql.gz", tmp_path, get_backup_config(),
        )
        assert fetched.read_bytes() == b"keyed"
        client = fake_sftp.instances[-1]
        assert client.connect_kwargs["key_filename"] == "/keys/id_ed25519"
        assert "password" not in client.connect_kwargs


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling
# ─────────────────────────────────────────────────────────────────────────────

class TestScheduling:
    def test_all_active_destinations_due_on_first_run(self, backup_env):
        assert due_destinations() == ["local", "network"]

    @override_settings(SNAPADMIN_BACKUP_FTP_HOST="backup.example.com")
    def test_remote_active_when_ftp_host_configured(self, backup_env):
        assert due_destinations() == ["local", "network", "remote"]

    @override_settings(SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com")
    def test_sftp_active_when_host_configured(self, backup_env):
        assert due_destinations() == ["local", "network", "sftp"]

    def test_destination_not_due_within_interval(self, backup_env):
        config = get_backup_config()
        state = {"local": timezone.now().isoformat()}
        backup_env["local"].mkdir(parents=True, exist_ok=True)
        (backup_env["local"] / STATE_FILENAME).write_text(json.dumps(state))
        assert due_destinations(config) == ["network"]

    def test_destination_due_after_interval(self, backup_env):
        config = get_backup_config()
        old = (timezone.now() - timedelta(hours=25)).isoformat()
        backup_env["local"].mkdir(parents=True, exist_ok=True)
        (backup_env["local"] / STATE_FILENAME).write_text(
            json.dumps({"local": old, "network": old})
        )
        assert due_destinations(config) == ["local", "network"]

    def test_corrupt_state_file_treated_as_empty(self, backup_env):
        backup_env["local"].mkdir(parents=True, exist_ok=True)
        (backup_env["local"] / STATE_FILENAME).write_text("{not json")
        assert due_destinations() == ["local", "network"]


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────

class TestRunBackups:
    def test_run_due_backups_disabled(self, sqlite_db):
        assert run_due_backups() == {"ran": False, "reason": "disabled", "results": {}}

    def test_run_due_backups_ships_and_records_state(self, backup_env):
        summary = run_due_backups()
        assert summary["ran"] is True
        assert set(summary["results"]) == {"local", "network"}
        assert list(backup_env["local"].glob(f"{BACKUP_PREFIX}*"))
        assert list(backup_env["network"].glob(f"{BACKUP_PREFIX}*"))
        state = json.loads((backup_env["local"] / STATE_FILENAME).read_text())
        assert "local" in state and "network" in state
        # Immediately afterwards nothing is due any more
        assert run_due_backups() == {"ran": False, "reason": "not_due", "results": {}}

    def test_run_backup_reports_dump_failure(self, backup_env, monkeypatch):
        monkeypatch.setattr(
            backup_module, "create_db_dump",
            lambda target_dir: (_ for _ in ()).throw(BackupError("disk full")),
        )
        summary = run_backup(["local"])
        assert summary == {"ran": False, "reason": "disk full", "results": {}}

    def test_failed_destination_reported_and_retried(self, backup_env, monkeypatch):
        monkeypatch.setattr(
            backup_module, "store_network",
            lambda dump, config: (_ for _ in ()).throw(OSError("share offline")),
        )
        monkeypatch.setitem(backup_module._STORE_FUNCTIONS, "network", backup_module.store_network)
        summary = run_backup(["local", "network"])
        assert summary["ran"] is True
        assert summary["results"]["network"].startswith("error:")
        # Failed destination keeps no state → still due for retry
        state = json.loads((backup_env["local"] / STATE_FILENAME).read_text())
        assert "network" not in state and "local" in state

    def test_no_recipients_never_calls_the_encrypted_path(self, backup_env, monkeypatch):
        """Backward compatibility, pinned at the dispatch point itself: with no
        recipients configured, create_encrypted_db_dump must never even be
        called — not just "happen to produce the same bytes"."""
        monkeypatch.setattr(
            backup_module, "create_encrypted_db_dump",
            lambda target_dir, config: (_ for _ in ()).throw(
                AssertionError("encrypted path must not run with no recipients configured")
            ),
        )
        summary = run_backup(["local"])
        assert summary["ran"] is True

    def test_recipients_configured_uses_the_encrypted_path(self, tmp_path, sqlite_db, age_keypairs):
        _identity_path, recipient = age_keypairs[0]
        local = tmp_path / "local"
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True,
            SNAPADMIN_BACKUP_LOCAL_DIR=str(local),
            SNAPADMIN_BACKUP_KEEP=3,
            SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient],
        ):
            summary = run_backup(["local"])
        assert summary["ran"] is True
        assert summary["dump"].endswith(".sqlite3.gz.age")
        stored = list(local.glob(f"{BACKUP_PREFIX}*"))
        assert len(stored) == 1
        assert stored[0].name.endswith(".sqlite3.gz.age")

    def test_default_run_stores_manifest_alongside_db(self, backup_env):
        """Manifest generation is always part of a run (#BKP1c), even with
        SNAPADMIN_BACKUP_INCLUDE at its default ["db"] — the db dump itself
        stays byte-identical, the manifest is the one new artefact next to it."""
        summary = run_backup(["local"])
        assert summary["ran"] is True
        db_files = list(backup_env["local"].glob(f"{BACKUP_PREFIX}*"))
        manifest_files = list(backup_env["local"].glob(f"{PART_PREFIXES['manifest']}*"))
        assert len(db_files) == 1
        assert len(manifest_files) == 1

    def test_full_bundle_ships_every_part_to_every_destination(
        self, tmp_path, sqlite_db, media_root, env_file, age_keypairs,
    ):
        _identity_path, recipient = age_keypairs[0]
        local = tmp_path / "local"
        network = tmp_path / "network"
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True,
            SNAPADMIN_BACKUP_LOCAL_DIR=str(local),
            SNAPADMIN_BACKUP_NETWORK_DIR=str(network),
            SNAPADMIN_BACKUP_KEEP=3,
            SNAPADMIN_BACKUP_AGE_RECIPIENTS=[recipient],
            SNAPADMIN_BACKUP_INCLUDE=["db", "media", "env"],
            SNAPADMIN_BACKUP_ENV_FILE=str(env_file),
        ):
            summary = run_backup(["local", "network"])
        assert summary["ran"] is True
        for directory in (local, network):
            assert list(directory.glob(f"{PART_PREFIXES['db']}*"))
            assert list(directory.glob(f"{PART_PREFIXES['media']}*"))
            assert list(directory.glob(f"{PART_PREFIXES['env']}*"))
            assert list(directory.glob(f"{PART_PREFIXES['manifest']}*"))

    def test_retention_is_per_part_media_does_not_starve_db(self, tmp_path, sqlite_db, media_root):
        """The exact case #BKP1a-4 calls out: a run that includes media must
        not starve the db dump's retention headroom — "keep N" is "keep N of
        each part", not N total."""
        local = tmp_path / "local"
        # Distinct per-run timestamps: create_db_dump/build_backup_bundle both
        # stamp with second resolution, and three runs in the same test can
        # easily land in the same wall-clock second, colliding on one filename.
        # Freeze "now" to one moment per run, advancing between runs.
        base = timezone.now()

        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True,
            SNAPADMIN_BACKUP_LOCAL_DIR=str(local),
            SNAPADMIN_BACKUP_KEEP=2,
            SNAPADMIN_BACKUP_INCLUDE=["db", "media"],
        ):
            for i in range(3):
                with mock.patch.object(backup_module.timezone, "now", return_value=base + timedelta(seconds=i)):
                    run_backup(["local"])
        db_files = list(local.glob(f"{PART_PREFIXES['db']}*"))
        media_files = list(local.glob(f"{PART_PREFIXES['media']}*"))
        manifest_files = list(local.glob(f"{PART_PREFIXES['manifest']}*"))
        assert len(db_files) == 2  # keep=2, not starved by media
        assert len(media_files) == 2
        assert len(manifest_files) == 2


@pytest.mark.django_db
class TestBackupEntryPoints:
    def test_command_due_mode(self, backup_env):
        out = StringIO()
        call_command("snapadmin_db_backup", stdout=out)
        assert "Backup complete" in out.getvalue()
        assert "local:" in out.getvalue() and "network:" in out.getvalue()

    def test_command_disabled_reports_reason(self, sqlite_db):
        out = StringIO()
        call_command("snapadmin_db_backup", stdout=out)
        assert "No backup performed (disabled)" in out.getvalue()

    def test_command_single_destination(self, backup_env):
        out = StringIO()
        call_command("snapadmin_db_backup", "--destination", "local", stdout=out)
        assert "local:" in out.getvalue()
        assert "network:" not in out.getvalue()

    @override_settings(
        SNAPADMIN_BACKUP_FTP_HOST="backup.example.com",
        SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com",
    )
    def test_command_force_covers_all_configured(self, backup_env, fake_ftp, fake_sftp):
        out = StringIO()
        call_command("snapadmin_db_backup", "--force", stdout=out)
        assert "local:" in out.getvalue()
        assert "network:" in out.getvalue()
        assert "remote: ftp://backup.example.com" in out.getvalue()
        assert "sftp: sftp://offsite.example.com" in out.getvalue()

    def test_command_fails_on_destination_error(self, backup_env, monkeypatch):
        monkeypatch.setitem(
            backup_module._STORE_FUNCTIONS, "network",
            lambda dump, config: (_ for _ in ()).throw(OSError("share offline")),
        )
        out = StringIO()
        with pytest.raises(CommandError, match="Some backup destinations failed"):
            call_command("snapadmin_db_backup", "--force", stdout=out)
        assert "network: error: share offline" in out.getvalue()

    def test_celery_task(self, backup_env):
        from snapadmin.tasks import run_db_backups as backup_task

        result = backup_task.apply().result
        assert result["ran"] is True
        assert "local" in result["results"]
