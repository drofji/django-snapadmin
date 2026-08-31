"""Tests for the ``snapadmin_info`` backups collector (#BKP1g)."""

from __future__ import annotations

import json

from django.test import override_settings

from snapadmin.backup import STATE_FILENAME, get_backup_config
from snapadmin.crypto import fingerprint
from snapadmin.diagnostics import get_collector
from snapadmin.restore import record_restore_run


def _collect(*, verbose=False):
    return get_collector("backups").collect(verbose=verbose)


class TestRegistration:
    def test_registered_with_expected_metadata(self):
        collector = get_collector("backups")
        assert collector is not None
        assert collector.title == "Backups"
        assert collector.order == 22
        assert collector.health_probe is False


class TestDisabled:
    def test_disabled_reports_only_the_flag(self):
        assert _collect() == {"enabled": False}


class TestEnabled:
    def test_default_reports_local_only_unencrypted(self, tmp_path):
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path)):
            data = _collect()
        assert data["enabled"] is True
        assert data["destinations"] == ["local"]
        assert data["last_run"] == {"local": None}
        assert data["encrypted"] is False
        assert "recipient_fingerprints" not in data
        assert data["restore_last_run"] is None

    def test_lists_every_active_destination(self, tmp_path):
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True,
            SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path),
            SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com",
            SNAPADMIN_BACKUP_S3_BUCKET="my-bucket",
        ):
            data = _collect()
        assert data["destinations"] == ["local", "sftp", "s3"]
        assert set(data["last_run"]) == {"local", "sftp", "s3"}

    def test_reports_last_run_per_destination_from_state_file(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_FILENAME).write_text(
            json.dumps({"local": "2026-08-26T02:00:00+00:00"})
        )
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path)):
            data = _collect()
        assert data["last_run"]["local"] == "2026-08-26T02:00:00+00:00"

    def test_reports_encryption_and_fingerprints_never_the_identity(self, tmp_path):
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True,
            SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path),
            SNAPADMIN_BACKUP_AGE_RECIPIENTS=["age1x", "age1y"],
            SNAPADMIN_BACKUP_AGE_IDENTITY_FILE="/secret/identity.txt",
        ):
            data = _collect()
        assert data["encrypted"] is True
        assert data["recipient_fingerprints"] == [fingerprint("age1x"), fingerprint("age1y")]
        assert "/secret/identity.txt" not in json.dumps(data)
        assert "identity" not in json.dumps(data)

    def test_reports_restore_last_run_once_one_has_completed(self, tmp_path):
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path)):
            record_restore_run(get_backup_config())
            data = _collect()
        assert data["restore_last_run"] is not None
