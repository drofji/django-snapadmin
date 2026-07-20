"""
demo/apps/shop/managed_settings.py

**Demo-only** bridge that surfaces a curated set of runtime-editable
``SNAPADMIN_*`` settings through ``django-extra-settings`` (the ``[extra-settings]``
optional extra), so they can be edited from the admin and stored in the database
instead of only living as ``demo/core/settings.py`` constants.

Why this is a demo, not a package feature
------------------------------------------
The ``snapadmin`` package reads its configuration with
``getattr(settings, "SNAPADMIN_X", default)``. It deliberately does **not** depend
on ``django-extra-settings`` (an optional extra). To keep that contract intact while
still demonstrating DB-backed, runtime-editable configuration, the bridge lives
entirely here in the demo app and works by *syncing* the ``extra_settings`` value
back onto ``django.conf.settings`` — so the package keeps reading settings exactly
as it always does, unaware that a value originated in the database.

A real install that wants DB-backed settings can copy this pattern; an install that
doesn't use the ``[extra-settings]`` extra needs none of it and is unaffected.

Scope — only *runtime-editable* settings
-----------------------------------------
This intentionally surfaces **only** settings the package re-reads per request /
per operation, so editing one in the admin visibly changes behavior immediately
(after the sync below re-applies it). Settings that are read once at import / boot
time — URL-routing toggles (``SNAPADMIN_REST_API_ENABLED`` &c.), admin-index nesting,
dotted-path hooks — are excluded, because editing them at runtime would not take
effect and would mislead. Secrets/credentials (``SECRET_KEY``, DB/broker/SFTP
passwords, API keys) are never surfaced.
"""

from __future__ import annotations

from typing import Any

#: Curated, runtime-editable ``SNAPADMIN_*`` settings surfaced in the admin.
#: Each item is a ``django-extra-settings`` default spec plus the package default
#: (used as the seed value when the demo's own ``settings.py`` does not set one).
#: ``type`` values are ``extra_settings`` ``Setting.TYPE_*`` string constants.
MANAGED_SETTINGS_SPEC: list[dict[str, Any]] = [
    {
        "name": "SNAPADMIN_MASKED_FIELDS",
        "type": "json",
        "value": {},
        "description": (
            'PII masking map, e.g. {"demo.Customer": ["email"]}. Fields listed here '
            "are masked in the API/admin for users without the view_raw_pii permission. "
            "Edit this to see masking apply live on the Customer API."
        ),
    },
    {
        "name": "SNAPADMIN_API_PAGE_SIZE",
        "type": "int",
        "value": 25,
        "description": "Default page size for the auto-generated REST API list endpoints.",
    },
    {
        "name": "SNAPADMIN_API_MAX_PAGE_SIZE",
        "type": "int",
        "value": 500,
        "description": "Hard ceiling on a client-requested ?page_size= on the REST API.",
    },
    {
        "name": "SNAPADMIN_THROTTLE_ANON",
        "type": "string",
        "value": "60/min",
        "description": "DRF rate limit for anonymous API callers (e.g. '60/min'). Blank disables it.",
    },
    {
        "name": "SNAPADMIN_THROTTLE_USER",
        "type": "string",
        "value": "600/min",
        "description": "DRF rate limit for authenticated API callers (e.g. '600/min'). Blank disables it.",
    },
    {
        "name": "SNAPADMIN_AUDIT_LOG_ENABLED",
        "type": "bool",
        "value": True,
        "description": "Record every admin create/update/delete to the immutable audit trail.",
    },
    {
        "name": "SNAPADMIN_AUDIT_RETENTION_DAYS",
        "type": "int",
        "value": 365,
        "description": "Days to keep audit-trail rows before the retention purge removes them (0 = keep forever).",
    },
    {
        "name": "SNAPADMIN_ERROR_RETENTION_DAYS",
        "type": "int",
        "value": 30,
        "description": "Days to keep captured error events before the purge removes them (0 = keep forever).",
    },
    {
        "name": "SNAPADMIN_EXPORT_MAX_ROWS",
        "type": "int",
        "value": 0,
        "description": "Row ceiling on the synchronous streaming export before it steers to the async API (0 = unlimited).",
    },
    {
        "name": "SNAPADMIN_EXPORT_LIMIT_MAX",
        "type": "int",
        "value": 0,
        "description": "Hard cap applied to an explicit ?limit= on the streaming export (0 = no clamp).",
    },
    {
        "name": "SNAPADMIN_ES_SEARCH_LIMIT",
        "type": "int",
        "value": 1000,
        "description": "Maximum hits an Elasticsearch-routed ?search= query returns.",
    },
    {
        "name": "SNAPADMIN_DASHBOARD_PUBLIC",
        "type": "bool",
        "value": False,
        "description": (
            "Serve the /dashboard/ system page without the staff gate. Leave OFF unless you "
            "intend a public status page — it exposes host/service details."
        ),
    },
]

