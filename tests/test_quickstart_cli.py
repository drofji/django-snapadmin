"""Tests for :mod:`snapadmin.quickstart.cli` and the ``python -m`` entry point (#CLI3a)."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from snapadmin.quickstart import QuickstartError, TagNotFoundError, cli, extract, fetch, run, stamp


def _patch_pipeline(monkeypatch, **over):
    monkeypatch.setattr(fetch, "resolve_version", over.get("resolve", lambda v: "1.0.0"))
    monkeypatch.setattr(fetch, "download_demo", over.get("download", lambda v, **kw: Path("/x/v1.tar.gz")))
    monkeypatch.setattr(extract, "extract_demo", over.get("extract", lambda a, p, **kw: Path("/x/demo")))
    monkeypatch.setattr(run, "run_demo", over.get("run", lambda d, **kw: None))


class TestMain:
    def test_happy_path(self, monkeypatch):
        _patch_pipeline(monkeypatch)
        assert cli.main([]) == 0

    def test_forwards_flags(self, monkeypatch):
        captured: dict = {}
        _patch_pipeline(
            monkeypatch,
            download=lambda v, **kw: captured.update(download=kw) or Path("/x/a"),
            extract=lambda a, p, **kw: captured.update(extract=kw, path=str(p)) or Path("/x/demo"),
            run=lambda d, **kw: captured.update(run=kw),
        )
        code = cli.main(
            ["--clear-cache", "--yes", "--skip-install", "--no-serve", "--version", "2.0", "--path", "/tmp/z"]
        )
        assert code == 0
        assert captured["download"]["clear_cache"] is True
        assert captured["extract"]["assume_yes"] is True
        assert captured["extract"]["version"] == "1.0.0"
        assert captured["run"] == {"skip_install": True, "no_serve": True, "mode": "runserver"}
        assert captured["path"] == "/tmp/z"

    def test_tag_not_found_returns_2(self, monkeypatch, capsys):
        _patch_pipeline(
            monkeypatch,
            download=lambda v, **kw: (_ for _ in ()).throw(TagNotFoundError("9.9.9", ["v1"])),
        )
        assert cli.main([]) == 2
        assert "9.9.9" in capsys.readouterr().err

    def test_quickstart_error_returns_1(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch, resolve=lambda v: (_ for _ in ()).throw(QuickstartError("boom")))
        assert cli.main([]) == 1
        assert "boom" in capsys.readouterr().err


class TestParser:
    def test_defaults(self):
        args = cli.build_parser().parse_args([])
        assert args.path == "."
        assert args.skip_install is False
        assert args.yes is False


class TestConfigResolution:
    def test_no_flags_returns_none(self):
        args = cli.build_parser().parse_args([])
        assert cli._config_from_args(args) is None

    def test_flags_build_config(self):
        args = cli.build_parser().parse_args(
            ["--database", "postgresql", "--db-host", "h", "--no-elasticsearch", "--debug", "--no-secret-key"]
        )
        config = cli._config_from_args(args)
        assert config["database"] == "postgresql"
        assert config["db_host"] == "h"
        assert config["elasticsearch"] is False
        assert config["debug"] is True
        assert config["generate_secret_key"] is False

    def test_interactive_invokes_wizard(self, monkeypatch):
        monkeypatch.setattr("snapadmin.quickstart.wizard.run_wizard", lambda: {"mode": "docker"})
        args = cli.build_parser().parse_args(["--interactive"])
        assert cli._config_from_args(args) == {"mode": "docker"}

    def test_load_config(self, tmp_path):
        from snapadmin.quickstart import config as config_mod

        path = config_mod.save_config({"mode": "runserver", "database": "sqlite"}, tmp_path / "c.ini")
        args = cli.build_parser().parse_args(["--load-config", str(path)])
        assert cli._config_from_args(args)["mode"] == "runserver"


class TestConfigWiring:
    def test_writes_env_and_saves_config(self, monkeypatch, tmp_path):
        demo_dir = tmp_path / "out" / "demo"
        demo_dir.mkdir(parents=True)
        _patch_pipeline(monkeypatch, extract=lambda a, p, **kw: demo_dir)
        saved: dict = {}
        monkeypatch.setattr(
            "snapadmin.quickstart.config.save_config",
            lambda c, p: saved.update(config=c, path=str(p)),
        )
        code = cli.main(
            ["--database", "sqlite", "--admin-password", "s3cret", "--save-config", str(tmp_path / "team.ini"),
             "--skip-install", "--no-serve"]
        )
        assert code == 0
        assert (demo_dir / ".env").exists()
        assert saved["config"]["database"] == "sqlite"

    def test_mode_forwarded_to_run(self, monkeypatch, tmp_path):
        demo_dir = tmp_path / "demo"
        demo_dir.mkdir()
        captured: dict = {}
        _patch_pipeline(
            monkeypatch,
            extract=lambda a, p, **kw: demo_dir,
            run=lambda d, **kw: captured.update(kw),
        )
        cli.main(["--mode", "docker", "--skip-install", "--no-serve"])
        assert captured["mode"] == "docker"


class TestExistingTreeReport:
    """A tree left over from an older release is announced before it is touched (#CHK2b)."""

    def test_silent_when_there_is_no_tree_yet(self, tmp_path, capsys):
        cli._report_existing_tree(tmp_path / "demo", "2.0")
        assert capsys.readouterr().out == ""

    def test_names_both_versions_when_the_tree_is_older(self, tmp_path, capsys):
        demo = tmp_path / "demo"
        demo.mkdir()
        stamp.write_stamp(demo, "1.0", ["manage.py"])
        cli._report_existing_tree(demo, "2.0")
        assert "v1.0 → v2.0" in capsys.readouterr().out

    def test_says_so_when_the_tree_is_already_current(self, tmp_path, capsys):
        demo = tmp_path / "demo"
        demo.mkdir()
        stamp.write_stamp(demo, "2.0", ["manage.py"])
        cli._report_existing_tree(demo, "2.0")
        assert "already v2.0" in capsys.readouterr().out

    def test_flags_an_unstamped_tree(self, tmp_path, capsys):
        demo = tmp_path / "demo"
        demo.mkdir()
        cli._report_existing_tree(demo, "2.0")
        assert "unstamped" in capsys.readouterr().out

    def test_main_reports_the_tree_it_is_about_to_replace(self, monkeypatch, tmp_path, capsys):
        demo = tmp_path / "demo"
        demo.mkdir()
        stamp.write_stamp(demo, "0.1.0b5", ["manage.py"])
        _patch_pipeline(monkeypatch, extract=lambda a, p, **kw: demo)
        monkeypatch.setattr(fetch, "resolve_version", lambda v: "0.1.0b6")
        assert cli.main(["--path", str(tmp_path), "--skip-install", "--no-serve", "--yes"]) == 0
        assert "v0.1.0b5 → v0.1.0b6" in capsys.readouterr().out


class TestDunderMain:
    def test_module_entrypoint(self, monkeypatch):
        monkeypatch.setattr("snapadmin.quickstart.cli.main", lambda argv=None: 0)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("snapadmin.quickstart", run_name="__main__")
        assert exc.value.code == 0
