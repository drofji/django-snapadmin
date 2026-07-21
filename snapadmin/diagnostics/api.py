"""
Diagnostics collector for the REST API surface.

Health probe: when ``SNAPADMIN_REST_API_ENABLED`` is on, it confirms the REST
stack is importable and the API URLs are wired (the ``api-health`` route
reverses). When the feature is off it returns a single ``{"enabled": False}``
line and is **never** counted as a health failure — so ``--health-check`` and the
health-alert email don't report a subsystem that was intentionally turned off.
"""

from __future__ import annotations

from django.conf import settings
from django.urls import reverse

from snapadmin.diagnostics.registry import register


@register("api", title="REST API", icon="🔌", order=34, health_probe=True)
def collect(*, verbose: bool) -> dict:
    """Collect the REST API section."""
    if not getattr(settings, "SNAPADMIN_REST_API_ENABLED", True):
        return {"enabled": False}

    data: dict = {"enabled": True}
    try:
        import rest_framework  # noqa: F401 — presence check for the REST stack

        data["health_url"] = reverse("api-health")
        data["ok"] = True
    except Exception as exc:
        # Enabled but not wired/importable (e.g. the URLs aren't included, or a
        # future optional [api] extra is missing) — a real, actionable failure.
        data["ok"] = False
        data["error"] = str(exc)
    return data
