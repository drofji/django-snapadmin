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


#: Shards ``eu`` and ``eu-west``: ``snapadmin-db-eu-`` is a plain prefix of the other
#: shard's files, which sort after it — so a prefix match kept eu-west's files and
#: deleted every eu dump, the one just written included.
SHARD_HISTORY = [
    f"snapadmin-db-eu-west-{START + timedelta(days=day):%Y%m%d-%H%M%S}.sql.gz" for day in range(3)
] + [f"snapadmin-db-eu-{START + timedelta(days=day):%Y%m%d-%H%M%S}.sql.gz" for day in range(3)]
SHARD_NEW = f"snapadmin-db-eu-{START + timedelta(days=5):%Y%m%d-%H%M%S}.sql.gz"


@pytest.mark.parametrize("destination", sorted(DESTINATIONS))
def test_a_shard_never_prunes_another_shard_whose_name_extends_its_own(
    destination, tmp_path, monkeypatch, fake_ftp, fake_sftp, fake_s3  # noqa: F811
):
    global HISTORY, NEW, TIERS
    saved = HISTORY, NEW, TIERS
    HISTORY, NEW, TIERS = SHARD_HISTORY, SHARD_NEW, {"SNAPADMIN_BACKUP_KEEP": 2}
    try:
        kept = DESTINATIONS[destination](tmp_path, monkeypatch)
    finally:
        HISTORY, NEW, TIERS = saved

    eu_west = {name for name in SHARD_HISTORY if "eu-west" in name}
    assert kept == eu_west | {SHARD_NEW, SHARD_HISTORY[-1]}, (
        f"review: {destination} pruned files of another part or shard — match the part prefix "
        "exactly (_part_prefix_for), not as a string prefix"
    )


#: What else lives in a shared remote directory: none of it is a backup of this part.
FOREIGN = [
    "README.txt", "other-app-dump.sql", "snapadmin-media-latest.tar",
    # Prefixed like a db dump but carrying no run timestamp: a manual copy, or another
    # shard's unstamped file. Sorting after the dated dumps, it used to take keep_last's
    # slots — and cost the newest real dump its place.
    "snapadmin-db-latest.sql.gz", "snapadmin-db-eu-latest.sql.gz",
]


@pytest.mark.parametrize("destination", ["remote", "sftp", "s3"])
def test_files_that_are_not_this_part_s_backups_are_never_touched(
    destination, tmp_path, monkeypatch, fake_ftp, fake_sftp, fake_s3  # noqa: F811
):
    """A name with no timestamp maps to the db prefix by fallback; the exact-prefix
    rule alone then took a remote directory's every unrelated file as a db dump."""
    global HISTORY, TIERS
    saved = HISTORY, TIERS
    # KEEP alone, so no tier can rescue a dump a foreign file pushed out of keep_last.
    HISTORY, TIERS = saved[0] + FOREIGN, {"SNAPADMIN_BACKUP_KEEP": 2}
    try:
        kept = DESTINATIONS[destination](tmp_path, monkeypatch)
    finally:
        HISTORY, TIERS = saved

    assert set(FOREIGN) <= kept, f"review: {destination} deleted files that are not its backups"
    assert kept - set(FOREIGN) == {NEW, HISTORY[-1]}, (
        f"review: {destination} let a file with no run timestamp take a kept slot"
    )


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


def test_a_key_outside_the_location_prefix_is_not_part_of_it():
    from snapadmin.backup import _of_part

    keys = [f"prod/{NEW}", f"staging/{NEW}"]

    assert _of_part(keys, BACKUP_PREFIX, strip="prod/") == [f"prod/{NEW}"]
