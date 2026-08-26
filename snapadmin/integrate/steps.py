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
    "    # Optional themed UI — pip install django-snapadmin[theme]. If you use Unfold,\n"
    '    # its apps must be listed before "django.contrib.admin":\n'
    '    "unfold", "unfold.contrib.filters", "unfold.contrib.forms", "unfold.contrib.inlines",\n'
    '    "django.contrib.admin",\n'
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
    #: ``True`` (✅, present) / ``False`` (❌, missing) / ``None`` (⚠️, not checked —
    #: this doctor is stdlib-only and runs before a Django project exists, so it
    #: cannot tell without importing Django and touching a live database; the
    #: ``note`` names the live command that can, e.g. ``snapadmin_info --health-check``).
    #: Never reinterpret ``None`` as ``False`` — that would be exactly the false
    #: green this doctor is required to never print (see ``rules.md``).
    present: bool | None
    snippet: str
    note: str = ""
    #: Which group of the integration checklist (docs `#integration-checklist`) this
    #: row belongs to, so the report can render the same four groups the docs do.
    group: str = "must_work"


#: Display order + heading for each :attr:`Step.group` value.
GROUPS: tuple[tuple[str, str], ...] = (
    ("must_work", "Must work"),
    ("should_configure", "Should be configured before production"),
    ("data_safety", "Data safety"),
)


