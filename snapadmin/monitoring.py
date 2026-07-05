"""
snapadmin/monitoring.py

Optional email error monitoring for SnapAdmin.

Two independent notification channels, both driven by ``ErrorEvent`` rows that
``SnapErrorMonitorMiddleware`` records for every unhandled exception / 5xx:

  1. **Spike alert** — when the number of errors within the last
     ``SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES`` (default 15) reaches
     ``SNAPADMIN_ERROR_ALERT_THRESHOLD`` (default 20), one email is sent to
     ``SNAPADMIN_ERROR_ALERT_EMAILS``. A cache-based cooldown guarantees at
     most one alert per ``SNAPADMIN_ERROR_ALERT_COOLDOWN_MINUTES``.

  2. **Daily digest** — ``send_error_digest()`` (Celery task
     ``api.tasks.send_error_digest`` or the ``send_error_digest`` management
     command) groups the last 24h of errors by fingerprint, caps the report at
     ``SNAPADMIN_ERROR_DIGEST_MAX_GROUPS`` groups so the email never explodes,
     and purges events older than ``SNAPADMIN_ERROR_RETENTION_DAYS``.

Delivery uses Django's standard email machinery — a working ``EMAIL_BACKEND``
(SMTP in production) and ``DEFAULT_FROM_EMAIL`` are prerequisites.
Everything is fail-safe: monitoring must never turn a broken page into a
broken site, so recording/alerting errors are logged and swallowed.
"""

from __future__ import annotations

import traceback as traceback_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snapadmin.models import ErrorEvent
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Max, Min
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

ALERT_COOLDOWN_CACHE_KEY = "snapadmin:error-alert-cooldown"


@dataclass(frozen=True)
class ErrorMonitorConfig:
    """Snapshot of all SNAPADMIN_ERROR_* settings with their defaults."""

    enabled: bool
    alert_enabled: bool
    alert_threshold: int
    alert_window_minutes: int
    alert_cooldown_minutes: int
    alert_emails: list[str]
    digest_enabled: bool
    digest_emails: list[str]
    digest_max_groups: int
    retention_days: int
    from_email: str | None


def get_config() -> ErrorMonitorConfig:
    """Read the SNAPADMIN_ERROR_* settings, applying documented defaults."""
    alert_emails = list(getattr(settings, "SNAPADMIN_ERROR_ALERT_EMAILS", []))
    digest_emails = list(getattr(settings, "SNAPADMIN_ERROR_DIGEST_EMAILS", [])) or alert_emails
    window = int(getattr(settings, "SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES", 15))
    return ErrorMonitorConfig(
        enabled=bool(getattr(settings, "SNAPADMIN_ERROR_MONITOR_ENABLED", True)),
        alert_enabled=bool(getattr(settings, "SNAPADMIN_ERROR_ALERT_ENABLED", True)),
        alert_threshold=int(getattr(settings, "SNAPADMIN_ERROR_ALERT_THRESHOLD", 20)),
        alert_window_minutes=window,
        alert_cooldown_minutes=int(
            getattr(settings, "SNAPADMIN_ERROR_ALERT_COOLDOWN_MINUTES", window)
        ),
        alert_emails=alert_emails,
        digest_enabled=bool(getattr(settings, "SNAPADMIN_ERROR_DIGEST_ENABLED", True)),
        digest_emails=digest_emails,
        digest_max_groups=int(getattr(settings, "SNAPADMIN_ERROR_DIGEST_MAX_GROUPS", 20)),
        retention_days=int(getattr(settings, "SNAPADMIN_ERROR_RETENTION_DAYS", 30)),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
    )


def record_error(
    *,
    request: HttpRequest | None = None,
    exception: BaseException | None = None,
    status_code: int = 500,
) -> "ErrorEvent | None":
    """Persist one ``ErrorEvent`` and fire the spike alert when due.

    Called by ``SnapErrorMonitorMiddleware``. Never raises — a failure here is
    logged and swallowed so monitoring cannot break request handling.
    Returns the created event, or ``None`` when disabled or on failure.
    """
    from snapadmin.models import ErrorEvent

    config = get_config()
    if not config.enabled:
        return None

    try:
        if exception is not None:
            exception_class = type(exception).__name__
            message = str(exception)
            traceback_text = "".join(
                traceback_module.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            )
        else:
            exception_class = f"HTTP{status_code}"
            message = ""
            traceback_text = ""

        event = ErrorEvent.record(
            exception_class=exception_class,
            message=message,
            path=getattr(request, "path", "") or "",
            method=getattr(request, "method", "") or "",
            status_code=status_code,
            traceback_text=traceback_text,
        )
    except Exception as exc:
        logger.warning("error_monitor_record_failed", error=str(exc))
        return None

    try:
        maybe_send_spike_alert(config=config)
    except Exception as exc:
        logger.warning("error_monitor_alert_failed", error=str(exc))

    return event


