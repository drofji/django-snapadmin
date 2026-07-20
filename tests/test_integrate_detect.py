"""Tests for :mod:`snapadmin.integrate.detect` (#CLI4a)."""

from __future__ import annotations

import pytest

from snapadmin.integrate import IntegrateError, detect


class TestFindSettings:
    def test_explicit(self):
        assert detect.find_settings(__import__("pathlib").Path("."), "/x/settings.py").name == "settings.py"

    def test_from_manage_module(self, tmp_path):
        (tmp_path / "manage.py").write_text(
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')"
        )
        pkg = tmp_path / "myproj"
        pkg.mkdir()
        (pkg / "settings.py").write_text("X = 1")
        assert detect.find_settings(tmp_path, None) == pkg / "settings.py"

    def test_glob_fallback(self, tmp_path):
        pkg = tmp_path / "cfg"
        pkg.mkdir()
        (pkg / "settings.py").write_text("X = 1")
        assert detect.find_settings(tmp_path, None) == pkg / "settings.py"

    def test_none_found(self, tmp_path):
        assert detect.find_settings(tmp_path, None) is None

    def test_manage_without_module(self, tmp_path):
        (tmp_path / "manage.py").write_text("print('no settings module here')")
        assert detect.find_settings(tmp_path, None) is None


class TestFindUrls:
    def test_explicit(self, tmp_path):
        assert detect.find_urls(tmp_path, None, "/x/urls.py").name == "urls.py"

    def test_sibling_of_settings(self, tmp_path):
        pkg = tmp_path / "proj"
        pkg.mkdir()
        settings = pkg / "settings.py"
        settings.write_text("X = 1")
        (pkg / "urls.py").write_text("urlpatterns = []")
        assert detect.find_urls(tmp_path, settings, None) == pkg / "urls.py"

    def test_glob_fallback(self, tmp_path):
        pkg = tmp_path / "proj"
        pkg.mkdir()
        (pkg / "urls.py").write_text("urlpatterns = []")
        assert detect.find_urls(tmp_path, None, None) == pkg / "urls.py"

    def test_none_found(self, tmp_path):
        assert detect.find_urls(tmp_path, None, None) is None


class TestBuildContext:
    def test_reads_everything(self, tmp_path):
        pkg = tmp_path / "proj"
        pkg.mkdir()
        (pkg / "settings.py").write_text("INSTALLED_APPS = ['snapadmin']")
        (pkg / "urls.py").write_text("include('snapadmin.urls')")
        (tmp_path / "requirements.txt").write_text("django-snapadmin\n")
        ctx = detect.build_context(project_dir=str(tmp_path), extras=["celery"], include_api=True)
        assert "snapadmin" in ctx.settings_text
        assert "snapadmin.urls" in ctx.urls_text
        assert "django-snapadmin" in ctx.requirements_text
        assert ctx.extras == ["celery"]
        assert ctx.include_api is True

    def test_missing_files_are_blank(self, tmp_path):
        ctx = detect.build_context(project_dir=str(tmp_path))
        assert ctx.settings_text == ""
        assert ctx.settings_path is None

    def test_not_a_directory(self, tmp_path):
        with pytest.raises(IntegrateError, match="Not a directory"):
            detect.build_context(project_dir=str(tmp_path / "nope"))
