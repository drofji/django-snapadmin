"""
snapadmin/health.py

Email alerting when a SnapAdmin subsystem goes unhealthy.

The counterpart of ``snapadmin_info --health-check``: it runs the same
health-probe collectors (database, Elasticsearch, REST API, GraphQL) and, when
one reports ``ok=False``, sends a single email to the configured recipients so an
operator hears about an outage instead of finding it in the logs. Meant to run on
a schedule — the ``snapadmin.send_health_alert`` Celery task (via Celery Beat) or
the ``snapadmin_health_alert`` management command (via system cron). A
cache-based cooldown means a persistent outage emails at most once per
``SNAPADMIN_HEALTH_ALERT_COOLDOWN_MINUTES`` rather than on every run, and a
recovery clears the cooldown so the next outage alerts immediately.

Delivery uses Django's standard email machinery — a working ``EMAIL_BACKEND``
and ``DEFAULT_FROM_EMAIL`` are prerequisites. Each probe honours its feature
toggle (``ELASTICSEARCH_ENABLED``, ``SNAPADMIN_REST_API_ENABLED``,
``SNAPADMIN_GRAPHQL_ENABLED``): a disabled subsystem returns ``{"enabled": False}``
with no ``ok`` key. A probe *fails* only when its data reports ``ok is False``, so
a subsystem that was intentionally turned off is never a false alarm — mirroring
the ``--health-check`` semantics exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

HEALTH_ALERT_COOLDOWN_CACHE_KEY = "snapadmin:health-alert-cooldown"


@dataclass(frozen=True)
class HealthAlertConfig:
    """Snapshot of the SNAPADMIN_HEALTH_ALERT_* settings with their defaults."""

    enabled: bool
    emails: list[str]
    cooldown_minutes: int
    from_email: str | None


def get_health_config() -> HealthAlertConfig:
    """Read the SNAPADMIN_HEALTH_ALERT_* settings, applying documented defaults.

    Recipients default to ``SNAPADMIN_ERROR_ALERT_EMAILS`` so an operator who has
    already set up error alerting receives health alerts without configuring a
    second recipient list.
    """
    emails = list(getattr(settings, "SNAPADMIN_HEALTH_ALERT_EMAILS", [])) or list(
        getattr(settings, "SNAPADMIN_ERROR_ALERT_EMAILS", [])
    )
    return HealthAlertConfig(
        enabled=bool(getattr(settings, "SNAPADMIN_HEALTH_ALERT_ENABLED", True)),
        emails=emails,
        cooldown_minutes=int(getattr(settings, "SNAPADMIN_HEALTH_ALERT_COOLDOWN_MINUTES", 60)),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
    )


def run_health_probes(*, verbose: bool = False) -> list[dict]:
    """Run the health-probe diagnostics collectors and normalise their results.

    Returns one dict per probe with ``name``, ``title``, ``ok`` (``True`` /
    ``False`` / ``None`` when the probe reports nothing — e.g. a disabled
    subsystem) and the raw ``data``.
    """
    from snapadmin.diagnostics import collect

    return [
        {"name": collector.name, "title": collector.title, "ok": data.get("ok"), "data": data}
        for collector, data in collect(health_only=True, verbose=verbose)
    ]


def failing_probes(probes: list[dict]) -> list[dict]:
    """The probes that actively report a failure (``ok is False``)."""
    return [probe for probe in probes if probe["ok"] is False]


def send_health_alert(*, force: bool = False) -> dict:
    """Probe subsystem health and email the recipients when one is down.

    Returns a flat summary dict: ``sent`` plus a ``reason`` when nothing was
    emailed (``disabled`` / ``healthy`` / ``no_recipients`` / ``cooldown``), so
    the Celery task and the management command can report what happened. ``force``
    bypasses the cooldown (for the ``--force`` flag / testing).
    """
    config = get_health_config()
    probes = run_health_probes()
    failing = failing_probes(probes)
    checked = len(probes)
    failing_names = [probe["name"] for probe in failing]

    if not config.enabled:
        return {"sent": False, "reason": "disabled", "checked": checked, "failing": len(failing)}
    if not failing:
        # A recovery clears the cooldown so the next outage alerts immediately.
        cache.delete(HEALTH_ALERT_COOLDOWN_CACHE_KEY)
        return {"sent": False, "reason": "healthy", "checked": checked, "failing": 0}
    if not config.emails:
        logger.warning("health_alert_no_recipients", failing=",".join(failing_names))
        return {"sent": False, "reason": "no_recipients", "checked": checked, "failing": len(failing)}
    # Always attempt to arm the cooldown (``cache.add`` is atomic and a no-op when
    # the key already exists). ``force`` still sends when the window hasn't elapsed,
    # but arming here means a forced send also suppresses the next scheduled run
    # instead of letting it fire a second alert immediately.
    armed = cache.add(
        HEALTH_ALERT_COOLDOWN_CACHE_KEY,
        timezone.now().isoformat(),
        timeout=config.cooldown_minutes * 60,
    )
    if not armed and not force:
        return {"sent": False, "reason": "cooldown", "checked": checked, "failing": len(failing)}

    from snapadmin.monitoring import _send_email

    _send_email(
        subject=(
            f"[SnapAdmin] Health alert — {len(failing)} subsystem"
            f"{'' if len(failing) == 1 else 's'} down: {', '.join(failing_names)}"
        ),
        template="health_alert",
        context={
            "failing": failing,
            "probes": probes,
            "checked": checked,
            "generated_at": timezone.now(),
        },
        recipients=config.emails,
        from_email=config.from_email,
    )
    logger.error(
        "health_alert_sent",
        failing=",".join(failing_names),
        checked=checked,
        recipients=len(config.emails),
    )
    return {
        "sent": True,
        "checked": checked,
        "failing": len(failing),
        "failing_names": ",".join(failing_names),
        "recipients": len(config.emails),
    }
