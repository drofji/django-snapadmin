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
     ``snapadmin.send_error_digest`` or the ``send_error_digest`` management
     command) groups the last 24h of errors by fingerprint, caps the report at
     ``SNAPADMIN_ERROR_DIGEST_MAX_GROUPS`` groups so the email never explodes,
     and purges events older than ``SNAPADMIN_ERROR_RETENTION_DAYS``.

Delivery goes through ``snapadmin.alerts``: email (a working ``EMAIL_BACKEND``
and ``DEFAULT_FROM_EMAIL``) plus any Slack/Discord/Teams/Telegram webhook listed
in ``SNAPADMIN_ALERT_WEBHOOKS``. The thresholds, grouping and cooldown above are
shared by every channel — a webhook is a transport, not a second alert — so
adding one never makes an alert fire more often.
Everything is fail-safe: monitoring must never turn a broken page into a
broken site, so recording/alerting errors are logged and swallowed, and a
delivery that failed everywhere releases the cooldown instead of silently
consuming it.
"""

from __future__ import annotations

import traceback as traceback_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snapadmin.models import ErrorEvent
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Min
from django.http import HttpRequest
from django.utils import timezone

from snapadmin import alerts
from snapadmin.conf import get_setting
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

ALERT_COOLDOWN_CACHE_KEY = "snapadmin:error-alert-cooldown"

#: How many grouped errors a chat message lists before it says "and N more".
#: The email keeps the full grouped table — only the webhook text is capped.
ALERT_MAX_CHAT_LINES = 10


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
    alert_emails = list(get_setting("SNAPADMIN_ERROR_ALERT_EMAILS", []))
    digest_emails = list(get_setting("SNAPADMIN_ERROR_DIGEST_EMAILS", [])) or alert_emails
    window = int(get_setting("SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES", 15))
    return ErrorMonitorConfig(
        enabled=bool(get_setting("SNAPADMIN_ERROR_MONITOR_ENABLED", True)),
        alert_enabled=bool(get_setting("SNAPADMIN_ERROR_ALERT_ENABLED", True)),
        alert_threshold=int(get_setting("SNAPADMIN_ERROR_ALERT_THRESHOLD", 20)),
        alert_window_minutes=window,
        alert_cooldown_minutes=int(
            get_setting("SNAPADMIN_ERROR_ALERT_COOLDOWN_MINUTES", window)
        ),
        alert_emails=alert_emails,
        digest_enabled=bool(get_setting("SNAPADMIN_ERROR_DIGEST_ENABLED", True)),
        digest_emails=digest_emails,
        digest_max_groups=int(get_setting("SNAPADMIN_ERROR_DIGEST_MAX_GROUPS", 20)),
        retention_days=int(get_setting("SNAPADMIN_ERROR_RETENTION_DAYS", 30)),
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
    """Send the spike alert if the window threshold has been crossed.

    Returns True only when at least one channel accepted the alert. The
    cache-based cooldown (``cache.add`` is atomic) ensures a single alert per
    cooldown window even under concurrent requests — and it is claimed *before*
    the send, then released again if every channel failed, so an unreachable
    webhook cannot mute the next spike for the rest of the window.
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

    channels = alerts.build_channels(
        kind=alerts.ALERT_KIND_ERROR_SPIKE,
        recipients=config.alert_emails,
        from_email=config.from_email,
    )
    if not channels:
        logger.warning(
            "error_monitor_no_alert_recipients",
            count=count,
            window_minutes=config.alert_window_minutes,
        )
        return False

    token = alerts.arm_cooldown(
        ALERT_COOLDOWN_CACHE_KEY, minutes=config.alert_cooldown_minutes
    )
    if token is None:
        return False

    groups, hidden_groups, hidden_events = group_events(
        recent, max_groups=config.digest_max_groups
    )
    alert = alerts.Alert(
        kind=alerts.ALERT_KIND_ERROR_SPIKE,
        subject=(
            f"[SnapAdmin] {count} server errors in the last "
            f"{config.alert_window_minutes} minutes"
        ),
        summary=(
            f"{count} errors in {config.alert_window_minutes} min "
            f"(threshold {config.alert_threshold})."
        ),
        lines=group_lines(groups, hidden_groups=hidden_groups, hidden_events=hidden_events),
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
    )
    result = alerts.dispatch(alert, channels)
    if not result.any_delivered:
        alerts.release_cooldown(ALERT_COOLDOWN_CACHE_KEY, token)
        logger.error(
            "error_monitor_spike_alert_undelivered",
            count=count,
            window_minutes=config.alert_window_minutes,
            channels=",".join(result.failed),
        )
        return False
    logger.error(
        "error_monitor_spike_alert_sent",
        count=count,
        window_minutes=config.alert_window_minutes,
        recipients=len(config.alert_emails),
        channels=",".join(result.delivered),
    )
    return True


