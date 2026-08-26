"""
snapadmin/manage_cli.py

Shell entry points that forward to SnapAdmin's ``manage.py`` commands.

``snapadmin_info`` and ``snapadmin_license_check`` are Django management commands, so they need a
configured project and cannot be console scripts themselves. But the package *also* ships two real
console scripts (``snapadmin-demo``, ``snapadmin-init``), and the two groups look identical in the
documentation — so typing ``snapadmin_info`` in a shell and getting ``command not found`` is an easy
and unhelpful mistake to make (reported from a real first run).

These shims close that gap: they walk up from the current directory looking for ``manage.py`` and
re-run the real command through it, forwarding every argument and the exit code. When there is no
project to run against they say so, and say what to do instead, rather than failing as a missing
binary.

Both spellings are registered — ``snapadmin-info`` (canonical, matching the dashed console scripts)
and ``snapadmin_info`` (matching the management-command name people actually read in the docs) —
because the whole point is that neither should be a dead end.

Like the other console scripts this module runs **before** any Django project exists, so it must
stay stdlib-only and must not import Django at module level.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: How far up the tree to look for manage.py. Deep enough for a project nested a few
#: directories down, shallow enough not to wander into an unrelated parent checkout.
MAX_PARENTS = 6


def find_manage_py(start: Path | None = None) -> Path | None:
    """Return the nearest ``manage.py`` at or above ``start`` (default: cwd)."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *list(current.parents)[:MAX_PARENTS]):
        candidate = directory / "manage.py"
        if candidate.is_file():
            return candidate
    return None


def forward(command: str, argv: list[str], *, start: Path | None = None) -> int:
    """Run ``python manage.py <command> <argv…>`` and return its exit code."""
    manage_py = find_manage_py(start)
    if manage_py is None:
        sys.stderr.write(
            f"snapadmin: no manage.py found in {(start or Path.cwd())} or its parents.\n"
            f"'{command}' is a Django management command, so it needs a project to inspect.\n"
            f"Run it from inside your project, or use: python manage.py {command}\n"
            "To try SnapAdmin without a project of your own, run: snapadmin-demo\n"
        )
        return 2
    return subprocess.call(
        [sys.executable, str(manage_py), command, *argv],
        cwd=str(manage_py.parent),
    )


def info_main(argv: list[str] | None = None) -> int:
    """Entry point for ``snapadmin-info`` / ``snapadmin_info``."""
    return forward("snapadmin_info", list(sys.argv[1:] if argv is None else argv))


def license_check_main(argv: list[str] | None = None) -> int:
    """Entry point for ``snapadmin-license-check`` / ``snapadmin_license_check``."""
    return forward("snapadmin_license_check", list(sys.argv[1:] if argv is None else argv))
