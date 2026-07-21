"""Tests for the ``snapadmin_info`` GraphQL collector (health probe)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings

from snapadmin.api import graphql as gql
from snapadmin.diagnostics import get_collector


def _collect():
    return get_collector("graphql").collect(verbose=False)


class TestGraphqlCollector:
    @override_settings(SNAPADMIN_GRAPHQL_ENABLED=False)
    def test_disabled_is_one_line_and_never_a_failure(self):
        assert _collect() == {"enabled": False}

    @override_settings(SNAPADMIN_GRAPHQL_ENABLED=True)
    def test_enabled_and_schema_builds(self):
        data = _collect()
        assert data["enabled"] is True
        assert data["ok"] is True

    @override_settings(SNAPADMIN_GRAPHQL_ENABLED=True)
    def test_enabled_but_schema_reports_errors(self):
        fake = SimpleNamespace(errors=[Exception("bad field")], data=None)
        with patch.object(gql.schema, "execute", return_value=fake):
            data = _collect()
        assert data["ok"] is False
        assert "bad field" in data["error"]

    @override_settings(SNAPADMIN_GRAPHQL_ENABLED=True)
    def test_enabled_but_schema_raises(self):
        with patch.object(gql.schema, "execute", side_effect=RuntimeError("boom")):
            data = _collect()
        assert data["ok"] is False
        assert "boom" in data["error"]
