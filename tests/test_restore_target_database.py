"""Restoring into a database other than ``default`` — the restore drill (#EXT2f).

Every serious backup policy wants a periodic restore drill into a throwaway
database. ``snapadmin_restore`` only ever targeted ``DATABASES["default"]``, so
a drill meant pointing the whole process at a different database URL. Now:

* ``--database <alias>`` restores the ``db`` part into that alias and leaves
  ``default`` untouched;
* an alias that resolves to the **same** database as ``default`` is refused —
  a typo must not turn a drill into an overwrite of production;
* only ``db`` may go to another alias: ``media``/``env`` have no per-alias
  target and would land on the live system;
* the result is verified — the command prints the row count per table, and a
  restore that produced no tables is an error, never a green drill;
* on PostgreSQL the target is reset by recreating its ``public`` schema instead
  of ``dropdb``/``createdb``, so the drill role needs no ``CREATEDB``.
"""
from __future__ import annotations

import gzip
import sqlite3
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import connections
from django.test import override_settings

from snapadmin import restore as restore_module
from snapadmin.backup import PART_PREFIXES, get_backup_config, run_backup
from snapadmin.restore import (
    RestoreError,
    perform_restore,
    plan_restore,
    resolve_source,
    restore_db,
    table_row_counts,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:Overriding setting DATABASES can lead to unexpected behavior"
)


@contextmanager
def use_databases(databases: dict, **other_settings):
    """``override_settings(DATABASES=...)`` that ``django.db.connections`` also sees.

    Django caches the connection settings on first use and ignores a later
    ``DATABASES`` override, so a restore into an alias declared only here would
    find no connection. The extra aliases are closed and forgotten on exit;
    the real ``default`` connection is never replaced.
    """
    extra = [alias for alias in databases if alias != "default"]
    original = connections.__dict__.pop("settings", None), connections._settings
    with override_settings(DATABASES=databases, **other_settings):
        connections._settings = None
        try:
            yield
        finally:
            for alias in extra:
                if hasattr(connections._connections, alias):
                    getattr(connections._connections, alias).close()
                    delattr(connections._connections, alias)
            connections.__dict__.pop("settings", None)
            cached, connections._settings = original
            if cached is not None:
                connections.__dict__["settings"] = cached


def _real_sqlite(path: Path, rows: int) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO orders (label) VALUES (?)", [(f"o{i}",) for i in range(rows)])
    return path


@pytest.fixture
def two_databases(tmp_path, django_db_blocker):
    """A real SQLite ``default`` with data, an empty ``drill`` beside it, backups on.

    Both are throwaway files under ``tmp_path``, never the test database, so
    the verification queries are let through pytest-django's database guard.
    """
    live = _real_sqlite(tmp_path / "live.sqlite3", rows=3)
    drill = tmp_path / "drill.sqlite3"
    local = tmp_path / "local"
    databases = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(live)},
        "drill": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(drill)},
    }
    with django_db_blocker.unblock(), use_databases(
        databases, SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local),
    ):
        yield live, drill, local


def _manifest(local: Path) -> str:
    [manifest] = local.glob(f"{PART_PREFIXES['manifest']}*")
    return str(manifest)


class TestRestoreIntoAnotherAlias:
    def test_the_drill_database_gets_the_data_and_default_is_untouched(self, two_databases, tmp_path):
        live, drill, local = two_databases
        run_backup(["local"])
        live_before = live.read_bytes()
        resolved = resolve_source(_manifest(local), tmp_path / "work", get_backup_config())

        results = perform_restore(resolved, ["db"], get_backup_config(), database="drill")

        assert live.read_bytes() == live_before
        assert results["db"] == "restored into 'drill' (2 tables, 3 rows)"
        assert table_row_counts("drill") == {"notes": 0, "orders": 3}

    def test_media_or_env_cannot_go_to_another_alias(self, two_databases, tmp_path):
        _live, _drill, local = two_databases
        run_backup(["local"])
        resolved = resolve_source(_manifest(local), tmp_path / "work", get_backup_config())

        with pytest.raises(RestoreError, match="only the 'db' part"):
            perform_restore(resolved, ["db", "media"], get_backup_config(), database="drill")

    def test_an_unknown_alias_is_refused(self, two_databases, tmp_path):
        with pytest.raises(RestoreError, match="'nope' is not in DATABASES"):
            restore_db(tmp_path / "x.gz", database="nope")

    def test_a_restore_that_produced_no_tables_fails_the_drill(self, two_databases, tmp_path):
        empty = tmp_path / "empty.gz"
        with gzip.open(empty, "wb") as f:
            f.write(b"")
        restore_db(empty, database="drill")

        with pytest.raises(RestoreError, match="no tables"):
            restore_module.verify_restored_database("drill")


