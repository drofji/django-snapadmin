"""Tests for :mod:`snapadmin.integrate.steps` (#CLI4b/c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapadmin.integrate import steps
from snapadmin.integrate.detect import ProjectContext


def _ctx(*, settings_text="", urls_text="", requirements_text="", project_dir=Path("."), **kwargs):
    return ProjectContext(
        project_dir=project_dir,
        settings_path=None,
        settings_text=settings_text,
        urls_path=None,
        urls_text=urls_text,
        requirements_text=requirements_text,
        **kwargs,
    )


class TestInstalledApps:
    def test_present_with_unfold(self):
        step = steps.installed_apps_step(_ctx(settings_text='INSTALLED_APPS=["unfold","snapadmin"]'))
        assert step.present is True
        assert step.note == ""

    def test_missing_notes_unfold_is_optional(self):
        step = steps.installed_apps_step(_ctx(settings_text="INSTALLED_APPS=[]"))
        assert step.present is False
        # Unfold is now an optional theme, not a hard requirement — the note frames
        # it that way rather than demanding it precede django.contrib.admin.
        assert "unfold" in step.note
        assert "optional" in step.note


class TestUrls:
    def test_present(self):
        assert steps.urls_step(_ctx(urls_text="include('snapadmin.urls')")).present is True

    def test_missing_uses_prefix(self):
        step = steps.urls_step(_ctx(urls_text="", url_prefix="api/"))
        assert step.present is False
        assert 'path("api/", include("snapadmin.urls"))' in step.snippet


class TestSettingsAndApis:
    def test_settings_present(self):
        assert steps.settings_step(_ctx(settings_text="SNAPADMIN_REST_API_ENABLED=True")).present is True

    def test_rest_needs_both_tokens(self):
        assert steps.rest_step(_ctx(settings_text="rest_framework")).present is False
        assert steps.rest_step(_ctx(settings_text="rest_framework drf_spectacular")).present is True

    def test_graphql(self):
        assert steps.graphql_step(_ctx(settings_text="graphene_django")).present is True
        assert steps.graphql_step(_ctx(settings_text="")).present is False


class TestInstall:
    def test_present_with_extras(self):
        step = steps.install_step(_ctx(requirements_text="django-snapadmin", extras=["celery", "backup"]))
        assert step.present is True
        assert "django-snapadmin[celery,backup]" in step.snippet

    def test_missing(self):
        assert steps.install_step(_ctx(requirements_text="")).present is False

    @pytest.mark.parametrize(
        "req,conflict",
        [
            ("Django==4.2\n", True),
            ("Django==5\n", True),
            ("Django>=5.2\n", False),
            ("Django==6.0\n", False),
            ("flask==1.0\n", False),
        ],
    )
    def test_pin_conflict(self, req, conflict):
        assert bool(steps._django_pin_conflict(req)) is conflict


class TestModels:
    def test_finds_plain_models_and_skips_ignored_dirs(self, tmp_path):
        (tmp_path / "shop").mkdir()
        (tmp_path / "shop" / "models.py").write_text("class P(models.Model):\n    pass\n")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "models.py").write_text("class Q(models.Model):\n    pass\n")
        step = steps.models_step(_ctx(project_dir=tmp_path))
        assert step.present is False
        assert "shop/models.py" in step.note
        assert ".venv" not in step.note

    def test_clean_project(self, tmp_path):
        step = steps.models_step(_ctx(project_dir=tmp_path))
        assert step.present is True
        assert step.note == ""


class TestCheckProject:
    def test_base_steps(self, tmp_path):
        names = [s.name for s in steps.check_project(_ctx(project_dir=tmp_path))]
        assert names == [
            "installed_apps", "urls", "settings", "install", "models", "migrations",
            "pii_masking", "throttling", "pagination", "alerts", "backups", "backup_encryption",
            "restore_tested",
        ]

    def test_with_api_and_graphql(self, tmp_path):
        ctx = _ctx(project_dir=tmp_path, include_api=True, include_graphql=True)
        names = [s.name for s in steps.check_project(ctx)]
        assert "rest_api" in names and "graphql" in names
        assert "api_auth" in names

    def test_api_auth_only_with_api_flag(self, tmp_path):
        names = [s.name for s in steps.check_project(_ctx(project_dir=tmp_path))]
        assert "api_auth" not in names


class TestMigrationsStep:
    def test_always_not_checked(self, tmp_path):
        step = steps.migrations_step(_ctx(project_dir=tmp_path))
        assert step.present is None
        assert "not checked" in step.note.lower()
        assert step.group == "must_work"


class TestApiAuthStep:
    def test_present(self):
        step = steps.api_auth_step(_ctx(settings_text="SNAPADMIN_API_AUTHENTICATION_CLASSES = []"))
        assert step.present is True

    def test_missing(self):
        step = steps.api_auth_step(_ctx(settings_text=""))
        assert step.present is False
        assert step.group == "should_configure"


class TestMaskingStep:
    def test_present_via_masked_fields(self):
        assert steps.masking_step(_ctx(settings_text="SNAPADMIN_MASKED_FIELDS = {}")).present is True

    def test_present_via_masking_rules(self):
        assert steps.masking_step(_ctx(settings_text="SNAPADMIN_MASKING_RULES = {}")).present is True

    def test_missing(self):
        step = steps.masking_step(_ctx(settings_text=""))
        assert step.present is False
        assert step.group == "should_configure"


class TestThrottlingStep:
    def test_present(self):
        assert steps.throttling_step(_ctx(settings_text="SNAPADMIN_THROTTLE_ANON = '60/min'")).present is True

    def test_missing(self):
        assert steps.throttling_step(_ctx(settings_text="")).present is False


class TestPaginationStep:
    def test_present(self):
        step = steps.pagination_step(_ctx(settings_text="SNAPADMIN_API_PAGE_SIZE = 50"))
        assert step.present is True

    def test_missing(self):
        step = steps.pagination_step(_ctx(settings_text=""))
        assert step.present is False
        assert step.group == "should_configure"


class TestAlertsStep:
    def test_present(self):
        assert steps.alerts_step(_ctx(settings_text="SNAPADMIN_HEALTH_ALERT_EMAILS = []")).present is True

    def test_missing(self):
        step = steps.alerts_step(_ctx(settings_text=""))
        assert step.present is False
        assert step.group == "should_configure"


class TestBackupsStep:
    def test_present(self):
        assert steps.backups_step(_ctx(settings_text="SNAPADMIN_BACKUP_ENABLED = True")).present is True

    def test_missing(self):
        step = steps.backups_step(_ctx(settings_text=""))
        assert step.present is False
        assert step.group == "data_safety"


class TestBackupEncryptionStep:
    def test_present_has_no_note(self):
        step = steps.backup_encryption_step(_ctx(settings_text="SNAPADMIN_BACKUP_AGE_RECIPIENTS = []"))
        assert step.present is True
        assert step.note == ""

    def test_missing_recommends_it(self):
        step = steps.backup_encryption_step(_ctx(settings_text=""))
        assert step.present is False
        assert "recommended" in step.note.lower()
        assert step.group == "data_safety"


class TestRestoreTestedStep:
    def test_always_not_checked_no_command_invented(self, tmp_path):
        step = steps.restore_tested_step(_ctx(project_dir=tmp_path))
        assert step.present is None
        assert step.snippet == ""
        assert "no automated restore command yet" in step.note.lower()
        assert step.group == "data_safety"
