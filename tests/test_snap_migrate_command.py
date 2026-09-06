"""
tests/test_snap_migrate_command.py — #SHARD1f

``manage.py snap_migrate`` runs Django's own ``migrate`` against every
shard's primary alias — never a replica — sequentially by default or via
``--parallel``. ``call_command`` itself is monkeypatched here (a real
migration run is exactly what ``test_reindex_command.py``'s siblings avoid
too — this file is about the command's own orchestration: which aliases it
targets, in what order it reports them, and how one failure is handled
without hiding the others).
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from snapadmin.management.commands import snap_migrate as snap_migrate_module

TWO_SHARDS = {
    "ENABLED": True,
    "SHARDS": {
        "shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]},
        "shard_2": {"PRIMARY": "d2"},
    },
}


def _fake_call_command(calls, *, fail_for=()):
    """A drop-in replacement for django.core.management.call_command that
    records every (command_name, database) pair and writes to the caller's
    stdout, raising for any alias named in ``fail_for``."""

    def fake(name, *args, database=None, stdout=None, stderr=None, **kwargs):
        calls.append((name, database))
        if database in fail_for:
            raise CommandError(f"boom on {database}")
        if stdout is not None:
            stdout.write(f"Applying {database}... OK\n")

    return fake


class TestDisabled:
    def test_unset_is_a_clear_no_op(self):
        out = StringIO()
        call_command("snap_migrate", stdout=out)
        assert "not enabled" in out.getvalue()

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": False})
    def test_explicitly_disabled_is_a_clear_no_op(self):
        out = StringIO()
        call_command("snap_migrate", stdout=out)
        assert "not enabled" in out.getvalue()


class TestZeroShards:
    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True})
    def test_misconfigured_raises_command_error(self):
        with pytest.raises(CommandError, match="zero shards"):
            call_command("snap_migrate")


class TestSequential:
    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS)
    def test_migrates_every_primary_never_a_replica(self, monkeypatch):
        calls = []
        monkeypatch.setattr(snap_migrate_module, "call_command", _fake_call_command(calls))
        out = StringIO()
        call_command("snap_migrate", stdout=out)

        databases = [db for _name, db in calls]
        assert databases == ["snapadmin_shard_1_primary", "snapadmin_shard_2_primary"]
        assert "snapadmin_shard_1_replica_0" not in databases
        assert all(name == "migrate" for name, _db in calls)

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS)
    def test_reports_success_for_every_shard(self, monkeypatch):
        monkeypatch.setattr(snap_migrate_module, "call_command", _fake_call_command([]))
        out = StringIO()
        call_command("snap_migrate", stdout=out)

        output = out.getvalue()
        assert "snapadmin_shard_1_primary: migrated" in output
        assert "snapadmin_shard_2_primary: migrated" in output
        assert "Applying snapadmin_shard_1_primary" in output

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS)
    def test_one_failure_does_not_stop_the_others(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            snap_migrate_module, "call_command",
            _fake_call_command(calls, fail_for={"snapadmin_shard_1_primary"}),
        )
        out = StringIO()
        with pytest.raises(CommandError, match="failed for 1 of 2 shard"):
            call_command("snap_migrate", stdout=out)

        # Both shards were still attempted — the failure did not short-circuit.
        databases = [db for _name, db in calls]
        assert databases == ["snapadmin_shard_1_primary", "snapadmin_shard_2_primary"]
        output = out.getvalue()
        assert "snapadmin_shard_1_primary: FAILED" in output
        assert "snapadmin_shard_2_primary: migrated" in output


class TestParallel:
    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS)
    def test_migrates_every_primary(self, monkeypatch):
        calls = []
        monkeypatch.setattr(snap_migrate_module, "call_command", _fake_call_command(calls))
        out = StringIO()
        call_command("snap_migrate", "--parallel", stdout=out)

        databases = sorted(db for _name, db in calls)
        assert databases == ["snapadmin_shard_1_primary", "snapadmin_shard_2_primary"]
        output = out.getvalue()
        assert "snapadmin_shard_1_primary: migrated" in output
        assert "snapadmin_shard_2_primary: migrated" in output

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS)
    def test_one_failure_is_reported_without_hiding_the_other(self, monkeypatch):
        monkeypatch.setattr(
            snap_migrate_module, "call_command",
            _fake_call_command([], fail_for={"snapadmin_shard_2_primary"}),
        )
        out = StringIO()
        with pytest.raises(CommandError, match="failed for 1 of 2 shard"):
            call_command("snap_migrate", "--parallel", stdout=out)

        output = out.getvalue()
        assert "snapadmin_shard_2_primary: FAILED" in output
        assert "snapadmin_shard_1_primary: migrated" in output
