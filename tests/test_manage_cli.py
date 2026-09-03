"""
tests/test_manage_cli.py — the ``snapadmin-info`` / ``snapadmin-license-check`` shims (#CLI6)

``snapadmin_info`` is a Django management command, but SnapAdmin also ships real console scripts
(``snapadmin-demo``, ``snapadmin-init``) and the docs list both groups together — so typing
``snapadmin_info`` in a shell and getting ``command not found`` is an easy mistake (reported from a
real first run). These shims find the project's ``manage.py`` and forward to it.

Like the other console scripts they must stay importable with no Django configured.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from snapadmin import manage_cli


@pytest.fixture
def recorded(monkeypatch):
    """Capture the subprocess call instead of running it."""
    calls = []

    def fake_call(cmd, cwd=None):
        calls.append({"cmd": cmd, "cwd": cwd})
        return 0

    monkeypatch.setattr(manage_cli.subprocess, "call", fake_call)
    return calls


# ── locating manage.py ───────────────────────────────────────────────────────

class TestFindManagePy:
    def test_found_in_the_start_directory(self, tmp_path):
        (tmp_path / "manage.py").write_text("#")
        assert manage_cli.find_manage_py(tmp_path) == (tmp_path / "manage.py").resolve()

    def test_found_in_a_parent(self, tmp_path):
        (tmp_path / "manage.py").write_text("#")
        nested = tmp_path / "apps" / "shop"
        nested.mkdir(parents=True)
        assert manage_cli.find_manage_py(nested) == (tmp_path / "manage.py").resolve()

    def test_absent(self, tmp_path):
        assert manage_cli.find_manage_py(tmp_path) is None

    def test_a_directory_named_manage_py_is_not_a_project(self, tmp_path):
        (tmp_path / "manage.py").mkdir()
        assert manage_cli.find_manage_py(tmp_path) is None

    def test_search_depth_is_bounded(self, tmp_path, monkeypatch):
        """It must not wander up into an unrelated parent checkout."""
        (tmp_path / "manage.py").write_text("#")
        deep = tmp_path.joinpath(*[f"d{i}" for i in range(manage_cli.MAX_PARENTS + 2)])
        deep.mkdir(parents=True)
        assert manage_cli.find_manage_py(deep) is None

    def test_defaults_to_the_current_directory(self, tmp_path, monkeypatch):
        (tmp_path / "manage.py").write_text("#")
        monkeypatch.chdir(tmp_path)
        assert manage_cli.find_manage_py() is not None


# ── forwarding ───────────────────────────────────────────────────────────────

class TestForward:
    def test_runs_the_command_through_manage_py(self, tmp_path, recorded):
        (tmp_path / "manage.py").write_text("#")
        assert manage_cli.forward("snapadmin_info", [], start=tmp_path) == 0

        cmd = recorded[0]["cmd"]
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("manage.py")
        assert cmd[2] == "snapadmin_info"

    def test_arguments_are_passed_through(self, tmp_path, recorded):
        (tmp_path / "manage.py").write_text("#")
        manage_cli.forward("snapadmin_info", ["--section", "features"], start=tmp_path)
        assert recorded[0]["cmd"][3:] == ["--section", "features"]

    def test_runs_from_the_project_directory(self, tmp_path, recorded):
        (tmp_path / "manage.py").write_text("#")
        nested = tmp_path / "deep"
        nested.mkdir()
        manage_cli.forward("snapadmin_info", [], start=nested)
        assert recorded[0]["cwd"] == str(tmp_path.resolve())

    def test_exit_code_is_propagated(self, tmp_path, monkeypatch):
        """--health-check exits non-zero; the shim must not swallow that."""
        (tmp_path / "manage.py").write_text("#")
        monkeypatch.setattr(manage_cli.subprocess, "call", lambda cmd, cwd=None: 3)
        assert manage_cli.forward("snapadmin_info", [], start=tmp_path) == 3


class TestNoProjectFound:
    def test_exit_code_two(self, tmp_path):
        assert manage_cli.forward("snapadmin_info", [], start=tmp_path) == 2

    def test_message_explains_what_to_do(self, tmp_path, capsys):
        manage_cli.forward("snapadmin_info", [], start=tmp_path)
        err = capsys.readouterr().err
        assert "no manage.py found" in err
        assert "python manage.py snapadmin_info" in err
        assert "snapadmin-demo" in err            # the way to try it with no project


# ── entry points ─────────────────────────────────────────────────────────────

class TestEntryPoints:
    def test_info_targets_the_right_command(self, tmp_path, monkeypatch, recorded):
        (tmp_path / "manage.py").write_text("#")
        monkeypatch.chdir(tmp_path)
        assert manage_cli.info_main([]) == 0
        assert recorded[0]["cmd"][2] == "snapadmin_info"

    def test_license_check_targets_the_right_command(self, tmp_path, monkeypatch, recorded):
        (tmp_path / "manage.py").write_text("#")
        monkeypatch.chdir(tmp_path)
        assert manage_cli.license_check_main(["--json"]) == 0
        assert recorded[0]["cmd"][2] == "snapadmin_license_check"
        assert recorded[0]["cmd"][3:] == ["--json"]

    def test_argv_defaults_to_sys_argv(self, tmp_path, monkeypatch, recorded):
        (tmp_path / "manage.py").write_text("#")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["snapadmin-info", "--brief"])
        manage_cli.info_main()
        assert recorded[0]["cmd"][3:] == ["--brief"]

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is stdlib only on Python 3.11+")
    def test_underscored_spelling_removed_at_1_0(self):
        """The underscored duplicate (snapadmin_info / snapadmin_license_check) was
        a deprecated alias slated for removal in 1.0 (SECURITY.md's API-stability
        table) — `manage.py snapadmin_info` and the dashed console scripts stay."""
        import tomllib

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        scripts = data["tool"]["poetry"]["scripts"]
        assert "snapadmin_info" not in scripts
        assert "snapadmin_license_check" not in scripts

    @pytest.mark.parametrize("script,target", [
        ("snapadmin-info", "snapadmin.manage_cli:info_main"),
        ("snapadmin-license-check", "snapadmin.manage_cli:license_check_main"),
    ])
    def test_declared_in_pyproject(self, script, target):
        """Both spellings ship — neither should be a 'command not found' dead end."""
        tomllib = pytest.importorskip("tomllib")   # 3.11+ stdlib
        root = Path(__file__).resolve().parent.parent
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        assert data["tool"]["poetry"]["scripts"][script] == target


class TestNoDjangoAtImportTime:
    def test_module_imports_without_django_settings(self):
        """Console scripts run before a Django project exists, so they must stay stdlib-only."""
        source = Path(manage_cli.__file__).read_text(encoding="utf-8")
        assert "import django" not in source
        assert "from django" not in source

    def test_only_stdlib_imports(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.modules.pop('django', None);"
             "import snapadmin.manage_cli as m;"
             "assert 'django' not in sys.modules; print('ok')"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout
