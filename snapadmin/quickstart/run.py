"""
Drive the extracted demo: install requirements, migrate, seed, and serve.

Every step shells out to ``manage.py`` (or ``pip`` / ``docker``) as a subprocess — the bootstrapper
never imports Django in-process. ``runner`` defaults to :func:`subprocess.run` and is injected in the
tests so nothing is actually executed. A failed step is turned into a clean :class:`QuickstartError`
rather than a traceback.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from snapadmin.quickstart import QuickstartError


def _step(message: str) -> None:
    print(f"→ {message}…")


def run_demo(
    demo_dir: Path,
    *,
    skip_install: bool = False,
    no_serve: bool = False,
    mode: str = "runserver",
    python_exe: str | None = None,
    runner: Callable[..., object] | None = None,
) -> None:
    """Install/migrate/seed the extracted demo and (unless ``no_serve``) start the server."""
    runner = runner or subprocess.run
    python_exe = python_exe or sys.executable
    demo_dir = Path(demo_dir)
    manage = str(demo_dir / "manage.py")
    workdir = str(demo_dir.parent)

    def _run(cmd: list[str], description: str) -> None:
        _step(description)
        try:
            runner(cmd, cwd=workdir, check=True)
        except subprocess.CalledProcessError as exc:
            raise QuickstartError(f"{description} failed (exit {exc.returncode}).")
        except FileNotFoundError as exc:
            raise QuickstartError(f"{description} failed — command not found: {exc}")

    if not skip_install:
        _run(
            [python_exe, "-m", "pip", "install", "-r", str(demo_dir / "requirements.txt")],
            "Installing dependencies (this modifies the current environment)",
        )
    _run([python_exe, manage, "migrate"], "Applying migrations")
    _run([python_exe, manage, "seed_demo"], "Seeding demo data")

    if no_serve:
        _step("Prepared — skipping the server (--no-serve)")
        return

    if mode == "docker":
        _run(
            ["docker", "compose", "-f", str(demo_dir / "docker-compose.yml"), "up", "--build"],
            "Starting the Docker stack",
        )
    else:
        _run([python_exe, manage, "runserver"], "Starting the demo server at http://localhost:8000")
