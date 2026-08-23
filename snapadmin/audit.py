"""
snapadmin/audit.py

Unalterable audit trail for administrative activity.

Django's built-in ``LogEntry`` is minimal and editable straight from the DB.
For DORA / ISO 27001 SnapAdmin records a richer, append-only
``SnapadminAuditLog`` for every create / update / delete performed through a
SnapAdmin-generated admin:

* **WHO** — the acting user (FK) plus a text snapshot that survives the user's
  deletion, their IP address and User-Agent;
* **WHAT** — the target object (content type + id + ``str`` snapshot) and a
  before/after field diff;
* **WHEN** — a timezone-aware timestamp.

The diff is stored in ``SnapadminAuditLog.changes`` as
``{field_name: {"old": <before>, "new": <after>}}``. Those two key names are the
on-disk contract — every row ever written uses them, so they are never renamed;
read them as-is when consuming the trail from a SIEM. Values keep their
JSON-native type where they have one (see :func:`format_value`), so a numeric
``42`` stays distinguishable from the string ``"42"``.

Rows are immutable at the ORM level (``SnapadminAuditLog.save``/``delete``
raise once persisted) and the admin is fully read-only. Recording is fail-safe:
an audit failure must never turn a working admin action into an error, so
problems are logged and swallowed.

Toggle with ``SNAPADMIN_AUDIT_LOG_ENABLED`` (default ``True``). Export for a
SIEM with ``manage.py snapadmin_audit_export``.
"""

from __future__ import annotations

import json
from math import isfinite

from django.conf import settings

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

# Action codes — kept in sync with SnapadminAuditLog.Action.
CREATE = "create"
UPDATE = "update"
DELETE = "delete"


def audit_enabled() -> bool:
    """Whether administrative actions are recorded to the audit trail."""
    return bool(getattr(settings, "SNAPADMIN_AUDIT_LOG_ENABLED", True))


def client_ip(request) -> str | None:
    """Best-effort client IP, honouring a single ``X-Forwarded-For`` hop."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def user_agent(request) -> str:
    """Client User-Agent string (empty when unavailable)."""
    if request is None:
        return ""
    return request.META.get("HTTP_USER_AGENT", "")


def format_value(value) -> object | None:
    """Render a field value for the diff, JSON-safe and type-preserving.

    Values that JSON represents natively are stored as themselves, so a diff
    keeps the difference between ``42`` and ``"42"`` (and between ``False``,
    ``0`` and ``"False"``) instead of flattening everything to text:

    * ``None`` → ``None``; ``bool`` / ``int`` / ``str`` → unchanged;
    * ``float`` → unchanged when finite. ``inf`` / ``nan`` have no JSON
      literal and are rejected by strict JSON columns, so they fall back to
      their string form;
    * anything else (``Decimal``, ``date``, ``UUID``, a model instance, …) →
      ``str(value)``, exactly as before.

    Rows written before this became type-preserving hold the stringified form;
    consumers must accept both.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else str(value)
    return str(value)


def display_value(value) -> str:
    """Render one diff side as display text.

    ``None`` becomes an em dash (the field had no value on that side), booleans
    render lowercase so they cannot be confused with the strings ``"True"`` /
    ``"False"``, and a nested list/dict is shown as compact JSON. Everything
    else is its ``str``. Callers render the result as text — it is escaped by
    the template, never marked safe.
    """
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def diff_rows(changes: dict | None) -> list[dict]:
    """Flatten a ``changes`` diff into rows ready for rendering.

    ``changes`` is the ``{field: {"old": …, "new": …}}`` mapping stored on
    ``SnapadminAuditLog.changes`` (already masked, if the viewer is not allowed
    raw PII). Each row carries the field name, both sides pre-rendered by
    :func:`display_value`, and ``changed`` — ``False`` when the two sides are
    equal, which lets a template mute a no-op line instead of dropping it.

    Fields are sorted by name so two entries for the same object read in the
    same order. A malformed entry (a field whose value is not an
    ``old``/``new`` mapping — e.g. a hand-written row) degrades to a single
    "new value" row rather than breaking the view.
    """
    if not changes:
        return []
    rows = []
    for field in sorted(changes, key=str):
        diff = changes[field]
        if isinstance(diff, dict) and ("old" in diff or "new" in diff):
            old, new = diff.get("old"), diff.get("new")
        else:
            old, new = None, diff
        rows.append({
            "field": str(field),
            "old": display_value(old),
            "new": display_value(new),
            "changed": old != new,
        })
    return rows


def record_audit(request, action: str, instance, changes: dict | None = None) -> None:
    """Append one audit-trail row for ``action`` on ``instance``.

    Fail-safe: any error is logged and swallowed so auditing never breaks the
    underlying admin operation. No-op when ``SNAPADMIN_AUDIT_LOG_ENABLED`` is
    off.
    """
    if not audit_enabled():
        return
    try:
        from django.contrib.contenttypes.models import ContentType
        from snapadmin.models import SnapadminAuditLog

        user = getattr(request, "user", None)
        is_auth = bool(getattr(user, "is_authenticated", False))
        actor = user if is_auth else None
        actor_repr = (str(user) if is_auth else "anonymous")[:255]

        ct = ContentType.objects.get_for_model(instance.__class__)
        SnapadminAuditLog.objects.create(
            action=action,
            actor=actor,
            actor_repr=actor_repr,
            ip_address=client_ip(request),
            user_agent=user_agent(request),
            content_type=ct,
            app_label=instance._meta.app_label,
            model=instance._meta.model_name,
            object_id=str(getattr(instance, "pk", "") or ""),
            object_repr=str(instance)[:255],
            changes=changes or None,
        )
    except Exception:  # pragma: no cover - defensive; exercised via monkeypatch
        logger.exception("snapadmin.audit.record_failed", action=action)
