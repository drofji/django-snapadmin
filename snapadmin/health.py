"""
snapadmin/health.py

Alerting when a SnapAdmin subsystem goes unhealthy.

The counterpart of ``snapadmin_info --health-check``: it runs the same
health-probe collectors (database, Elasticsearch, REST API, GraphQL) and, when
one reports ``ok=False``, sends a single notification to the configured channels
so an operator hears about an outage instead of finding it in the logs. Meant to
run on a schedule — the ``snapadmin.send_health_alert`` Celery task (via Celery
Beat) or the ``snapadmin_health_alert`` management command (via system cron). A
cache-based cooldown means a persistent outage alerts at most once per
``SNAPADMIN_HEALTH_ALERT_COOLDOWN_MINUTES`` rather than on every run, and a
recovery clears the cooldown so the next outage alerts immediately.

Delivery goes through ``snapadmin.alerts``: email (a working ``EMAIL_BACKEND``
and ``DEFAULT_FROM_EMAIL``) plus any Slack/Discord/Teams/Telegram webhook in
``SNAPADMIN_ALERT_WEBHOOKS`` subscribed to the ``health`` event. The cooldown
above is shared by all of them, and a run where every channel failed releases it
again so the outage is re-announced instead of going quiet.

Each probe honours its feature toggle (``ELASTICSEARCH_ENABLED``,
``SNAPADMIN_REST_API_ENABLED``, ``SNAPADMIN_GRAPHQL_ENABLED``): a disabled
subsystem returns ``{"enabled": False}`` with no ``ok`` key. A probe *fails* only
when its data reports ``ok is False``, so a subsystem that was intentionally turned
off is never a false alarm — mirroring the ``--health-check`` semantics exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from snapadmin import alerts
from snapadmin.conf import get_setting
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
    emails = list(get_setting("SNAPADMIN_HEALTH_ALERT_EMAILS", [])) or list(
        get_setting("SNAPADMIN_ERROR_ALERT_EMAILS", [])
    )
    return HealthAlertConfig(
        enabled=bool(get_setting("SNAPADMIN_HEALTH_ALERT_ENABLED", True)),
        emails=emails,
        cooldown_minutes=int(get_setting("SNAPADMIN_HEALTH_ALERT_COOLDOWN_MINUTES", 60)),
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


def probe_lines(failing: list[dict]) -> tuple[str, ...]:
    """One ``title: reason`` line per failing probe, for the chat channels.

    The email template renders the full probe table; a chat message gets the
    part an operator reads first — which subsystem, and why it says it is down.
    """
    lines = []
    for probe in failing:
        data = probe.get("data") or {}
        reason = data.get("error") or data.get("detail") or "reported unhealthy"
        lines.append(f"{probe['title']}: {reason}")
    return tuple(lines)


def send_health_alert(*, force: bool = False) -> dict:
    """Probe subsystem health and alert the configured channels when one is down.

    Returns a flat summary dict: ``sent`` plus a ``reason`` when nothing went out
    (``disabled`` / ``healthy`` / ``no_recipients`` / ``cooldown`` /
    ``delivery_failed``), so the Celery task and the management command can
    report what happened. ``force`` bypasses the cooldown (for the ``--force``
    flag / testing).
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
    channels = alerts.build_channels(
        kind=alerts.ALERT_KIND_HEALTH,
        recipients=config.emails,
        from_email=config.from_email,
    )
    if not channels:
        logger.warning("health_alert_no_recipients", failing=",".join(failing_names))
        return {"sent": False, "reason": "no_recipients", "checked": checked, "failing": len(failing)}
    # Always attempt to arm the cooldown (``cache.add`` is atomic and a no-op when
    # the key already exists). ``force`` still sends when the window hasn't elapsed,
    # but arming here means a forced send also suppresses the next scheduled run
    # instead of letting it fire a second alert immediately.
    token = alerts.arm_cooldown(HEALTH_ALERT_COOLDOWN_CACHE_KEY, minutes=config.cooldown_minutes)
    if token is None and not force:
        return {"sent": False, "reason": "cooldown", "checked": checked, "failing": len(failing)}

    alert = alerts.Alert(
        kind=alerts.ALERT_KIND_HEALTH,
        subject=(
            f"[SnapAdmin] Health alert — {len(failing)} subsystem"
            f"{'' if len(failing) == 1 else 's'} down: {', '.join(failing_names)}"
        ),
        summary=f"{len(failing)} of {checked} probe(s) failing.",
        lines=probe_lines(failing),
        template="health_alert",
        context={
            "failing": failing,
            "probes": probes,
            "checked": checked,
            "generated_at": timezone.now(),
        },
    )
    result = alerts.dispatch(alert, channels)
    if not result.any_delivered:
        # Nothing was announced, so the window must not stay armed — the next
        # scheduled run has to try again rather than assume it already told someone.
        alerts.release_cooldown(HEALTH_ALERT_COOLDOWN_CACHE_KEY, token)
        logger.error(
            "health_alert_undelivered",
            failing=",".join(failing_names),
            checked=checked,
            channels=",".join(result.failed),
        )
        return {
            "sent": False,
            "reason": "delivery_failed",
            "checked": checked,
            "failing": len(failing),
            "failing_names": ",".join(failing_names),
        }
    logger.error(
        "health_alert_sent",
        failing=",".join(failing_names),
        checked=checked,
        recipients=len(config.emails),
        channels=",".join(result.delivered),
    )
    return {
        "sent": True,
        "checked": checked,
        "failing": len(failing),
        "failing_names": ",".join(failing_names),
        "recipients": len(config.emails),
        "channels": ",".join(result.delivered),
    }
