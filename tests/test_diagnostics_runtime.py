"""Tests for the ``snapadmin_info`` Celery/broker collector (#CLI1d)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from snapadmin.diagnostics import get_collector
from snapadmin.diagnostics.runtime import _mask_url, _worker_names


def _collect(verbose=False):
    return get_collector("celery").collect(verbose=verbose)


class TestMaskUrl:
    def test_no_password_unchanged(self):
        assert _mask_url("redis://localhost:6379/0") == "redis://localhost:6379/0"

    def test_password_with_user_redacted(self):
        assert _mask_url("redis://user:secret@host:6379/0") == "redis://user:***@host:6379/0"

    def test_password_without_user_redacted(self):
        assert _mask_url("redis://:secret@host/0") == "redis://***@host/0"


class TestWorkerNames:
    def test_returns_sorted_names(self):
        app = MagicMock()
        app.control.inspect.return_value.ping.return_value = {"w2@h": {}, "w1@h": {}}
        assert _worker_names(app) == ["w1@h", "w2@h"]

    def test_no_workers(self):
        app = MagicMock()
        app.control.inspect.return_value.ping.return_value = None
        assert _worker_names(app) == []


class TestCeleryCollector:
    def test_celery_absent(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "celery", None)
        assert _collect() == {"enabled": False}

    def test_reports_workers_and_schedule(self, monkeypatch):
        monkeypatch.setattr(
            "snapadmin.diagnostics.runtime._worker_names", lambda app: ["celery@host"]
        )
        data = _collect()
        assert data["enabled"] is True
        assert data["workers_online"] == 1
        assert isinstance(data["scheduled_tasks"], list)
        assert "workers" not in data  # only under --verbose

    def test_verbose_lists_workers(self, monkeypatch):
        monkeypatch.setattr(
            "snapadmin.diagnostics.runtime._worker_names", lambda app: ["a@h", "b@h"]
        )
        data = _collect(verbose=True)
        assert data["workers"] == ["a@h", "b@h"]

    def test_inspect_error_is_reported(self, monkeypatch):
        def _boom(app):
            raise RuntimeError("broker down")

        monkeypatch.setattr("snapadmin.diagnostics.runtime._worker_names", _boom)
        data = _collect()
        assert data["workers_online"] == 0
        assert "broker down" in data["error"]
