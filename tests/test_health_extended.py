"""
tests/test_health_extended.py

Coverage for snapadmin/api/health.py — ES enabled/disabled paths.

The per-service breakdown is reserved for authenticated callers; anonymous
probes (load balancers) receive the overall status only.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.fixture
def client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.mark.django_db
class TestHealthCheckExtended:
    def test_health_check_db_online(self, client):
        url = reverse("api-health")
        response = client.get(url)
        data = response.json()
        assert data["services"]["database"] == "online"

    def test_health_check_es_disabled(self, client):
        url = reverse("api-health")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", False):
            response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "disabled"

    def test_health_check_es_enabled_and_online(self, client):
        url = reverse("api-health")
        mock_es = MagicMock()
        mock_es.ping.return_value = True
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "online"
        assert data["status"] in ("healthy", "degraded")

    def test_health_check_es_enabled_but_offline(self, client):
        url = reverse("api-health")
        mock_es = MagicMock()
        mock_es.ping.return_value = False
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "offline"
        assert data["status"] == "degraded"

    def test_health_check_es_enabled_exception(self, client):
        url = reverse("api-health")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", side_effect=Exception("ES error")):
                response = client.get(url)
        data = response.json()
        assert data["services"]["elasticsearch"] == "offline"

    def test_health_check_db_offline(self, client):
        from django.db.utils import OperationalError
        url = reverse("api-health")
        with patch("snapadmin.api.health.connections") as mock_conns:
            mock_conns.__getitem__.return_value.cursor.side_effect = OperationalError("DB down")
            response = client.get(url)
        data = response.json()
        assert data["services"]["database"] == "offline"
        assert data["status"] == "unhealthy"


@pytest.mark.django_db
class TestHealthCheckStatusCode:
    """The HTTP status is what a container runtime actually reads.

    The endpoint used to answer ``200`` unconditionally, so a Docker/Kubernetes/Coolify
    probe pointed at it could never fail — a database outage looked healthy.
    """

    def _db_down(self, client):
        from django.db.utils import OperationalError
        with patch("snapadmin.api.health.connections") as mock_conns:
            mock_conns.__getitem__.return_value.cursor.side_effect = OperationalError("down")
            return client.get(reverse("api-health"))

    def test_healthy_is_200(self, client):
        assert client.get(reverse("api-health")).status_code == 200

    def test_unhealthy_is_503(self, client):
        assert self._db_down(client).status_code == 503

    def test_unhealthy_is_503_for_anonymous_probes_too(self):
        assert self._db_down(APIClient()).status_code == 503

    def test_degraded_still_serves_200(self, client):
        """Elasticsearch is optional — pulling the instance would make it worse."""
        mock_es = MagicMock()
        mock_es.ping.return_value = False
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                response = client.get(reverse("api-health"))
        assert response.json()["status"] == "degraded"
        assert response.status_code == 200

    @pytest.mark.parametrize("es_reachable", [False, "raises"])
    def test_database_outage_outranks_elasticsearch(self, client, es_reachable):
        """A dead database stays 503 even when Elasticsearch reports too.

        Both subsystems failing used to let the Elasticsearch branch overwrite
        "unhealthy" with "degraded", answering 200 and keeping an instance that
        cannot serve a single query in the load balancer.
        """
        from django.db.utils import OperationalError

        mock_es = MagicMock()
        if es_reachable == "raises":
            mock_es.ping.side_effect = RuntimeError("unreachable")
        else:
            mock_es.ping.return_value = False

        with patch("snapadmin.api.health.connections") as mock_conns:
            mock_conns.__getitem__.return_value.cursor.side_effect = OperationalError("down")
            with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
                with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                    response = client.get(reverse("api-health"))

        assert response.json()["status"] == "unhealthy"
        assert response.status_code == 503


@pytest.mark.django_db
class TestHealthCheckAnonymous:
    def test_anonymous_gets_status_only(self):
        response = APIClient().get(reverse("api-health"))
        data = response.json()
        assert response.status_code == 200
        assert "status" in data
        assert "services" not in data