def maybe_send_spike_alert(*, config: ErrorMonitorConfig | None = None) -> bool:
    """Send the spike alert email if the window threshold has been crossed.

    Returns True only when an email was actually sent. The cache-based
    cooldown (``cache.add`` is atomic) ensures a single alert per cooldown
    window even under concurrent requests.
    """
    from snapadmin.models import ErrorEvent

    config = config or get_config()
    if not config.alert_enabled:
        return False

    window_start = timezone.now() - timedelta(minutes=config.alert_window_minutes)
    recent = ErrorEvent.objects.filter(created_at__gte=window_start)
    count = recent.count()
    if count < config.alert_threshold:
        return False

    if not config.alert_emails:
        logger.warning(
            "error_monitor_no_alert_recipients",
            count=count,
            window_minutes=config.alert_window_minutes,
        )
        return False

    if not cache.add(
        ALERT_COOLDOWN_CACHE_KEY,
        timezone.now().isoformat(),
        timeout=config.alert_cooldown_minutes * 60,
    ):
        return False

    groups, hidden_groups, hidden_events = group_events(
        recent, max_groups=config.digest_max_groups
    )
    _send_email(
        subject=(
            f"[SnapAdmin] {count} server errors in the last "
            f"{config.alert_window_minutes} minutes"
        ),
        template="error_alert",
        context={
            "count": count,
            "window_minutes": config.alert_window_minutes,
            "threshold": config.alert_threshold,
            "groups": groups,
            "hidden_groups": hidden_groups,
            "hidden_events": hidden_events,
            "generated_at": timezone.now(),
        },
        recipients=config.alert_emails,
        from_email=config.from_email,
    )
    logger.error(
        "error_monitor_spike_alert_sent",
        count=count,
        window_minutes=config.alert_window_minutes,
        recipients=len(config.alert_emails),
    )
    return True


def group_events(queryset, *, max_groups: int) -> tuple[list[dict], int, int]:
    """Group events by fingerprint, most frequent first, capped at max_groups.

    Returns ``(groups, hidden_groups, hidden_events)`` where the hidden counts
    describe what the cap cut off — they are surfaced in the email footer so
    the reader knows the digest is not exhaustive.
    """
    aggregated = list(
        queryset.values("fingerprint")
        .annotate(count=Count("id"), first_seen=Min("created_at"), last_seen=Max("created_at"))
        .order_by("-count", "-last_seen")
    )
    groups: list[dict] = []
    for row in aggregated[:max_groups]:
        sample = queryset.filter(fingerprint=row["fingerprint"]).latest("created_at")
        groups.append(
            {
                "exception_class": sample.exception_class,
                "message": sample.message,
                "path": sample.path,
                "method": sample.method,
                "status_code": sample.status_code,
                "count": row["count"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
        )
    hidden = aggregated[max_groups:]
    return groups, len(hidden), sum(row["count"] for row in hidden)


def send_error_digest(*, hours: int = 24) -> dict:
    """Send the grouped error digest for the last ``hours`` and purge old rows.

    Returns a summary dict (``sent``, ``errors``, ``groups``, ``purged`` …) so
    both the Celery task and the management command can report what happened.
    """
    from snapadmin.models import ErrorEvent

    config = get_config()
    since = timezone.now() - timedelta(hours=hours)
    events = ErrorEvent.objects.filter(created_at__gte=since)
    total = events.count()
    purged = purge_expired_events(config=config)

    if not config.digest_enabled:
        return {"sent": False, "reason": "disabled", "errors": total, "purged": purged}
    if total == 0:
        logger.info("error_digest_skipped_empty", hours=hours)
        return {"sent": False, "reason": "no_errors", "errors": 0, "purged": purged}
    if not config.digest_emails:
        logger.warning("error_digest_no_recipients", errors=total)
        return {"sent": False, "reason": "no_recipients", "errors": total, "purged": purged}

    groups, hidden_groups, hidden_events = group_events(
        events, max_groups=config.digest_max_groups
    )
    _send_email(
        subject=(
            f"[SnapAdmin] Error digest — {total} errors in {len(groups)} groups "
            f"(last {hours}h)"
        ),
        template="error_digest",
        context={
            "hours": hours,
            "total": total,
            "groups": groups,
            "hidden_groups": hidden_groups,
            "hidden_events": hidden_events,
            "generated_at": timezone.now(),
        },
        recipients=config.digest_emails,
        from_email=config.from_email,
    )
    logger.info(
        "error_digest_sent",
        errors=total,
        groups=len(groups),
        hidden_groups=hidden_groups,
        recipients=len(config.digest_emails),
        purged=purged,
    )
    return {
        "sent": True,
        "errors": total,
        "groups": len(groups),
        "hidden_groups": hidden_groups,
        "purged": purged,
    }


def purge_expired_events(*, config: ErrorMonitorConfig | None = None) -> int:
    """Delete events older than ``SNAPADMIN_ERROR_RETENTION_DAYS``."""
    from snapadmin.models import ErrorEvent

    config = config or get_config()
    cutoff = timezone.now() - timedelta(days=config.retention_days)
    deleted, _ = ErrorEvent.objects.filter(created_at__lt=cutoff).delete()
    return deleted


def _send_email(
    *,
    subject: str,
    template: str,
    context: dict,
    recipients: list[str],
    from_email: str | None,
) -> None:
    text_body = render_to_string(f"snapadmin/email/{template}.txt", context)
    html_body = render_to_string(f"snapadmin/email/{template}.html", context)
    email = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=from_email, to=recipients
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)
