"""Tests for Celery task discoverability and graceful degradation (#B13).

The background tasks used to live in ``snapadmin/api/tasks.py`` under names like
``api.tasks.purge_expired_data``. Because Celery's ``autodiscover_tasks()`` only
scans ``<app>/tasks.py`` for each installed app, those tasks were never found by
a standard Celery setup and Beat schedules silently produced "unregistered task"
errors. The tasks now live in ``snapadmin/tasks.py`` (autodiscoverable) under the
``snapadmin.*`` namespace, and the async-export endpoint fails with an
actionable 503 instead of a raw 500 when Celery is not installed.
"""
import sys
from unittest.mock import patch

import pytest


class TestTaskModuleIsDiscoverable:
    def test_snapadmin_tasks_module_exists(self):
        import importlib

        assert importlib.util.find_spec("snapadmin.tasks") is not None

    def test_all_tasks_importable_from_snapadmin_tasks(self):
        from snapadmin.tasks import (  # noqa: F401
            purge_expired_data,
            purge_expired_tokens,
            run_db_backups,
            run_export,
            send_error_digest,
        )


class TestTaskNamesAreNamespaced:
    @pytest.mark.parametrize(
        "attr, expected_name",
        [
            ("purge_expired_tokens", "snapadmin.purge_expired_tokens"),
            ("purge_expired_data", "snapadmin.purge_expired_data"),
            ("send_error_digest", "snapadmin.send_error_digest"),
            ("run_export", "snapadmin.run_export"),
            ("run_db_backups", "snapadmin.run_db_backups"),
        ],
    )
    def test_task_name_is_snapadmin_namespaced(self, attr, expected_name):
        import snapadmin.tasks as tasks

        assert getattr(tasks, attr).name == expected_name

    def test_no_legacy_api_tasks_module(self):
        import importlib

        assert importlib.util.find_spec("snapadmin.api.tasks") is None


@pytest.mark.django_db
class TestExportEndpointWithoutCelery:
    def test_create_returns_503_when_celery_missing(self, auth_client, product):
        # Simulate Celery not being importable: setting the module to None in
        # sys.modules makes ``from snapadmin.tasks import run_export`` raise
        # ImportError, exactly as a project without the celery extra would.
        with patch.dict(sys.modules, {"snapadmin.tasks": None}):
            r = auth_client.post(
                "/api/exports/",
                {"app_label": "demo", "model": "Product", "export_format": "csv"},
                format="json",
            )
        assert r.status_code == 503
        assert "celery" in r.data["detail"].lower()

    def test_create_still_works_with_celery(self, auth_client, product):
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "export_format": "csv"},
            format="json",
        )
        assert r.status_code == 201, r.content
