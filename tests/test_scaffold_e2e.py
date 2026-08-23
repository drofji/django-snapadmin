"""
End-to-end tests for ``snapadmin-new`` (#SCAFF1d): generate a project into a tmpdir and
actually run it — ``manage.py check``, ``migrate``, then confirm the admin (and the REST
API SnapAdmin builds from the worked model) responds. Covers both the default (minimal)
and ``--full`` modes.

These shell out to a real ``manage.py`` in a subprocess rather than importing the
generated settings into the current process: pytest-django has already configured Django
once for this process (``demo.core.settings_test``), and Django's app registry is a
global singleton that cannot be reconfigured with a second, unrelated ``INSTALLED_APPS``
— a subprocess is the only clean way to actually boot the generated project.

``django-snapadmin`` is not ``pip install``-ed into this dev checkout (only the local
source tree is on ``sys.path``, resolved via the current working directory when pytest is
invoked from the repo root — see ``demo/manage.py``'s own comment for the same trick).
The *generated* project's ``manage.py`` has no such trick, matching what a real
``pip install django-snapadmin`` user's project looks like, so these subprocesses are
given ``PYTHONPATH=<repo root>`` explicitly — a test-environment concern, not something
``snapadmin-new`` itself needs to do.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from snapadmin.scaffold import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
_TIMEOUT = 90


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
    # pytest-django exports DJANGO_SETTINGS_MODULE (demo.core.settings_test) into this
    # process's environment for the whole session. The generated manage.py only sets it
    # with os.environ.setdefault(...), so inheriting that value here would make it a
    # no-op — the subprocess would silently run against the *main suite's* settings
    # (and its in-memory test database) instead of the generated project's own.
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env.update(extra or {})
    return env


def _run(args: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=_subprocess_env(extra_env),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )


def _generate(tmp_path: Path, name: str, *, full: bool = False) -> Path:
    argv = [name, "--path", str(tmp_path)]
    if full:
        argv.append("--full")
    assert cli.main(argv) == 0
    return tmp_path / name


def _check_and_migrate(dest: Path) -> None:
    check = _run(["manage.py", "check"], cwd=dest)
    assert check.returncode == 0, check.stdout + check.stderr

    migrate = _run(["manage.py", "migrate", "--noinput"], cwd=dest)
    assert migrate.returncode == 0, migrate.stdout + migrate.stderr
    assert (dest / "db.sqlite3").is_file()

    dry_run = _run(["manage.py", "makemigrations", "--check", "--dry-run"], cwd=dest)
    assert dry_run.returncode == 0, "the worked model should need no further migration:\n" + dry_run.stdout


_ADMIN_PROBE = """
import django
django.setup()
from django.test import Client

# django.test.Client defaults to Host: testserver, which ALLOWED_HOSTS (deliberately
# scoped to localhost/127.0.0.1 in the generated settings) rejects with 400 — that
# rejection is correct production behaviour, not something the generated project
# should special-case, so the probe supplies a Host ALLOWED_HOSTS already accepts.
client = Client(SERVER_NAME="127.0.0.1")

r1 = client.get("/admin/")
assert r1.status_code in (200, 302), f"/admin/ -> {r1.status_code}"

r2 = client.get("/admin/login/")
assert r2.status_code == 200, f"/admin/login/ -> {r2.status_code}"
assert b"admin" in r2.content.lower()

r3 = client.get("/api/schema/")
assert r3.status_code == 200, f"/api/schema/ -> {r3.status_code}: {r3.content[:300]!r}"

print("ADMIN_OK")
"""


def _assert_admin_responds(dest: Path, project_name: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _ADMIN_PROBE],
        cwd=dest,
        env=_subprocess_env({"DJANGO_SETTINGS_MODULE": f"{project_name}.settings"}),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ADMIN_OK" in result.stdout


class TestGeneratedProjectMinimal:
    def test_check_migrate_and_admin_responds(self, tmp_path):
        dest = _generate(tmp_path, "genminimal")
        assert not (dest / "Dockerfile").exists()
        _check_and_migrate(dest)
        _assert_admin_responds(dest, "genminimal")

    def test_custom_app_name_also_boots(self, tmp_path):
        dest = tmp_path / "genminimal2"
        assert cli.main(
            ["genminimal2", "--path", str(tmp_path), "--app-name", "storefront"]
        ) == 0
        _check_and_migrate(dest)
        _assert_admin_responds(dest, "genminimal2")


class TestGeneratedProjectFull:
    def test_check_migrate_and_admin_responds(self, tmp_path):
        """--full still falls back to SQLite with no Postgres/Redis/ES services running —
        the docker-compose stack is what provisions those, not manage.py check/migrate."""
        dest = _generate(tmp_path, "genfull", full=True)
        assert (dest / "Dockerfile").is_file()
        assert (dest / "docker-compose.yml").is_file()
        _check_and_migrate(dest)
        _assert_admin_responds(dest, "genfull")
