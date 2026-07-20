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

    def test_missing_and_warns_about_unfold(self):
        step = steps.installed_apps_step(_ctx(settings_text="INSTALLED_APPS=[]"))
        assert step.present is False
        assert "unfold" in step.note


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
        assert names == ["installed_apps", "urls", "settings", "install", "models"]

    def test_with_api_and_graphql(self, tmp_path):
        ctx = _ctx(project_dir=tmp_path, include_api=True, include_graphql=True)
        names = [s.name for s in steps.check_project(ctx)]
        assert "rest_api" in names and "graphql" in names