class TestSameDatabaseGuard:
    def test_a_second_alias_on_the_same_sqlite_file_is_refused(self, tmp_path):
        live = _real_sqlite(tmp_path / "live.sqlite3", rows=1)
        databases = {
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(live)},
            "alias": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(tmp_path / "." / "live.sqlite3")},
        }
        with use_databases(databases):
            with pytest.raises(RestoreError, match="same database as 'default'"):
                restore_db(tmp_path / "x.gz", database="alias")
        assert table_row_counts_file(live) == 1

    def test_a_postgres_alias_on_the_same_server_and_name_is_refused(self, tmp_path):
        pg = {"ENGINE": "django.db.backends.postgresql", "NAME": "app", "HOST": "db", "PORT": "5432"}
        databases = {"default": pg, "replica": {**pg, "PORT": ""}}
        with use_databases(databases):
            with pytest.raises(RestoreError, match="same database as 'default'"):
                restore_db(tmp_path / "x.gz", database="replica")

    def test_a_postgres_alias_with_another_name_is_allowed(self, tmp_path, monkeypatch):
        calls = _fake_postgres(monkeypatch)
        gz = tmp_path / "dump.sql.gz"
        with gzip.open(gz, "wb") as f:
            f.write(b"CREATE TABLE t (x int);")
        pg = {"ENGINE": "django.db.backends.postgresql", "NAME": "app", "HOST": "db", "USER": "u"}
        databases = {"default": pg, "drill": {**pg, "NAME": "app_drill", "USER": "drill"}}
        with use_databases(databases):
            restore_db(gz, database="drill")

        assert not any(c[0] in ("dropdb", "createdb") for c in calls)
        reset = [c for c in calls if c[0] == "psql" and "DROP SCHEMA" in c[-1]]
        assert len(reset) == 1
        assert reset[0][reset[0].index("-d") + 1] == "app_drill"
        assert reset[0][reset[0].index("-U") + 1] == "drill"
        assert "CREATE SCHEMA public" in reset[0][-1]


def table_row_counts_file(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def _fake_postgres(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stderr = b""

    def fake_run(args, capture_output, env):
        calls.append(args)
        return Completed()

    class Proc:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.stdin = mock.MagicMock()
            self.stderr = mock.MagicMock(read=lambda: b"")

        def wait(self):
            return 0

    monkeypatch.setattr(restore_module.subprocess, "run", fake_run)
    monkeypatch.setattr(restore_module.subprocess, "Popen", Proc)
    return calls


class TestDefaultTargetIsUnchanged:
    def test_postgres_default_still_drops_and_recreates(self, tmp_path, monkeypatch):
        calls = _fake_postgres(monkeypatch)
        gz = tmp_path / "dump.sql.gz"
        with gzip.open(gz, "wb") as f:
            f.write(b"x")
        databases = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "snap"}}
        with use_databases(databases):
            restore_db(gz)

        assert [c[0] for c in calls if c[0] in ("dropdb", "createdb")] == ["dropdb", "createdb"]
        assert not any("DROP SCHEMA" in c[-1] for c in calls if c[0] == "psql")


class TestTheCommand:
    def test_dry_run_names_the_target_alias(self, two_databases):
        _live, _drill, local = two_databases
        run_backup(["local"])
        out = StringIO()

        call_command("snapadmin_restore", _manifest(local), "--database", "drill", stdout=out)

        text = out.getvalue()
        assert "would replace database" in text
        assert "(alias 'drill')" in text
        assert "Dry run" in text

    def test_confirm_restores_and_prints_row_counts_per_table(self, two_databases):
        live, _drill, local = two_databases
        run_backup(["local"])
        live_before = live.read_bytes()
        out = StringIO()

        call_command("snapadmin_restore", _manifest(local), "--database", "drill", "--confirm",
                     stdout=out)

        text = out.getvalue()
        assert "db: restored into 'drill' (2 tables, 3 rows)" in text
        assert "  orders: 3" in text
        assert "  notes: 0" in text
        assert "snapshot skipped" in text
        assert live.read_bytes() == live_before

    def test_a_bare_selection_restores_only_db_into_another_alias(self, two_databases, tmp_path):
        _live, _drill, local = two_databases
        media = tmp_path / "media"
        media.mkdir()
        (media / "a.txt").write_text("x")
        with override_settings(MEDIA_ROOT=str(media), SNAPADMIN_BACKUP_INCLUDE=["db", "media"]):
            run_backup(["local"])
            out = StringIO()
            call_command("snapadmin_restore", _manifest(local), "--database", "drill", stdout=out)

        text = out.getvalue()
        assert "  db:" in text
        assert "  media:" not in text

    def test_only_media_with_another_alias_is_an_error(self, two_databases):
        _live, _drill, local = two_databases
        run_backup(["local"])

        with pytest.raises(CommandError, match="only the 'db' part"):
            call_command("snapadmin_restore", _manifest(local), "--database", "drill",
                         "--only", "db,media", "--confirm")

    def test_the_default_plan_is_unchanged(self, two_databases):
        live, _drill, local = two_databases
        run_backup(["local"])
        out = StringIO()

        call_command("snapadmin_restore", _manifest(local), stdout=out)

        assert f"would replace database {str(live)!r}\n" in out.getvalue()


def test_plan_restore_defaults_to_default(two_databases, tmp_path):
    live, _drill, local = two_databases
    run_backup(["local"])
    resolved = resolve_source(_manifest(local), tmp_path / "work", get_backup_config())

    lines = plan_restore(resolved, ["db"])

    assert any(line.endswith(f"would replace database {str(live)!r}") for line in lines)
