"""
Tests for the 3-2-1 database backup stack (v0.1.0a5):

  create_db_dump (sqlite / pg_dump) → local / network / remote-FTP destinations
  → per-destination schedules → retention pruning → command + Celery task.
"""

import gzip
import json
import subprocess
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from snapadmin import backup as backup_module
from snapadmin.backup import (
    BACKUP_PREFIX,
    STATE_FILENAME,
    BackupError,
    create_db_dump,
    due_destinations,
    get_backup_config,
    run_backup,
    run_due_backups,
    store_local,
    store_network,
    store_remote_ftp,
    store_remote_sftp,
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


class FakeFTP:
    """Stand-in for ftplib.FTP capturing every call."""

    instances: list["FakeFTP"] = []

    def __init__(self, timeout=None):
        self.timeout = timeout
        self.calls = []
        self.stored = []
        self.deleted = []
        self.existing = [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]
        self.fail_cwd_once = False
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

    def nlst(self):
        return self.existing + [cmd.split(" ", 1)[1] for cmd in self.stored]

    def delete(self, name):
        self.deleted.append(name)

    def quit(self):
        self.calls.append(("quit",))


@pytest.fixture
def fake_ftp(monkeypatch):
    FakeFTP.instances = []
    monkeypatch.setattr(backup_module.ftplib, "FTP", FakeFTP)
    monkeypatch.setattr(backup_module.ftplib, "FTP_TLS", FakeFTP)
    return FakeFTP


class FakeSFTP:
    """Stand-in for a paramiko SFTPClient."""

    def __init__(self, fail_chdir_once):
        self.fail_chdir_once = fail_chdir_once
        self.put_calls = []
        self.removed = []
        self.chdir_calls = []
        self.mkdir_calls = []
        self.closed = False
        self.existing = [f"{BACKUP_PREFIX}00000000-000000.sql.gz"]

    def chdir(self, path):
        if self.fail_chdir_once:
            self.fail_chdir_once = False
            raise IOError("No such file or directory")
        self.chdir_calls.append(path)

    def mkdir(self, path):
        self.mkdir_calls.append(path)

    def put(self, local, remote):
        self.put_calls.append((local, remote))

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
    monkeypatch.setattr(paramiko, "SSHClient", FakeSSHClient)
    monkeypatch.setattr(paramiko, "AutoAddPolicy", lambda: "autoadd-policy")
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

        def fake_run(command, capture_output, env):
            recorded["command"] = command
            recorded["env"] = env
            return subprocess.CompletedProcess(command, 0, stdout=b"PG SQL", stderr=b"")

        monkeypatch.setattr(backup_module.subprocess, "run", fake_run)
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
        assert gzip.decompress(dump.read_bytes()) == b"PG SQL"
        assert recorded["command"][0] == "pg_dump"
        assert recorded["command"][-1] == "snap"
        assert "-h" in recorded["command"] and "db" in recorded["command"]
        assert recorded["env"]["PGPASSWORD"] == "pw"

    def test_postgres_dump_failure_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            backup_module.subprocess,
            "run",
            lambda command, capture_output, env: subprocess.CompletedProcess(
                command, 1, stdout=b"", stderr=b"connection refused"
            ),
        )
        databases = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "snap"}}
        with override_settings(DATABASES=databases):
            with pytest.raises(BackupError, match="connection refused"):
                create_db_dump(tmp_path)

    def test_unsupported_engine_raises(self, tmp_path):
        databases = {"default": {"ENGINE": "django.db.backends.oracle", "NAME": "x"}}
        with override_settings(DATABASES=databases):
            with pytest.raises(BackupError, match="Unsupported"):
                create_db_dump(tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# Destinations + retention
# ─────────────────────────────────────────────────────────────────────────────

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
        assert client.policy == "autoadd-policy"
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


@pytest.mark.django_db
class TestBackupEntryPoints:
    def test_command_due_mode(self, backup_env):
        out = StringIO()
        call_command("db_backup", stdout=out)
        assert "Backup complete" in out.getvalue()
        assert "local:" in out.getvalue() and "network:" in out.getvalue()

    def test_command_disabled_reports_reason(self, sqlite_db):
        out = StringIO()
        call_command("db_backup", stdout=out)
        assert "No backup performed (disabled)" in out.getvalue()

    def test_command_single_destination(self, backup_env):
        out = StringIO()
        call_command("db_backup", "--destination", "local", stdout=out)
        assert "local:" in out.getvalue()
        assert "network:" not in out.getvalue()

    @override_settings(
        SNAPADMIN_BACKUP_FTP_HOST="backup.example.com",
        SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com",
    )
    def test_command_force_covers_all_configured(self, backup_env, fake_ftp, fake_sftp):
        out = StringIO()
        call_command("db_backup", "--force", stdout=out)
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
            call_command("db_backup", "--force", stdout=out)
        assert "network: error: share offline" in out.getvalue()

    def test_celery_task(self, backup_env):
        from snapadmin.api.tasks import run_db_backups as backup_task

        result = backup_task.apply().result
        assert result["ran"] is True
        assert "local" in result["results"]
