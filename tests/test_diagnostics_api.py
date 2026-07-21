"""Tests for the ``snapadmin_info`` REST API collector (health probe)."""

from __future__ import annotations

from unittest.mock import patch

from django.test import override_settings

from snapadmin.diagnostics import api as api_collector
from snapadmin.diagnostics import get_collector


def _collect():
    return get_collector("api").collect(verbose=False)


class TestApiCollector:
    @override_settings(SNAPADMIN_REST_API_ENABLED=False)
    def test_disabled_is_one_line_and_never_a_failure(self):
        assert _collect() == {"enabled": False}

    @override_settings(SNAPADMIN_REST_API_ENABLED=True)
    def test_enabled_and_wired(self):
        data = _collect()
        assert data["enabled"] is True
        assert data["ok"] is True
        assert data["health_url"] == "/api/health/"

    @override_settings(SNAPADMIN_REST_API_ENABLED=True)
    def test_enabled_but_unwired_reports_failure(self):
        with patch.object(api_collector, "reverse", side_effect=Exception("no route")):
            data = _collect()
        assert data["ok"] is False
        assert "no route" in data["error"]
