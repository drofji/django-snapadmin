"""
tests/test_backup_retention_tiers.py — every destination prunes through one rule (#EXT3u).

The five backup destinations each pruned with their own copy of ``names[:-keep]``.
Each now asks ``_select_stale`` with the tiers from ``SNAPADMIN_BACKUP_KEEP_DAILY`` /
``_WEEKLY`` / ``_MONTHLY``. The class guard is twofold: the same seeded history,
pruned by every destination, leaves the same set behind; and an ``ast`` scan finds no
``[:-keep]``-style slice left in ``snapadmin/backup.py``.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from django.test import override_settings

from snapadmin import backup as backup_module
from snapadmin import checks
from snapadmin.backup import BACKUP_PREFIX, _RetentionPolicy, get_backup_config
from tests.test_backup import (  # noqa: F401 - fixtures are used by name
    FakeFTP,
    FakeS3Client,
    FakeSFTP,
    fake_ftp,
    fake_s3,
    fake_sftp,
)

START = datetime(2026, 3, 1, 2, 0)

#: Two backups a day for ten days — the history every destination starts from.
HISTORY = [
    f"{BACKUP_PREFIX}{START + timedelta(days=day, hours=hour):%Y%m%d-%H%M%S}.sql.gz"
    for day in range(10)
    for hour in (0, 12)
]
NEW = f"{BACKUP_PREFIX}{START + timedelta(days=10):%Y%m%d-%H%M%S}.sql.gz"

#: KEEP=2 plus three daily: the new dump and the newest of the day before (keep 2),
#: and the newest of each of the three most recent days that have one.
EXPECTED_KEPT = {
    NEW,
    HISTORY[-1],  # day 9, 14:00 — keep_last
    HISTORY[-3],  # day 8, 14:00 — daily
    # day 9's newest is HISTORY[-1], already kept; day 10 is NEW.
}
TIERS = {"SNAPADMIN_BACKUP_KEEP": 2, "SNAPADMIN_BACKUP_KEEP_DAILY": 3}


def _local(tmp_path, monkeypatch):
    directory = tmp_path / "local"
    directory.mkdir()
    for name in HISTORY:
        (directory / name).write_bytes(b"x")
    source = tmp_path / NEW
    source.write_bytes(b"new")
    with override_settings(SNAPADMIN_BACKUP_LOCAL_DIR=str(directory), **TIERS):
        backup_module.store_local(source, get_backup_config())
    return {path.name for path in directory.iterdir()}


def _network(tmp_path, monkeypatch):
    directory = tmp_path / "network"
    directory.mkdir()
    for name in HISTORY:
        (directory / name).write_bytes(b"x")
    source = tmp_path / NEW
    source.write_bytes(b"new")
    with override_settings(SNAPADMIN_BACKUP_NETWORK_DIR=str(directory), **TIERS):
        backup_module.store_network(source, get_backup_config())
    return {path.name for path in directory.iterdir()}


def _ftp(tmp_path, monkeypatch):
    FakeFTP.shared_existing = list(HISTORY)
    source = tmp_path / NEW
    source.write_bytes(b"new")
    with override_settings(SNAPADMIN_BACKUP_FTP_HOST="ftp.example.com", **TIERS):
        backup_module.store_remote_ftp(source, get_backup_config())
    ftp = FakeFTP.instances[0]
    return set(ftp.nlst()) - set(ftp.deleted)


def _sftp(tmp_path, monkeypatch):
    FakeSFTP.shared_existing = list(HISTORY)
    source = tmp_path / NEW
    source.write_bytes(b"new")
    with override_settings(SNAPADMIN_BACKUP_SFTP_HOST="sftp.example.com", **TIERS):
        backup_module.store_remote_sftp(source, get_backup_config())
    from tests.test_backup import FakeSSHClient

    sftp = FakeSSHClient.instances[0].sftp
    return set(sftp.listdir()) - set(sftp.removed)


def _s3(tmp_path, monkeypatch):
    FakeS3Client.shared_objects = {name: b"x" for name in HISTORY}
    source = tmp_path / NEW
    source.write_bytes(b"new")
    with override_settings(SNAPADMIN_BACKUP_S3_BUCKET="bucket", **TIERS):
        backup_module.store_s3(source, get_backup_config())
    return set(FakeS3Client.shared_objects)


DESTINATIONS = {"local": _local, "network": _network, "remote": _ftp, "sftp": _sftp, "s3": _s3}


def test_every_destination_is_in_the_matrix():
    assert set(DESTINATIONS) == set(backup_module.DESTINATIONS), (
        "review: a backup destination prunes without being tested against the tiers — add it to "
        "DESTINATIONS in tests/test_backup_retention_tiers.py"
    )


@pytest.mark.parametrize("destination", sorted(DESTINATIONS))
def test_each_destination_keeps_what_the_tiers_keep(
    destination, tmp_path, monkeypatch, fake_ftp, fake_sftp, fake_s3  # noqa: F811
):
    kept = DESTINATIONS[destination](tmp_path, monkeypatch)

    assert kept == EXPECTED_KEPT, f"review: {destination} pruned differently from _select_stale"


@pytest.mark.parametrize("destination", sorted(DESTINATIONS))
@override_settings(SNAPADMIN_BACKUP_KEEP=0)
def test_keep_zero_keeps_the_dump_just_written_everywhere(
    destination, tmp_path, monkeypatch, fake_ftp, fake_sftp, fake_s3  # noqa: F811
):
    global TIERS
    saved, TIERS = TIERS, {"SNAPADMIN_BACKUP_KEEP": 0}
    try:
        kept = DESTINATIONS[destination](tmp_path, monkeypatch)
    finally:
        TIERS = saved

    assert kept == {NEW}


class TestTheConfig:
    def test_the_tiers_default_to_off(self):
        config = get_backup_config()

        assert (config.keep_daily, config.keep_weekly, config.keep_monthly) == (0, 0, 0)
        assert config.retention == _RetentionPolicy(keep_last=7)

    @override_settings(
        SNAPADMIN_BACKUP_KEEP=3, SNAPADMIN_BACKUP_KEEP_DAILY=7,
        SNAPADMIN_BACKUP_KEEP_WEEKLY=4, SNAPADMIN_BACKUP_KEEP_MONTHLY=12,
    )
    def test_the_settings_build_the_policy(self):
        assert get_backup_config().retention == _RetentionPolicy(3, 7, 4, 12)


class TestTheChecks:
    def test_unset_is_quiet(self):
        assert checks.check_backup_retention(None) == []

    @override_settings(SNAPADMIN_BACKUP_KEEP=0)
    def test_keep_below_one_warns_that_the_newest_is_kept_anyway(self):
        (message,) = checks.check_backup_retention(None)

        assert message.id == "snapadmin.W036"
        assert "SNAPADMIN_BACKUP_KEEP = 0" in message.msg

    @override_settings(SNAPADMIN_BACKUP_KEEP_WEEKLY=-1)
    def test_a_negative_tier_warns(self):
        (message,) = checks.check_backup_retention(None)

        assert message.id == "snapadmin.W036"
        assert "SNAPADMIN_BACKUP_KEEP_WEEKLY = -1" in message.msg

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_KEEP_DAILY=7,
        SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS=24,
        SNAPADMIN_BACKUP_S3_BUCKET="bucket", SNAPADMIN_BACKUP_S3_EVERY_HOURS=168,
    )
    def test_a_daily_tier_on_a_weekly_destination_warns(self):
        messages = [m for m in checks.check_backup_retention(None) if m.id == "snapadmin.W037"]

        assert [m.msg for m in messages] == [
            "SNAPADMIN_BACKUP_KEEP_DAILY = 7, but the 's3' destination runs every 168 h — its "
            "daily tier keeps one backup per run: 7 of them span 49 days, not 7 days."
        ]

    @override_settings(SNAPADMIN_BACKUP_KEEP_DAILY="")
    def test_a_value_that_is_not_a_number_is_left_to_e029(self):
        """An unset environment variable arrives as ""; int() would crash the check."""
        assert checks.check_backup_retention(None) == []
        assert [m.id for m in checks.check_setting_values(None)] == ["snapadmin.E029"]

    def test_the_settings_are_validated_as_numbers(self):
        for name in ("SNAPADMIN_BACKUP_KEEP_DAILY", "SNAPADMIN_BACKUP_KEEP_WEEKLY", "SNAPADMIN_BACKUP_KEEP_MONTHLY"):
            assert checks._NUMERIC_SETTINGS[name] is int

    def test_the_check_is_registered(self):
        assert checks.check_backup_retention in checks.ALL_CHECKS


# ── The class guard: no destination slices its own retention any more ───────


def test_no_keep_slice_is_left_in_the_backup_module():
    tree = ast.parse((Path(backup_module.__file__)).read_text())
    slices = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Slice)
        and isinstance(node.slice.upper, ast.UnaryOp)
        and isinstance(node.slice.upper.op, ast.USub)
    ]

    assert not slices, (
        f"review: snapadmin/backup.py lines {slices} prune with a negative slice — ask "
        "_select_stale(names, config.retention) so every destination keeps the same backups"
    )
