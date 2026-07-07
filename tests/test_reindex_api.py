"""
Tests for the admin-only ES reindex API (#B10):

  POST /api/es/reindex/ — bulk-reindex every ES-enabled SnapModel.

Off by default (SNAPADMIN_REINDEX_API_ENABLED), staff-only (IsAdminUser),
synchronous unless SNAPADMIN_REINDEX_API_ASYNC offloads to Celery.
"""

import sys
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient


@pytest.fixture
def admin_client(api_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {api_token.token_key}")
    return client


@pytest.fixture
def regular_client(db, regular_user):
    from snapadmin.models import APIToken
    token = APIToken.create_for_user(regular_user, "Regular")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
    return client


@pytest.mark.django_db
class TestReindexApiGate:
    def test_disabled_by_default_returns_404(self, admin_client):
        # No SNAPADMIN_REINDEX_API_ENABLED → the endpoint hides itself.
        r = admin_client.post("/api/es/reindex/")
        assert r.status_code == 404

    @override_settings(SNAPADMIN_REINDEX_API_ENABLED=True)
    def test_enabled_admin_runs_sync(self, admin_client):
        r = admin_client.post("/api/es/reindex/")
        assert r.status_code == 200
        body = r.json()
        assert body["async"] is False
        # ES is disabled in tests, so every model is skipped, but the summary
        # shape is still returned.
        assert "results" in body and "models" in body


@pytest.mark.django_db
class TestReindexApiAuth:
    @override_settings(SNAPADMIN_REINDEX_API_ENABLED=True)
    def test_non_admin_forbidden(self, regular_client):
        assert regular_client.post("/api/es/reindex/").status_code == 403

    @override_settings(SNAPADMIN_REINDEX_API_ENABLED=True)
    def test_anonymous_denied(self):
        assert APIClient().post("/api/es/reindex/").status_code in (401, 403)

    def test_non_admin_forbidden_even_when_disabled(self, regular_client):
        # Permission (IsAdminUser) is checked before the enabled-gate, so a
        # non-admin never learns whether the feature is on.
        assert regular_client.post("/api/es/reindex/").status_code == 403


@pytest.mark.django_db
class TestReindexApiDispatch:
    @override_settings(SNAPADMIN_REINDEX_API_ENABLED=True)
    def test_chunk_size_forwarded_to_run_reindex(self, admin_client):
        with patch("snapadmin.api.reindex.run_reindex", return_value={"models": 0, "results": {}}) as m:
            r = admin_client.post("/api/es/reindex/", {"chunk_size": 250}, format="json")
        assert r.status_code == 200
        m.assert_called_once_with(chunk_size=250)

    @override_settings(SNAPADMIN_REINDEX_API_ENABLED=True)
    def test_invalid_chunk_size_falls_back(self, admin_client):
        with patch("snapadmin.api.reindex.run_reindex", return_value={"models": 0, "results": {}}) as m:
            admin_client.post("/api/es/reindex/", {"chunk_size": "junk"}, format="json")
        m.assert_called_once_with(chunk_size=500)

    @override_settings(SNAPADMIN_REINDEX_API_ENABLED=True, SNAPADMIN_REINDEX_API_ASYNC=True)
    def test_async_dispatches_task(self, admin_client):
        from snapadmin.tasks import run_es_reindex

        class _Result:
            id = "task-123"

        with patch.object(run_es_reindex, "delay", return_value=_Result()) as delay:
            r = admin_client.post("/api/es/reindex/", {"chunk_size": 100}, format="json")
        assert r.status_code == 202
        body = r.json()
        assert body["async"] is True
        assert body["task_id"] == "task-123"
        delay.assert_called_once_with(chunk_size=100)

    @override_settings(SNAPADMIN_REINDEX_API_ENABLED=True, SNAPADMIN_REINDEX_API_ASYNC=True)
    def test_async_without_celery_returns_503(self, admin_client):
        # Make ``from snapadmin.tasks import run_es_reindex`` raise ImportError,
        # exactly as a project without the celery extra would.
        with patch.dict(sys.modules, {"snapadmin.tasks": None}):
            r = admin_client.post("/api/es/reindex/")
        assert r.status_code == 503
        assert "celery" in r.json()["detail"].lower()


@pytest.mark.django_db
def test_run_reindex_helper_skips_when_es_disabled():
    # The shared helper aggregates per-model es_reindex_all summaries.
    from snapadmin.models import run_reindex

    summary = run_reindex(chunk_size=10)
    assert summary["models"] >= 1
    assert summary["errored_models"] == 0
    assert all(s.get("skipped") for s in summary["results"].values())


@pytest.mark.django_db
def test_run_reindex_counts_indexed_and_errored(monkeypatch):
    from demo.models import Product, SearchLog
    from snapadmin.models import run_reindex

    monkeypatch.setattr(
        Product, "es_reindex_all",
        classmethod(lambda cls, *, chunk_size=500: {"indexed": 5, "errors": []}),
    )
    monkeypatch.setattr(
        SearchLog, "es_reindex_all",
        classmethod(lambda cls, *, chunk_size=500: {"indexed": 0, "errors": ["boom"]}),
    )
    summary = run_reindex(chunk_size=10)
    assert summary["indexed_models"] >= 1   # Product
    assert summary["errored_models"] >= 1   # SearchLog


@pytest.mark.django_db
def test_run_es_reindex_task_runs_helper():
    # Exercise the Celery task body directly (eager) — it delegates to run_reindex.
    from snapadmin.tasks import run_es_reindex

    summary = run_es_reindex.apply(kwargs={"chunk_size": 10}).get()
    assert summary["models"] >= 1
    assert "results" in summary
