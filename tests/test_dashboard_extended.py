"""
tests/test_dashboard_extended.py

Coverage for snapadmin/views.py — ES monitoring, OperationalError, environment detection.
"""

from unittest.mock import MagicMock, mock_open, patch

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory

from snapadmin.views import DashboardView


def _make_view_with_request(username="dashext"):
    factory = RequestFactory()
    request = factory.get("/")
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        user = User.objects.create_superuser(username, password="x")
    request.user = user
    view = DashboardView()
    view.request = request
    view.kwargs = {}
    view.args = []
    return view


@pytest.mark.django_db
class TestDashboardViewExtended:
    def test_get_service_status_db_online(self):
        view = _make_view_with_request("srv_test")
        services = view._get_service_status()
        db_service = next(s for s in services if "Database" in s["name"])
        assert db_service["status"] == "online"

    def test_get_service_status_db_offline(self):
        from django.db.utils import OperationalError
        view = _make_view_with_request("srv_offline")
        with patch("snapadmin.views.connections") as mock_conns:
            mock_conns.__getitem__.return_value.cursor.side_effect = OperationalError("down")
            services = view._get_service_status()
        db_service = next(s for s in services if "Database" in s["name"])
        assert db_service["status"] == "offline"

    def test_get_service_status_es_enabled_online(self):
        view = _make_view_with_request("es_online")
        mock_es = MagicMock()
        mock_es.ping.return_value = True
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                services = view._get_service_status()
        es_service = next(s for s in services if s["name"] == "Elasticsearch")
        assert es_service["status"] == "online"

    def test_get_service_status_es_enabled_offline(self):
        view = _make_view_with_request("es_offline")
        mock_es = MagicMock()
        mock_es.ping.return_value = False
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", return_value=mock_es):
                services = view._get_service_status()
        es_service = next(s for s in services if s["name"] == "Elasticsearch")
        assert es_service["status"] == "offline"

    def test_get_service_status_es_exception(self):
        view = _make_view_with_request("es_exc")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch("elasticsearch.Elasticsearch", side_effect=Exception("ES error")):
                services = view._get_service_status()
        es_service = next(s for s in services if s["name"] == "Elasticsearch")
        assert es_service["status"] == "offline"

    def test_get_service_status_es_disabled(self):
        view = _make_view_with_request("es_disabled")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", False):
            services = view._get_service_status()
        es_service = next(s for s in services if s["name"] == "Elasticsearch")
        assert es_service["status"] == "disabled"

    def test_get_environment_details(self):
        view = _make_view_with_request("env_test")
        env = view._get_environment_details()
        assert "mode" in env
        assert "os" in env
        assert env["mode"] in ("Docker", "Local")

    def test_get_environment_details_dockerenv_short_circuits_cgroup_open(self):
        """When /.dockerenv already proves Docker, /proc/self/cgroup must not be opened."""
        view = _make_view_with_request("env_dockerenv")
        with patch("os.path.exists", return_value=True):
            with patch("snapadmin.views.open", create=True) as mocked_open:
                env = view._get_environment_details()
        mocked_open.assert_not_called()
        assert env["mode"] == "Docker"

    def test_get_environment_details_cgroup_docker_uses_context_manager(self):
        """The cgroup fallback path must open the file as a context manager
        (no leaked file handle) and detect Docker from its contents."""
        view = _make_view_with_request("env_cgroup_docker")
        m = mock_open(read_data="1:name=systemd:/docker/abcdef1234567890\n")
        with patch("os.path.exists", side_effect=lambda p: p == "/proc/self/cgroup"):
            with patch("snapadmin.views.open", m, create=True):
                env = view._get_environment_details()
        m.assert_called_once_with("/proc/self/cgroup")
        m.return_value.__enter__.assert_called_once()
        m.return_value.__exit__.assert_called_once()
        assert env["mode"] == "Docker"

    def test_get_environment_details_cgroup_no_docker_marker(self):
        view = _make_view_with_request("env_cgroup_nodocker")
        m = mock_open(read_data="1:name=systemd:/\n")
        with patch("os.path.exists", side_effect=lambda p: p == "/proc/self/cgroup"):
            with patch("snapadmin.views.open", m, create=True):
                env = view._get_environment_details()
        assert env["mode"] == "Local"

    def test_get_environment_details_local_no_docker_indicators(self):
        view = _make_view_with_request("env_local")
        with patch("os.path.exists", return_value=False):
            with patch("snapadmin.views.open", create=True) as mocked_open:
                env = view._get_environment_details()
        mocked_open.assert_not_called()
        assert env["mode"] == "Local"
