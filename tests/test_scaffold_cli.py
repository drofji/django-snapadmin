"""Tests for :mod:`snapadmin.scaffold.cli` and the ``python -m`` entry point (#SCAFF1c/d)."""

from __future__ import annotations

import runpy

import pytest

from snapadmin.scaffold import cli


class TestParser:
    def test_defaults(self):
        args = cli.build_parser().parse_args(["myshop"])
        assert args.project_name == "myshop"
        assert args.path == "."
        assert args.app_name == "catalog"
        assert args.full is False

    def test_full_flag(self):
        args = cli.build_parser().parse_args(["myshop", "--full"])
        assert args.full is True

    def test_overrides(self):
        args = cli.build_parser().parse_args(
            ["myshop", "--path", "/tmp/x", "--app-name", "storefront"]
        )
        assert args.path == "/tmp/x"
        assert args.app_name == "storefront"

    def test_project_name_is_required(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([])


class TestMain:
    def test_happy_path_minimal(self, tmp_path, capsys):
        code = cli.main(["myshop", "--path", str(tmp_path)])
        assert code == 0
        dest = tmp_path / "myshop"
        assert (dest / "manage.py").is_file()
        assert not (dest / "Dockerfile").exists()
        out = capsys.readouterr().out
        assert "Creating myshop" in out
        assert "python manage.py migrate" in out
        assert "http://127.0.0.1:8000/admin/" in out
        assert "docker compose" not in out

    def test_happy_path_full(self, tmp_path, capsys):
        code = cli.main(["myshop", "--path", str(tmp_path), "--full"])
        assert code == 0
        dest = tmp_path / "myshop"
        assert (dest / "Dockerfile").is_file()
        out = capsys.readouterr().out
        assert "docker compose up --build" in out

    def test_custom_app_name(self, tmp_path):
        cli.main(["myshop", "--path", str(tmp_path), "--app-name", "storefront"])
        assert (tmp_path / "myshop" / "storefront" / "models.py").is_file()

    def test_invalid_project_name_returns_1(self, tmp_path, capsys):
        code = cli.main(["not-valid", "--path", str(tmp_path)])
        assert code == 1
        assert "Error:" in capsys.readouterr().err
        assert not (tmp_path / "not-valid").exists()

    def test_invalid_app_name_returns_1(self, tmp_path, capsys):
        code = cli.main(["myshop", "--path", str(tmp_path), "--app-name", "not-valid"])
        assert code == 1
        assert "Error:" in capsys.readouterr().err

    def test_app_name_equal_to_project_name_returns_1(self, tmp_path, capsys):
        code = cli.main(["myshop", "--path", str(tmp_path), "--app-name", "myshop"])
        assert code == 1
        assert "same as the project name" in capsys.readouterr().err

    def test_non_empty_target_returns_1(self, tmp_path, capsys):
        dest = tmp_path / "myshop"
        dest.mkdir()
        (dest / "existing.txt").write_text("x")
        code = cli.main(["myshop", "--path", str(tmp_path)])
        assert code == 1
        assert "not empty" in capsys.readouterr().err
        assert not (dest / "manage.py").exists()

    def test_rerun_into_same_empty_directory_succeeds(self, tmp_path):
        """An empty (but existing) directory is not a conflict — only a non-empty one is."""
        dest = tmp_path / "myshop"
        dest.mkdir()
        assert cli.main(["myshop", "--path", str(tmp_path)]) == 0
        assert (dest / "manage.py").is_file()


class TestDunderMain:
    def test_module_entrypoint(self, monkeypatch):
        monkeypatch.setattr("snapadmin.scaffold.cli.main", lambda argv=None: 0)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("snapadmin.scaffold", run_name="__main__")
        assert exc.value.code == 0


class TestDeclaredInPyproject:
    def test_console_script_registered(self):
        tomllib = pytest.importorskip("tomllib")
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        assert data["tool"]["poetry"]["scripts"]["snapadmin-new"] == "snapadmin.scaffold.cli:main"


class TestNoDjangoAtImportTime:
    """Console scripts run before a project exists — see .claude/rules.md."""

    @pytest.mark.parametrize("module_name", ["__init__", "__main__", "cli", "render", "validate"])
    def test_module_source_has_no_django_import(self, module_name):
        from pathlib import Path

        import snapadmin.scaffold as pkg

        path = Path(pkg.__file__).parent / f"{module_name}.py"
        source = path.read_text(encoding="utf-8")
        assert "import django" not in source
        assert "from django" not in source

    def test_only_stdlib_imports(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.modules.pop('django', None);"
                "import snapadmin.scaffold.cli as m;"
                "assert 'django' not in sys.modules; print('ok')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout
