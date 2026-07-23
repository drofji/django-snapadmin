"""
Feature-adoption collector for ``snapadmin_info`` (the ``features`` section).

A commerce-readiness checklist: for each business-important SnapAdmin capability —
backups, retention-based deletion, audit trail, PII masking, the REST/GraphQL APIs,
API tokens, Elasticsearch, background tasks, health/error alerting, rate limiting,
the read-only / write / delete guards and SSO — report whether it is actually turned
on or in use in *this* project (``✓``) or sitting unused (``✗``). Where a capability is
adopted per-model or per-field, ``--verbose`` adds a one-line count.

This complements the ``version`` collector (which lists the ``SNAPADMIN_*_ENABLED``
toggles): here the signal is *adoption* — a model actually declaring retention, a
masked field actually configured, a token actually issued — not just a default flag.

Living inventory: whenever a new business-important capability or its gating setting
ships, add a probe here (see the "Keep the feature-adoption audit current" rule).
Nothing here prints a secret — only booleans and counts.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings

from snapadmin.diagnostics.registry import register
from snapadmin.models import SnapModel


def _flag(name: str, default: bool) -> bool:
    return bool(getattr(settings, name, default))


def _count(n: int, noun: str) -> str:
    """A short ``"3 models"`` detail, or ``""`` when there is nothing to count."""
    if not n:
        return ""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _concrete_snap_models() -> list[type[SnapModel]]:
    return [model for model in apps.get_models() if SnapModel.is_concrete_subclass(model)]


def _api_tokens() -> tuple[bool, str]:
    """Active (enabled, unexpired) API token count — fail-soft if the table is absent."""
    try:
        from django.db.models import Q
        from django.utils import timezone

        from snapadmin.models import APIToken

        active = (
            APIToken.objects.filter(is_active=True)
            .filter(Q(expiration_date__isnull=True) | Q(expiration_date__gt=timezone.now()))
            .count()
        )
    except Exception:
        return False, ""
    return active > 0, _count(active, "active token")


def _sso() -> tuple[bool, str]:
    """Configured SSO providers — fail-soft if provider discovery raises."""
    try:
        from snapadmin.sso import get_sso_providers

        providers = get_sso_providers()
    except Exception:
        return False, ""
    return bool(providers), _count(len(providers), "provider")


def _capabilities() -> list[tuple[str, bool, str]]:
    """Every audited capability as ``(key, enabled, detail)`` in report order."""
    models = _concrete_snap_models()

    retention = sum(1 for m in models if (getattr(m, "data_retention_days", None) or 0) > 0)
    masked = getattr(settings, "SNAPADMIN_MASKED_FIELDS", {}) or {}
    masked_fields = sum(len(fields) for fields in masked.values())
    es_enabled = _flag("ELASTICSEARCH_ENABLED", False)
    es_models = sum(1 for m in models if getattr(m, "es_index_enabled", False))
    recipients = list(getattr(settings, "SNAPADMIN_HEALTH_ALERT_EMAILS", []) or
                      getattr(settings, "SNAPADMIN_ERROR_ALERT_EMAILS", []))
    throttled = bool(getattr(settings, "SNAPADMIN_THROTTLE_ANON", None) or
                     getattr(settings, "SNAPADMIN_THROTTLE_USER", None))
    read_only = sum(1 for m in models if getattr(m, "api_read_only", False))
    write_allowlist = sum(1 for m in models if getattr(m, "api_write_fields", None) is not None)

    return [
        ("rest_api", _flag("SNAPADMIN_REST_API_ENABLED", True), ""),
        ("graphql", _flag("SNAPADMIN_GRAPHQL_ENABLED", True), ""),
        ("audit_trail", _flag("SNAPADMIN_AUDIT_LOG_ENABLED", True), ""),
        ("error_monitoring", _flag("SNAPADMIN_ERROR_MONITOR_ENABLED", True), ""),
        ("backups", _flag("SNAPADMIN_BACKUP_ENABLED", False), ""),
        ("retention_purge", retention > 0, _count(retention, "model")),
        ("pii_masking", masked_fields > 0, _count(masked_fields, "field")),
        ("api_tokens", *_api_tokens()),
        ("elasticsearch", es_enabled, _count(es_models, "indexed model") if es_enabled else ""),
        ("background_tasks", bool(getattr(settings, "CELERY_BROKER_URL", None)), ""),
        ("health_alerts", bool(recipients), _count(len(recipients), "recipient")),
        ("rate_limiting", throttled, ""),
        ("read_only_models", read_only > 0, _count(read_only, "model")),
        ("write_allowlist", write_allowlist > 0, _count(write_allowlist, "model")),
        ("delete_guard", bool(getattr(settings, "SNAPADMIN_API_DELETE_GUARD", None)), ""),
        ("sso", *_sso()),
    ]


@register("features", title="Feature adoption", icon="🧩", order=15)
def collect(*, verbose: bool) -> dict:
    """Collect the feature-adoption checklist section."""
    caps = _capabilities()
    result: dict = {key: enabled for key, enabled, _detail in caps}
    if verbose:
        details = {key: detail for key, _enabled, detail in caps if detail}
        if details:
            result["details"] = details
    return result
