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

Rows are immutable at the ORM level (``SnapadminAuditLog.save``/``delete``
raise once persisted) and the admin is fully read-only. Recording is fail-safe:
an audit failure must never turn a working admin action into an error, so
problems are logged and swallowed.

Toggle with ``SNAPADMIN_AUDIT_LOG_ENABLED`` (default ``True``). Export for a
SIEM with ``manage.py snapadmin_audit_export``.
"""

from __future__ import annotations

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


def format_value(value) -> str | None:
    """Render a field value for the diff — stringified so it is JSON-safe."""
    if value is None:
        return None
    return str(value)


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
