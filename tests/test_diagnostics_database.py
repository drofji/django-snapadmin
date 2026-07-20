"""Tests for the ``snapadmin_info`` database collector (#CLI1b)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.db.utils import OperationalError

from snapadmin.diagnostics import get_collector
from snapadmin.diagnostics.database import _db_size


def _mock_conn(vendor, settings_dict=None):
    conn = MagicMock()
    conn.vendor = vendor
    conn.settings_dict = settings_dict or {}
    return conn


class TestDbSize:
    def test_sqlite_memory_has_no_size(self):
        assert _db_size(_mock_conn("sqlite", {"NAME": ":memory:"})) is None

    def test_sqlite_file_size(self, tmp_path):
        db_file = tmp_path / "db.sqlite3"
        db_file.write_bytes(b"x" * 128)
        assert _db_size(_mock_conn("sqlite", {"NAME": str(db_file)})) == 128

    def test_sqlite_missing_file(self, tmp_path):
        assert _db_size(_mock_conn("sqlite", {"NAME": str(tmp_path / "gone.sqlite3")})) is None

    def test_postgresql_size(self):
        conn = _mock_conn("postgresql")
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (2048,)
        assert _db_size(conn) == 2048

    def test_mysql_size(self):
        conn = _mock_conn("mysql")
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (4096,)
        assert _db_size(conn) == 4096

    def test_unknown_vendor(self):
        assert _db_size(_mock_conn("oracle")) is None

    def test_query_error_is_swallowed(self):
        conn = _mock_conn("postgresql")
        conn.cursor.side_effect = OperationalError("boom")
        assert _db_size(conn) is None


class TestDatabaseCollector:
    @pytest.mark.django_db
    def test_reachable_reports_ok_and_tables(self):
        data = get_collector("database").collect(verbose=False)
        assert data["ok"] is True
        assert data["engine"] == "sqlite"
        assert isinstance(data["tables"], int)
        assert data["host"] == "localhost"
        assert "password" not in data  # never leaked

    @pytest.mark.django_db
    def test_reports_size_when_backend_exposes_it(self, monkeypatch):
        monkeypatch.setattr("snapadmin.diagnostics.database._db_size", lambda conn: 5000)
        data = get_collector("database").collect(verbose=False)
        assert data["size_bytes"] == 5000

    def test_unreachable_reports_error(self, monkeypatch):
        conn = _mock_conn(
            "postgresql",
            {"NAME": "app", "HOST": "db", "PORT": 5432, "USER": "app"},
        )
        conn.ensure_connection.side_effect = OperationalError("refused")
        monkeypatch.setattr("snapadmin.diagnostics.database.connections", {"default": conn})
        data = get_collector("database").collect(verbose=False)
        assert data["ok"] is False
        assert "refused" in data["error"]
        assert data["user"] == "app"
