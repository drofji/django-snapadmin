"""Tests for the ``snapadmin_info`` Elasticsearch collector (#CLI1c)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import override_settings

from snapadmin.diagnostics import get_collector
from snapadmin.diagnostics.elasticsearch import _storage_mode_tally
from snapadmin.models import SnapModel


def _collect():
    return get_collector("elasticsearch").collect(verbose=False)


class TestStorageModeTally:
    def test_tally_has_all_modes(self):
        tally = _storage_mode_tally()
        assert set(tally) == {"DB_ONLY", "DUAL", "ES_ONLY"}
        assert all(isinstance(count, int) for count in tally.values())
        assert sum(tally.values()) > 0


class TestElasticsearchCollector:
    @override_settings(ELASTICSEARCH_ENABLED=False)
    def test_disabled(self):
        assert _collect() == {"enabled": False}

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_enabled_and_reachable(self):
        client = MagicMock()
        client.ping.return_value = True
        client.cluster.health.return_value = {"status": "green"}
        client.indices.get_alias.return_value = {"idx-a": {}, "idx-b": {}}
        with patch.object(SnapModel, "get_es_client", return_value=client):
            data = _collect()
        assert data["ok"] is True
        assert data["cluster_status"] == "green"
        assert data["indices"] == 2
        assert data["storage_modes"]["DUAL"] >= 1

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_enabled_but_ping_fails(self):
        client = MagicMock()
        client.ping.return_value = False
        with patch.object(SnapModel, "get_es_client", return_value=client):
            data = _collect()
        assert data["ok"] is False
        assert "cluster_status" not in data

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_enabled_but_client_errors(self):
        with patch.object(SnapModel, "get_es_client", side_effect=RuntimeError("no route")):
            data = _collect()
        assert data["ok"] is False
        assert "no route" in data["error"]
