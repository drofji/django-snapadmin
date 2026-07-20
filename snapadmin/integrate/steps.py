"""
The integration checks — each detects whether one piece of SnapAdmin wiring is present and carries
the snippet to paste when it isn't. Nothing here writes to the project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from snapadmin.integrate.detect import ProjectContext

_SKIP_DIRS = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".staticfiles", "snapadmin", "migrations"}

_INSTALLED_APPS_SNIPPET = (
    "INSTALLED_APPS = [\n"
    '    "unfold", "unfold.contrib.filters", "unfold.contrib.forms", "unfold.contrib.inlines",\n'
    '    "django.contrib.admin",  # must come after unfold\n'
    '    "django.contrib.auth", "django.contrib.contenttypes",\n'
    '    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",\n'
    '    "rest_framework", "drf_spectacular", "django_filters", "graphene_django",\n'
    '    "snapadmin",\n'
    "    # your apps …\n"
    "]"
)

_SETTINGS_SNIPPET = (
    "# SnapAdmin — every surface is a toggle (disabling one removes its URL routes)\n"
    "SNAPADMIN_REST_API_ENABLED = True\n"
    "SNAPADMIN_GRAPHQL_ENABLED = True\n"
    "SNAPADMIN_SWAGGER_ENABLED = True\n"
    'SNAPADMIN_URL_PREFIX = ""'
)

_REST_SNIPPET = (
    "REST_FRAMEWORK = {\n"
    '    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",\n'
    '    "DEFAULT_AUTHENTICATION_CLASSES": [\n'
    '        "snapadmin.api.authentication.APITokenAuthentication",\n'
    '        "rest_framework.authentication.SessionAuthentication",\n'
    "    ],\n"
    "}"
)

_GRAPHQL_SNIPPET = (
    'Add "graphene_django" to INSTALLED_APPS. SnapAdmin generates and mounts the GraphQL\n'
    "schema through its own URLs — no GRAPHENE setting is required."
)

_MODELS_SNIPPET = (
    "# Convert a model to get the admin, REST/GraphQL API and search for free:\n"
    "from snapadmin import models as snap_models, fields as snap\n"
    "class Product(snap_models.SnapModel):   # was: models.Model\n"
    "    name = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)  # was models.CharField"
)


@dataclass
class Step:
    name: str
    title: str
    present: bool
    snippet: str
    note: str = ""


def _has(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def installed_apps_step(ctx: ProjectContext) -> Step:
    present = _has(ctx.settings_text, '"snapadmin"', "'snapadmin'")
    note = "" if _has(ctx.settings_text, "unfold") else "'unfold' must be listed before 'django.contrib.admin'."
    return Step("installed_apps", "INSTALLED_APPS", present, _INSTALLED_APPS_SNIPPET, note)


def urls_step(ctx: ProjectContext) -> Step:
    present = "snapadmin.urls" in ctx.urls_text
    snippet = (
        "from django.urls import include, path\n\n"
        "urlpatterns = [\n"
        f'    path("{ctx.url_prefix}", include("snapadmin.urls")),\n'
        "    # …your other routes\n"
        "]"
    )
    return Step("urls", "URL routes", present, snippet)


def settings_step(ctx: ProjectContext) -> Step:
    present = "SNAPADMIN_" in ctx.settings_text
    return Step("settings", "SnapAdmin settings", present, _SETTINGS_SNIPPET)


def rest_step(ctx: ProjectContext) -> Step:
    present = _has(ctx.settings_text, "rest_framework") and _has(ctx.settings_text, "drf_spectacular")
    return Step("rest_api", "REST framework config", present, _REST_SNIPPET)


def graphql_step(ctx: ProjectContext) -> Step:
    present = "graphene_django" in ctx.settings_text
    return Step("graphql", "GraphQL config", present, _GRAPHQL_SNIPPET)


def _django_pin_conflict(text: str) -> str:
    match = re.search(r"^Django\s*[=<>~!]=?\s*([\d.]+)", text, re.MULTILINE | re.IGNORECASE)
    if not match:
        return ""
    parts = match.group(1).split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    if (major, minor) < (5, 2):
        return f"Your requirements pin Django {match.group(1)}; SnapAdmin needs Django >= 5.2."
    return ""


def install_step(ctx: ProjectContext) -> Step:
    present = "django-snapadmin" in ctx.requirements_text
    extras = f"[{','.join(ctx.extras)}]" if ctx.extras else ""
    return Step(
        "install",
        "Install django-snapadmin",
        present,
        f"pip install django-snapadmin{extras}",
        _django_pin_conflict(ctx.requirements_text),
    )


def _plain_model_files(project_dir) -> list:
    hits = []
    for path in project_dir.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if "(models.Model)" in path.read_text(errors="ignore"):
            hits.append(path.relative_to(project_dir))
    return sorted(hits)


def models_step(ctx: ProjectContext) -> Step:
    files = _plain_model_files(ctx.project_dir)
    note = ""
    if files:
        listed = ", ".join(str(path) for path in files[:5])
        note = f"{len(files)} file(s) still subclass models.Model: {listed}"
    return Step("models", "Model conversion (advisory)", not files, _MODELS_SNIPPET, note)


def check_project(ctx: ProjectContext) -> list[Step]:
    steps = [installed_apps_step(ctx), urls_step(ctx), settings_step(ctx)]
    if ctx.include_api:
        steps.append(rest_step(ctx))
    if ctx.include_graphql:
        steps.append(graphql_step(ctx))
    steps.append(install_step(ctx))
    steps.append(models_step(ctx))
    return steps
