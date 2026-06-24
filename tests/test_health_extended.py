"""
tests/test_health_extended.py

Coverage for snapadmin/api/health.py — ES enabled/disabled paths.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestHealthCheckExtended:
    def test_health_check_db_online(self):
        client = APIClient()
        url = reverse("api-health")
        response = client.get(url)
        data = response.json()
        assert data["services"]["database"] == "online"

    def test_health_check_es_disabled(self):
        client = APIClient()
        url = reverse("api-health")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", False):
            response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "disabled"

    def test_health_check_es_enabled_and_online(self):
        client = APIClient()
        url = reverse("api-health")
        mock_es = MagicMock()
        mock_es.ping.return_value = True
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "online"
        assert data["status"] in ("healthy", "degraded")

    def test_health_check_es_enabled_but_offline(self):
        client = APIClient()
        url = reverse("api-health")
        mock_es = MagicMock()
        mock_es.ping.return_value = False
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "offline"
        assert data["status"] == "degraded"

    def test_health_check_es_enabled_exception(self):
        client = APIClient()
        url = reverse("api-health")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", side_effect=Exception("ES error")):
                response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "offline"

    def test_health_check_db_offline(self):
        from django.db.utils import OperationalError
        client = APIClient()
        url = reverse("api-health")
        with patch("snapadmin.api.health.connections") as mock_conns:
            mock_conns.__getitem__.return_value.cursor.side_effect = OperationalError("DB down")
            response = client.get(url)
        data = response.json()
        assert data["services"]["database"] == "offline"
        assert data["status"] == "unhealthy"
