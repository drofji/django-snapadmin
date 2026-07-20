"""
``snapadmin-demo`` — the autonomous demo bootstrapper.

A stdlib-only command-line tool (console script ``snapadmin-demo`` and ``python -m
snapadmin.quickstart``) that brings up the SnapAdmin demo with no existing project: it downloads the
``demo/`` directory from the GitHub source tarball of a release tag (the wheel does not ship it),
caches it under ``~/.cache/snapadmin-demo/``, extracts it, installs its requirements, runs the
migrations and seed, and starts the server.

**It must not import Django at module-import time** — it runs before any project or settings exist,
and drives ``manage.py`` as a subprocess. It stays stdlib-only so it adds no runtime dependency.
"""

from __future__ import annotations

__all__ = ["QuickstartError", "TagNotFoundError"]


class QuickstartError(Exception):
    """A user-facing bootstrapper failure (printed without a traceback)."""


class TagNotFoundError(QuickstartError):
    """The requested release tag does not exist on GitHub."""

    def __init__(self, version: str, tags: list[str]):
        self.version = version
        self.tags = tags
        suggestion = f" Available tags: {', '.join(tags)}." if tags else ""
        super().__init__(
            f"No release tag 'v{version}' found on GitHub.{suggestion} "
            "Pass --version with one of the available tags and try again."
        )
