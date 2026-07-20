"""Tests for :mod:`snapadmin.integrate.cli` + report rendering and the ``python -m`` entry (#CLI4a/d)."""

from __future__ import annotations

import json
import runpy

import pytest

from snapadmin.integrate import cli


def _integrated(tmp_path):
    pkg = tmp_path / "proj"
    pkg.mkdir()
    (pkg / "settings.py").write_text(
        'INSTALLED_APPS = ["unfold", "snapadmin", "rest_framework", "drf_spectacular", "graphene_django"]\n'
        "SNAPADMIN_REST_API_ENABLED = True\n"
    )
    (pkg / "urls.py").write_text("include('snapadmin.urls')")
    (tmp_path / "manage.py").write_text("os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proj.settings')")
    (tmp_path / "requirements.txt").write_text("django-snapadmin\nDjango==4.2\n")  # pin conflict → note
    return tmp_path


def _bare(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("class P(models.Model):\n    pass\n")
    return tmp_path


class TestCliText:
    def test_integrated_all_present(self, tmp_path, capsys):
        assert cli.main(["--path", str(_integrated(tmp_path)), "--api", "--graphql"]) == 0
        out = capsys.readouterr().out
        assert "already present" in out
        assert "changes nothing" in out
        assert "needs Django >= 5.2" in out  # present step carrying a note

    def test_bare_all_missing(self, tmp_path, capsys):
        assert cli.main(["--path", str(_bare(tmp_path))]) == 0
        out = capsys.readouterr().out
        assert "add this" in out
        assert "not found (pass --settings" in out
        assert "still subclass models.Model" in out  # missing advisory step with a note


class TestCliJson:
    def test_json(self, tmp_path, capsys):
        assert cli.main(["--path", str(_integrated(tmp_path)), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["settings"] is not None
        assert any(step["name"] == "installed_apps" for step in payload["steps"])


class TestCliMisc:
    def test_bad_path_returns_1(self, tmp_path, capsys):
        assert cli.main(["--path", str(tmp_path / "nope")]) == 1
        assert "Error" in capsys.readouterr().err

    def test_split_extras(self):
        assert cli._split_extras("a, b ,,c") == ["a", "b", "c"]
        assert cli._split_extras(None) == []

    def test_defaults(self):
        args = cli.build_parser().parse_args([])
        assert args.path == "."
        assert args.as_json is False


class TestDunderMain:
    def test_entrypoint(self, monkeypatch):
        monkeypatch.setattr("snapadmin.integrate.cli.main", lambda argv=None: 0)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("snapadmin.integrate", run_name="__main__")
        assert exc.value.code == 0
