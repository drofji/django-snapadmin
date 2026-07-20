"""Tests for :mod:`snapadmin.quickstart.run` (#CLI3d) — subprocess is always mocked."""

from __future__ import annotations

import subprocess

import pytest

from snapadmin.quickstart import QuickstartError, run


def _recorder():
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append(cmd)

    return calls, runner


def _joined(calls):
    return [" ".join(c) for c in calls]


class TestRunDemo:
    def test_full_flow(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        calls, runner = _recorder()
        run.run_demo(demo, runner=runner)
        joined = _joined(calls)
        assert any("pip" in c and "install" in c for c in joined)
        assert any("migrate" in c for c in joined)
        assert any("seed_demo" in c for c in joined)
        assert any("runserver" in c for c in joined)

    def test_skip_install(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        calls, runner = _recorder()
        run.run_demo(demo, skip_install=True, runner=runner)
        assert not any("pip" in c for c in _joined(calls))

    def test_no_serve(self, tmp_path, capsys):
        demo = tmp_path / "demo"
        demo.mkdir()
        calls, runner = _recorder()
        run.run_demo(demo, no_serve=True, runner=runner)
        assert not any("runserver" in c for c in _joined(calls))
        assert "skipping the server" in capsys.readouterr().out.lower()

    def test_docker_mode(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        calls, runner = _recorder()
        run.run_demo(demo, mode="docker", runner=runner)
        assert any("docker" in c for c in _joined(calls))

    def test_failed_step_raises(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()

        def runner(cmd, **kwargs):
            raise subprocess.CalledProcessError(2, cmd)

        with pytest.raises(QuickstartError, match="exit 2"):
            run.run_demo(demo, skip_install=True, runner=runner)

    def test_command_not_found_raises(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()

        def runner(cmd, **kwargs):
            raise FileNotFoundError("docker")

        with pytest.raises(QuickstartError, match="command not found"):
            run.run_demo(demo, mode="docker", skip_install=True, runner=runner)
