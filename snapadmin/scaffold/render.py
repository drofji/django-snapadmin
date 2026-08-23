"""
Template rendering for ``snapadmin-new``.

Every template lives under ``snapadmin/scaffold/templates/`` and is rendered with the
standard library's :class:`string.Template` (``$identifier`` / ``${identifier}``
placeholders) — no Jinja, no templating dependency. ``.tmpl`` is stripped from the source
filename to get the real output name; the handful of leading-underscore ``_*.tmpl`` files
are fragments (composed into a parent template's placeholder, never written out on their
own) so ``--full`` can extend a file — the Postgres/Redis/Elasticsearch block in
``settings.py``, the extra ``.env`` lines — without a second, mostly-duplicate template.

:func:`generate_project` uses :meth:`string.Template.safe_substitute` rather than the
strict ``substitute``: the Dockerfile and docker-compose templates legitimately contain
``$PATH`` and ``${VAR:-default}`` (Docker's own and Compose's own variable syntax), which
are not placeholders we resolve. ``safe_substitute`` leaves any ``$name`` this module does
not recognise untouched instead of raising, so those pass through to the generated file
exactly as written — the generated project's own end-to-end tests (``manage.py check`` /
``migrate``) are the safety net for a genuine typo in one of *our* placeholders, since an
unresolved ``$identifier`` left inside generated Python usually breaks import at that point.
"""

from __future__ import annotations

import secrets
import string
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

TEMPLATES_ROOT = Path(__file__).parent / "templates"

#: (template path relative to TEMPLATES_ROOT, output path relative to the project root).
#: Destination paths are themselves ``string.Template`` strings, so ``$project_name`` /
#: ``$app_name`` in a destination places the file under the right generated package.
COMMON_MANIFEST: list[tuple[str, str]] = [
    ("common/manage.py.tmpl", "manage.py"),
    ("common/gitignore.tmpl", ".gitignore"),
    ("common/env.tmpl", ".env"),
    ("common/dist_env.tmpl", "dist.env"),
    ("common/requirements.txt.tmpl", "requirements.txt"),
    ("common/README.md.tmpl", "README.md"),
    ("common/project/__init__.py.tmpl", "$project_name/__init__.py"),
    ("common/project/settings.py.tmpl", "$project_name/settings.py"),
    ("common/project/urls.py.tmpl", "$project_name/urls.py"),
    ("common/project/wsgi.py.tmpl", "$project_name/wsgi.py"),
    ("common/app/__init__.py.tmpl", "$app_name/__init__.py"),
    ("common/app/apps.py.tmpl", "$app_name/apps.py"),
    ("common/app/models.py.tmpl", "$app_name/models.py"),
    ("common/app/admin.py.tmpl", "$app_name/admin.py"),
    ("common/app/migrations/__init__.py.tmpl", "$app_name/migrations/__init__.py"),
    ("common/app/migrations/0001_initial.py.tmpl", "$app_name/migrations/0001_initial.py"),
]

#: Written only when ``--full`` is passed, on top of :data:`COMMON_MANIFEST`.
FULL_MANIFEST: list[tuple[str, str]] = [
    ("full/Dockerfile.tmpl", "Dockerfile"),
    ("full/dockerignore.tmpl", ".dockerignore"),
    ("full/docker-compose.yml.tmpl", "docker-compose.yml"),
]

#: Fragments composed into a COMMON_MANIFEST template's placeholder when ``--full`` is set.
#: Empty string in minimal mode — the placeholder they fill just disappears.
_FULL_FRAGMENTS: dict[str, str] = {
    "database_block": "full/_settings_database.tmpl",
    "optional_services_block": "full/_settings_services.tmpl",
    "full_env_extra": "full/_env_extra.tmpl",
    "full_readme_extra": "full/_readme_extra.tmpl",
    "full_requirements_extra": "full/_requirements_extra.tmpl",
}

#: The minimal (non-``--full``) counterpart for the one fragment every mode needs
#: (the database block — every project needs *some* ``DATABASES`` setting).
_COMMON_FRAGMENTS: dict[str, str] = {
    "database_block": "common/_settings_database_sqlite.tmpl",
}


def _installed_version() -> str:
    """The installed ``django-snapadmin`` version, or a dev fallback — same pattern as
    ``snapadmin.__version__``, duplicated here so this module needs no snapadmin import."""
    try:
        return _pkg_version("django-snapadmin")
    except PackageNotFoundError:
        return "0.0.0.dev0"


def _camel_case(name: str) -> str:
    """``"my_app"`` -> ``"MyApp"``; used to build an ``AppConfig`` class name."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def _render_file(template_rel: str, context: dict[str, str]) -> str:
    text = (TEMPLATES_ROOT / template_rel).read_text(encoding="utf-8")
    return string.Template(text).safe_substitute(context)


def _build_context(*, project_name: str, app_name: str, full: bool) -> dict[str, str]:
    context: dict[str, str] = {
        "project_name": project_name,
        "app_name": app_name,
        "app_class_name": _camel_case(app_name) + "Config",
        "secret_key": secrets.token_urlsafe(50),
        "snapadmin_version": _installed_version(),
    }
    # Fragments may themselves reference $project_name/$app_name, so render them against
    # the context built so far, then fold the *already-resolved* text in as a plain value —
    # the outer safe_substitute pass never re-scans a substituted value for placeholders,
    # so this is a single, well-defined expansion with no risk of double substitution.
    fragment_map = {**_COMMON_FRAGMENTS, **(_FULL_FRAGMENTS if full else {})}
    for key, template_rel in fragment_map.items():
        context[key] = _render_file(template_rel, context).rstrip("\n")
    for key in _FULL_FRAGMENTS:
        context.setdefault(key, "")
    return context


def generate_project(dest: Path, *, project_name: str, app_name: str, full: bool) -> list[Path]:
    """Render the full project tree into *dest* (already validated empty/absent).

    Returns every file written, in write order. ``manage.py`` is left executable, matching
    ``django-admin startproject``.
    """
    context = _build_context(project_name=project_name, app_name=app_name, full=full)
    manifest = COMMON_MANIFEST + (FULL_MANIFEST if full else [])

    written: list[Path] = []
    for template_rel, dest_rel_tmpl in manifest:
        dest_rel = string.Template(dest_rel_tmpl).safe_substitute(context)
        target = dest / dest_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_file(template_rel, context), encoding="utf-8")
        written.append(target)

    manage_py = dest / "manage.py"
    manage_py.chmod(manage_py.stat().st_mode | 0o111)
    return written