def _has(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def installed_apps_step(ctx: ProjectContext) -> Step:
    present = _has(ctx.settings_text, '"snapadmin"', "'snapadmin'")
    note = "" if _has(ctx.settings_text, "unfold") else (
        "The 'unfold' theme is optional (pip install django-snapadmin[theme]); without it "
        "SnapAdmin renders on Django's built-in admin. If you add it, list the unfold apps "
        "before 'django.contrib.admin'."
    )
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


def migrations_step(ctx: ProjectContext) -> Step:
    """Whether pending migrations exist — genuinely not checkable here.

    Answering this needs Django's app registry and a database connection, both
    of which this doctor deliberately never touches (stdlib-only, read-only,
    runs before a project may even be configured). Always reports "not
    checked" rather than guessing — a degraded row, never a false green.
    """
    return Step(
        "migrations",
        "Migrations applied",
        None,
        "python manage.py migrate --check",
        "Not checked — needs a live database connection. Run the command in the snippet; "
        "it exits non-zero if anything is unapplied, without applying it.",
        group="must_work",
    )


def api_auth_step(ctx: ProjectContext) -> Step:
    present = "SNAPADMIN_API_AUTHENTICATION_CLASSES" in ctx.settings_text
    snippet = (
        "SNAPADMIN_API_AUTHENTICATION_CLASSES = [\n"
        '    "rest_framework_simplejwt.authentication.JWTAuthentication",  # or your own\n'
        '    "snapadmin.api.authentication.APITokenAuthentication",        # keep tokens working too\n'
        "]"
    )
    return Step(
        "api_auth", "Authentication on the API", present, snippet,
        "Defaults to SnapAdmin's own token auth if unset — fine to ship, but confirm it's the "
        "authenticator you actually want.",
        group="should_configure",
    )


def masking_step(ctx: ProjectContext) -> Step:
    present = _has(ctx.settings_text, "SNAPADMIN_MASKED_FIELDS", "SNAPADMIN_MASKING_RULES")
    snippet = (
        'SNAPADMIN_MASKED_FIELDS = {"shop.customer": ["email", "phone"]}\n'
        "# or SNAPADMIN_MASKING_RULES for per-permission unmasking"
    )
    return Step(
        "pii_masking", "PII masking configured for sensitive fields", present, snippet,
        group="should_configure",
    )


def throttling_step(ctx: ProjectContext) -> Step:
    present = _has(ctx.settings_text, "SNAPADMIN_THROTTLE_ANON", "SNAPADMIN_THROTTLE_USER")
    snippet = (
        'SNAPADMIN_THROTTLE_ANON = "60/min"\n'
        'SNAPADMIN_THROTTLE_USER = "600/min"'
    )
    return Step(
        "throttling", "API throttling configured", present, snippet,
        "Ships with a default rate even if unset — this row flags an explicit choice, not a gap.",
        group="should_configure",
    )


def pagination_step(ctx: ProjectContext) -> Step:
    present = "SNAPADMIN_API_PAGE_SIZE" in ctx.settings_text
    snippet = "SNAPADMIN_API_PAGE_SIZE = 25   # default; lower for wide rows, raise for small ones"
    return Step(
        "pagination", "API page size configured", present, snippet,
        "Ships with a default (25) even if unset — this row flags an explicit choice, not a gap.",
        group="should_configure",
    )


def alerts_step(ctx: ProjectContext) -> Step:
    present = _has(ctx.settings_text, "SNAPADMIN_HEALTH_ALERT_EMAILS", "SNAPADMIN_ERROR_ALERT_EMAILS", "SNAPADMIN_ALERT_")
    snippet = (
        'SNAPADMIN_HEALTH_ALERT_EMAILS = ["ops@example.com"]\n'
        "# or SNAPADMIN_ALERT_SLACK_WEBHOOK / _DISCORD_WEBHOOK / _TEAMS_WEBHOOK / _TELEGRAM_*"
    )
    return Step(
        "alerts", "Error and health alerts wired to a real channel", present, snippet,
        group="should_configure",
    )


def backups_step(ctx: ProjectContext) -> Step:
    present = "SNAPADMIN_BACKUP_ENABLED" in ctx.settings_text
    snippet = (
        "SNAPADMIN_BACKUP_ENABLED = True\n"
        "SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS = 24\n"
        "SNAPADMIN_BACKUP_SFTP_EVERY_HOURS = 24   # a second, offsite destination — the 3-2-1 rule"
    )
    return Step(
        "backups", "Backups enabled, with at least two destinations", present, snippet,
        "This doctor only checks that backups are turned on, not how many destinations are "
        "configured — confirm with `snapadmin_info --section features` once the project is running.",
        group="data_safety",
    )


def backup_encryption_step(ctx: ProjectContext) -> Step:
    present = "SNAPADMIN_BACKUP_AGE_RECIPIENTS" in ctx.settings_text
    snippet = 'SNAPADMIN_BACKUP_AGE_RECIPIENTS = ["age1qyqs..."]  # pip install django-snapadmin[age]'
    note = (
        "" if present else
        "Optional, but strongly recommended: an unencrypted dump on a rented offsite server is "
        "your whole database in someone else's hands. Costs one setting."
    )
    return Step(
        "backup_encryption", "Backup encryption configured (strongly recommended)", present, snippet,
        note, group="data_safety",
    )


def restore_tested_step(ctx: ProjectContext) -> Step:
    """Whether a restore has ever actually been run — always "not checked".

    Not checkable statically (it is an operational event, not a setting), and
    there is no ``snapadmin_restore`` command yet to point at either — say so
    honestly rather than citing a command that does not exist.
    """
    return Step(
        "restore_tested", "You have actually run a restore", None, "",
        "An untested backup is the most common form of not having a backup. There is no "
        "automated restore command yet — verify manually: restore a recent dump to a scratch "
        "database and confirm the data matches.",
        group="data_safety",
    )


def check_project(ctx: ProjectContext) -> list[Step]:
    steps = [installed_apps_step(ctx), urls_step(ctx), settings_step(ctx)]
    if ctx.include_api:
        steps.append(rest_step(ctx))
    if ctx.include_graphql:
        steps.append(graphql_step(ctx))
    steps.append(install_step(ctx))
    steps.append(models_step(ctx))
    steps.append(migrations_step(ctx))
    if ctx.include_api:
        steps.append(api_auth_step(ctx))
    steps.append(masking_step(ctx))
    steps.append(throttling_step(ctx))
    steps.append(pagination_step(ctx))
    steps.append(alerts_step(ctx))
    steps.append(backups_step(ctx))
    steps.append(backup_encryption_step(ctx))
    steps.append(restore_tested_step(ctx))
    return steps
