"""
Name and target-directory validation for ``snapadmin-new``.

Mirrors ``django.core.management.templates.TemplateCommand.validate_name`` — the guard
``django-admin startproject``/``startapp`` apply — in spirit: a project or app name must be
a valid Python identifier that does not shadow an already-importable module (stdlib,
Django, or any other installed package, ``snapadmin`` and ``django`` included). Reusing
:func:`importlib.util.find_spec` instead of hand-maintaining a stdlib name list keeps the
check accurate as Python's own module list changes across versions.

Stdlib-only, no Django import — this runs before any project exists.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

from snapadmin.scaffold import ScaffoldError


def validate_identifier_name(name: str, *, kind: str) -> None:
    """Raise :class:`ScaffoldError` unless *name* is safe to use as a Python package name.

    ``kind`` names the role in error messages (``"project"`` or ``"app"``).
    """
    if not name:
        raise ScaffoldError(f"You must provide a {kind} name.")
    if not name.isidentifier():
        raise ScaffoldError(
            f"{name!r} is not a valid {kind} name. Please make sure it is a valid Python "
            "identifier (letters, digits and underscores; it cannot start with a digit or "
            "be a Python keyword)."
        )
    try:
        conflict = find_spec(name) is not None
    except (ImportError, ValueError, TypeError):
        # A broken or partially installed package can make find_spec raise instead of
        # returning None — that is not evidence of a real conflict, so don't block on it.
        conflict = False
    if conflict:
        raise ScaffoldError(
            f"{name!r} conflicts with the name of an existing Python module and cannot be "
            f"used as a {kind} name. Please try another name."
        )


def validate_target_directory(path: Path) -> None:
    """Raise :class:`ScaffoldError` if *path* exists and already has files in it.

    ``snapadmin-new`` never overwrites — a non-empty target is a hard refusal, not a
    prompt, so a mistaken re-run can never silently clobber a project you already edited.
    """
    if not path.exists():
        return
    if not path.is_dir():
        raise ScaffoldError(f"{path} already exists and is not a directory.")
    if any(path.iterdir()):
        raise ScaffoldError(
            f"{path} is not empty — refusing to write into it (snapadmin-new never "
            "overwrites). Choose an empty or new directory."
        )