#: Names of every setting this bridge manages, in declaration order.
MANAGED_SETTING_NAMES: tuple[str, ...] = tuple(s["name"] for s in MANAGED_SETTINGS_SPEC)


def build_extra_settings_defaults(overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return the ``EXTRA_SETTINGS_DEFAULTS`` list for the managed settings.

    ``overrides`` maps a setting name to the demo's own configured value (e.g. an
    env-driven value already set in ``settings.py``); when present it replaces the
    spec's package-default seed so the seeded ``Setting`` row matches what the demo
    would otherwise use. Contains no Django imports so it is safe to call from
    ``settings.py`` at settings-import time.
    """
    overrides = overrides or {}
    defaults = []
    for spec in MANAGED_SETTINGS_SPEC:
        item = dict(spec)
        if spec["name"] in overrides:
            item["value"] = overrides[spec["name"]]
        defaults.append(item)
    return defaults


def sync_managed_settings_to_django() -> None:
    """Copy each managed ``Setting`` value from the DB onto ``django.conf.settings``.

    Best-effort: only settings that actually have a ``Setting`` row are applied, so a
    fresh database (no rows yet) leaves the ``settings.py`` defaults untouched, and a
    missing table / uninstalled extra (e.g. before the first ``migrate``) is a silent
    no-op rather than an error. Called once at app ``ready()`` and again whenever a
    managed ``Setting`` is saved (see :func:`connect_managed_settings_signals`).
    """
    from django.conf import settings

    try:
        from extra_settings.models import Setting
        present = dict(
            Setting.objects.filter(name__in=MANAGED_SETTING_NAMES).values_list("name", "id")
        )
    except Exception:
        # extra_settings not installed, or its table doesn't exist yet (pre-migrate).
        return

    for name in present:
        try:
            setattr(settings, name, Setting.get(name))
        except Exception:
            continue


def connect_managed_settings_signals() -> None:
    """Wire up the two demo sync triggers. Idempotent (stable ``dispatch_uid``\\ s).

    * ``post_save`` on a managed ``Setting`` → re-apply that value live, so an
      admin edit takes effect immediately in the running process.
    * a **one-shot** ``request_started`` → apply the persisted DB values onto
      ``django.conf.settings`` once, early in the first request. This is done on
      ``request_started`` rather than in ``AppConfig.ready()`` on purpose: a query
      in ``ready()`` trips Django's "database access during app initialization"
      warning, whereas the first request is a fully-ready context. It disconnects
      itself after running so it costs nothing on subsequent requests.
    """
    from django.core.signals import request_started
    from django.db.models.signals import post_save

    post_save.connect(
        _on_managed_setting_saved,
        dispatch_uid="demo_sync_managed_snapadmin_settings",
    )
    request_started.connect(
        _initial_sync_once,
        dispatch_uid="demo_initial_sync_managed_snapadmin_settings",
    )


def _initial_sync_once(sender, **kwargs) -> None:
    from django.core.signals import request_started

    request_started.disconnect(dispatch_uid="demo_initial_sync_managed_snapadmin_settings")
    sync_managed_settings_to_django()


def _on_managed_setting_saved(sender, instance, **kwargs) -> None:
    # extra_settings' Setting is the only sender whose instances carry a matching
    # `name`; guard on the managed set so unrelated post_save signals are ignored.
    name = getattr(instance, "name", None)
    if name in MANAGED_SETTING_NAMES:
        from django.conf import settings
        setattr(settings, name, instance.value)
