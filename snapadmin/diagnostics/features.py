"""
Feature-adoption collector for ``snapadmin_info`` (the ``features`` section).

A commerce-readiness checklist: for each business-important SnapAdmin capability —
backups, retention-based deletion, audit trail, PII masking (fields and rules), the REST/GraphQL APIs,
API tokens, Elasticsearch, background tasks, health/error alerting, rate limiting,
the read-only / write / delete guards, user-defined REST actions, field-level permission
guards and SSO — report whether it is actually turned on or in use in *this* project
(``✓``) or sitting unused (``✗``). Where a capability is adopted per-model or per-field,
``--verbose`` adds a one-line count.

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
from django.db.models import Model

from snapadmin.conf import get_setting
from snapadmin.diagnostics.registry import register
from snapadmin.registry import get_model_meta, is_registered


def _flag(name: str, default: bool) -> bool:
    return bool(getattr(settings, name, default))


def _count(n: int, noun: str) -> str:
    """A short ``"3 models"`` detail, or ``""`` when there is nothing to count."""
    if not n:
        return ""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _concrete_snap_models() -> list[type[Model]]:
    return [model for model in apps.get_models() if is_registered(model)]


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


def _profile() -> tuple[bool, str]:
    """``SNAPADMIN_PROFILE`` adoption (#SIMPL1g) — "on" means a project chose to
    set it at all, even to "full", not that a non-default profile is active.

    Reads the setting directly rather than through :func:`~snapadmin.conf.get_setting`:
    this *is* the profile mechanism being reported on, not a setting a profile
    resolves.
    """
    profile = getattr(settings, "SNAPADMIN_PROFILE", None)
    return profile is not None, profile or ""


def _masking_detail(masked_fields: int, ruled_fields: int) -> str:
    """``"3 fields"``, or ``"3 fields, 2 rules"`` when rules are configured."""
    detail = _count(masked_fields, "field")
    if not ruled_fields:
        return detail
    return f"{detail}, {_count(ruled_fields, 'rule')}"


def _backup_detail() -> str:
    """``"db, encrypted (2 recipients), destinations: local+sftp, restored"``.

    Reports what a run actually bundles (SNAPADMIN_BACKUP_INCLUDE), whether
    AGE encryption is configured, which destinations are active and whether
    a restore has ever completed — never the identity, only booleans, counts
    and destination names, all safe to print. The destinations/restore
    clauses are added only while backups are enabled — otherwise "local"
    (always the nominal staging destination) would print as "active" for a
    feature that is in fact off.
    """
    from snapadmin.backup import _active_destinations, get_backup_config
    from snapadmin.restore import last_restore_run

    config = get_backup_config()
    detail = "+".join(config.include)
    if config.age_recipients:
        detail += f", encrypted ({_count(len(config.age_recipients), 'recipient')})"
    if config.enabled:
        detail += f", destinations: {'+'.join(_active_destinations(config))}"
        if last_restore_run(config):
            detail += ", restored"
    return detail


def _snap_actions(models: list[type[Model]]) -> tuple[bool, str]:
    """``@snap_action`` adoption (#RFC1h) — fail-soft if the API views module can't import
    (REST disabled / DRF absent), same reasoning as :func:`_api_tokens`/:func:`_sso`."""
    try:
        from snapadmin.api.views import iter_snap_actions

        per_model = {m: iter_snap_actions(m) for m in models}
    except Exception:
        return False, ""
    total = sum(len(specs) for specs in per_model.values())
    if not total:
        return False, ""
    action_models = sum(1 for specs in per_model.values() if specs)
    return True, f"{_count(total, 'action')} on {_count(action_models, 'model')}"


def _field_permissions(models: list[type[Model]]) -> tuple[bool, str]:
    """``api_field_permissions`` adoption (#FUT3b) — fields gated, and how many models."""
    fields = 0
    field_models = 0
    for model in models:
        rules = get_model_meta(model, "api_field_permissions", {}) or {}
        if rules:
            field_models += 1
            fields += len(rules)
    if not fields:
        return False, ""
    return True, f"{_count(fields, 'field')} on {_count(field_models, 'model')}"


def _retention_detail(model_count: int, file_model_count: int, audit_on: bool, export_days) -> str:
    """Detail string for the ``retention_purge`` capability's several facets."""
    parts = []
    if model_count:
        parts.append(_count(model_count, "model"))
    if file_model_count:
        parts.append(f"{file_model_count} with data_retention_files")
    if audit_on:
        parts.append("audit log")
    if export_days:
        parts.append(f"export jobs ({export_days}d)")
    return ", ".join(parts)


def _capabilities() -> list[tuple[str, bool, str]]:
    """Every audited capability as ``(key, enabled, detail)`` in report order."""
    from snapadmin.models import SnapadminAuditLog

    models = _concrete_snap_models()

    retention = sum(1 for m in models if (get_model_meta(m, "data_retention_days", None) or 0) > 0)
    # data_retention_files (#RET2c) and the always-on audit-log purge (#RET2a)
    # and opt-in export-job purge (#RET2b) all feed the same retention_purge
    # capability below — one entry for "is anything being auto-deleted here",
    # with the detail spelling out which of the four sweeps contribute.
    retention_files = sum(1 for m in models if get_model_meta(m, "data_retention_files", None))
    audit_retention_on = SnapadminAuditLog.data_retention_days() > 0
    export_retention_days = get_setting("SNAPADMIN_EXPORT_RETENTION_DAYS", None)
    # A field can be declared sensitive by either setting; count the union, and
    # report how many of them carry an explicit rule (iterating a rules dict
    # yields its field names, so both shapes fold in the same way).
    masked = get_setting("SNAPADMIN_MASKED_FIELDS", {}) or {}
    rules = get_setting("SNAPADMIN_MASKING_RULES", {}) or {}
    by_model: dict[str, set[str]] = {}
    for source in (masked, rules):
        for key, fields in source.items():
            by_model.setdefault(str(key).lower(), set()).update(str(f) for f in (fields or []))
    masked_fields = sum(len(fields) for fields in by_model.values())
    ruled_fields = sum(len(rule or {}) for rule in rules.values())
    es_enabled = _flag("ELASTICSEARCH_ENABLED", False)
    es_models = sum(1 for m in models if get_model_meta(m, "es_index_enabled", False))
    recipients = list(get_setting("SNAPADMIN_HEALTH_ALERT_EMAILS", []) or
                      get_setting("SNAPADMIN_ERROR_ALERT_EMAILS", []))
    throttled = bool(get_setting("SNAPADMIN_THROTTLE_ANON", None) or
                     get_setting("SNAPADMIN_THROTTLE_USER", None))
    read_only = sum(1 for m in models if get_model_meta(m, "api_read_only", False))
    write_allowlist = sum(1 for m in models if get_model_meta(m, "api_write_fields", None) is not None)
    # Registered plain models (@snap_model) carry none of SnapModel's machinery:
    # ``register_admin`` is the marker for it. Worth surfacing, because these are
    # the models the ES reindex and the retention purge deliberately skip. The
    # ``inventory`` section breaks this down per model (door + exact gap list, #PAR1e).
    decorated = sum(1 for m in models if not hasattr(m, "register_admin"))
    offline_models = sum(1 for m in models if get_model_meta(m, "offline_mode", False))
    # "on" means the layer actually loads somewhere: the setting is enabled AND
    # at least one model has offline_mode=True (SnapModel.get_admin_media()'s
    # own gate, #JS2e) — the setting alone is not enough to mean anything is
    # actually running.
    connectivity_on = bool(get_setting("SNAPADMIN_CONNECTIVITY_ENABLED", False)) and offline_models > 0
    # GDPR subject-access declaration (#FUT4a/#FUT4b) — "on" means at least one
    # valid manage.py snapadmin_subject_request entry point exists; the detail
    # also names how many registered models a request from it can reach.
    subject_models = sum(1 for m in models if get_model_meta(m, "is_data_subject", False))
    subject_path_models = sum(1 for m in models if get_model_meta(m, "subject_path", None))
    # Multi-tenancy (#FUT1) — "on" means at least one registered model opted
    # into row-level isolation; the detail names how many, so an operator can
    # tell "no tenants configured" apart from "3 tenant-scoped models" at a
    # glance without reading snapadmin.checks' E009 output.
    tenant_scoped_models = sum(1 for m in models if get_model_meta(m, "tenant_scoped", False))

    return [
        ("rest_api", bool(get_setting("SNAPADMIN_REST_API_ENABLED", True)), ""),
        ("graphql", bool(get_setting("SNAPADMIN_GRAPHQL_ENABLED", True)), ""),
        ("audit_trail", bool(get_setting("SNAPADMIN_AUDIT_LOG_ENABLED", True)), ""),
        ("error_monitoring", bool(get_setting("SNAPADMIN_ERROR_MONITOR_ENABLED", True)), ""),
        ("backups", bool(get_setting("SNAPADMIN_BACKUP_ENABLED", False)), _backup_detail()),
        ("retention_purge", retention > 0 or audit_retention_on or bool(export_retention_days),
         _retention_detail(retention, retention_files, audit_retention_on, export_retention_days)),
        ("pii_masking", masked_fields > 0, _masking_detail(masked_fields, ruled_fields)),
        ("api_tokens", *_api_tokens()),
        ("elasticsearch", es_enabled, _count(es_models, "indexed model") if es_enabled else ""),
        ("background_tasks", bool(getattr(settings, "CELERY_BROKER_URL", None)), ""),
        ("health_alerts", bool(recipients), _count(len(recipients), "recipient")),
        ("rate_limiting", throttled, ""),
        ("read_only_models", read_only > 0, _count(read_only, "model")),
        ("write_allowlist", write_allowlist > 0, _count(write_allowlist, "model")),
        ("delete_guard", bool(get_setting("SNAPADMIN_API_DELETE_GUARD", None)), ""),
        ("decorated_models", decorated > 0, _count(decorated, "plain model")),
        ("show_in_form_default", bool(get_setting("SNAPADMIN_SHOW_IN_FORM_DEFAULT", False)), ""),
        ("connectivity_awareness", connectivity_on, _count(offline_models, "offline-capable model") if offline_models else ""),
        ("snap_actions", *_snap_actions(models)),
        ("field_permissions", *_field_permissions(models)),
        ("gdpr_subject_access", subject_models > 0,
         f"{_count(subject_models, 'subject model')}, {_count(subject_path_models, 'model')} reachable"
         if subject_models else ""),
        ("sso", *_sso()),
        ("profile", *_profile()),
        ("tenant_scoping", tenant_scoped_models > 0, _count(tenant_scoped_models, "tenant-scoped model")),
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
