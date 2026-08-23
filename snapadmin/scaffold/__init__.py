"""
``snapadmin-new`` — generate a runnable Django + SnapAdmin project.

A stdlib-only command-line tool (console script ``snapadmin-new`` and ``python -m
snapadmin.scaffold``) that writes a project you keep — not a throwaway demo
(``snapadmin-demo``) and not a read-only report (``snapadmin-init``), an actual
starting point: ``manage.py``, a settings package, one app carrying a worked
``SnapModel`` example, SQLite, and a ``.env`` / ``dist.env``. ``python manage.py
migrate`` then ``python manage.py runserver`` work immediately — no Docker, no
manual edits. Pass ``--full`` to additionally write a ``Dockerfile``,
``docker-compose.yml`` and the PostgreSQL / Redis / Elasticsearch wiring for the
same project.

Templates ship inside the wheel under ``snapadmin/scaffold/templates/`` and are
rendered with the standard library's :mod:`string.Template` — no Jinja, no new
runtime dependency.

**It must not import Django at module-import time** — it runs before any project
or settings exist, like the sibling console scripts ``snapadmin-demo``
(:mod:`snapadmin.quickstart`) and ``snapadmin-init`` (:mod:`snapadmin.integrate`).
"""

from __future__ import annotations

__all__ = ["ScaffoldError"]


class ScaffoldError(Exception):
    """A user-facing scaffolding failure (printed without a traceback)."""