def group_lines(
    groups: list[dict],
    *,
    hidden_groups: int = 0,
    hidden_events: int = 0,
    max_lines: int = ALERT_MAX_CHAT_LINES,
) -> tuple[str, ...]:
    """One short line per error group, for the chat channels.

    The email templates render the full grouped table; a chat message has to
    stay readable, so this caps the list and states what it left out instead of
    letting the provider truncate mid-sentence.
    """
    lines = [
        f"{group['count']}× {group['exception_class']} — "
        f"{group['method'] or 'GET'} {group['path'] or '—'}".rstrip()
        for group in groups[:max_lines]
    ]
    trimmed = groups[max_lines:]
    remaining_groups = len(trimmed) + hidden_groups
    if remaining_groups:
        remaining_events = sum(group["count"] for group in trimmed) + hidden_events
        lines.append(
            f"…and {remaining_groups} more group(s) covering {remaining_events} error(s)"
        )
    return tuple(lines)


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
    # Snapshot total and the grouped breakdown from the *same* pre-purge state:
    # events is a lazy queryset, so if purge_expired_events() ran first and
    # deleted rows counted in `total`, group_events()'s own re-evaluation of
    # `events` could then aggregate a different, smaller set of rows — leaving
    # the digest subject's total and the body's grouped counts disagreeing.
    groups, hidden_groups, hidden_events = group_events(
        events, max_groups=config.digest_max_groups
    )
    purged = purge_expired_events(config=config)

    if not config.digest_enabled:
        return {"sent": False, "reason": "disabled", "errors": total, "purged": purged}
    if total == 0:
        logger.info("error_digest_skipped_empty", hours=hours)
        return {"sent": False, "reason": "no_errors", "errors": 0, "purged": purged}
    channels = alerts.build_channels(
        kind=alerts.ALERT_KIND_ERROR_DIGEST,
        recipients=config.digest_emails,
        from_email=config.from_email,
    )
    if not channels:
        logger.warning("error_digest_no_recipients", errors=total)
        return {"sent": False, "reason": "no_recipients", "errors": total, "purged": purged}

    alert = alerts.Alert(
        kind=alerts.ALERT_KIND_ERROR_DIGEST,
        subject=(
            f"[SnapAdmin] Error digest — {total} errors in {len(groups)} groups "
            f"(last {hours}h)"
        ),
        summary=f"{total} errors in {len(groups)} group(s) over the last {hours}h.",
        lines=group_lines(groups, hidden_groups=hidden_groups, hidden_events=hidden_events),
        severity="warning",
        template="error_digest",
        context={
            "hours": hours,
            "total": total,
            "groups": groups,
            "hidden_groups": hidden_groups,
            "hidden_events": hidden_events,
            "generated_at": timezone.now(),
        },
    )
    result = alerts.dispatch(alert, channels)
    if not result.any_delivered:
        logger.warning(
            "error_digest_undelivered",
            errors=total,
            channels=",".join(result.failed),
            purged=purged,
        )
        return {
            "sent": False,
            "reason": "delivery_failed",
            "errors": total,
            "groups": len(groups),
            "hidden_groups": hidden_groups,
            "purged": purged,
        }
    logger.info(
        "error_digest_sent",
        errors=total,
        groups=len(groups),
        hidden_groups=hidden_groups,
        recipients=len(config.digest_emails),
        channels=",".join(result.delivered),
        purged=purged,
    )
    return {
        "sent": True,
        "errors": total,
        "groups": len(groups),
        "hidden_groups": hidden_groups,
        "channels": ",".join(result.delivered),
        "purged": purged,
    }


def purge_expired_events(*, config: ErrorMonitorConfig | None = None) -> int:
    """Delete events older than ``SNAPADMIN_ERROR_RETENTION_DAYS``.

    A non-positive ``retention_days`` (e.g. ``0``, meant by an operator as
    "keep forever") is treated as "retention not configured" and purges
    nothing — otherwise the cutoff would collapse to roughly "now" and wipe
    the entire table on the next digest run.
    """
    from snapadmin.models import ErrorEvent

    config = config or get_config()
    if not config.retention_days or config.retention_days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=config.retention_days)
    count = ErrorEvent.objects.filter(created_at__lt=cutoff).count()
    ErrorEvent.objects.filter(created_at__lt=cutoff).delete()
    return count


def _send_email(
    *,
    subject: str,
    template: str,
    context: dict,
    recipients: list[str],
    from_email: str | None,
) -> None:
    """Render and send one alert email.

    Kept as a thin delegation to ``alerts.send_email_alert`` — the rendering
    moved to ``snapadmin.alerts`` when email became one channel among several,
    and this name predates that, so anything already importing it keeps working.
    """
    alerts.send_email_alert(
        subject=subject,
        template=template,
        context=context,
        recipients=recipients,
        from_email=from_email,
    )
