"""
snapadmin/checks.py

Startup configuration checks (onboarding / drop-in DX).

SnapAdmin is settings-driven, so a typo in a setting used to fail silently or
deep inside a request. These Django system checks (run on ``manage.py check``,
``runserver``, ``migrate``, and in CI) surface misconfiguration early with an
actionable hint. Everything here is advisory: a warning never blocks boot, and
each check is a no-op when its feature is unconfigured.
"""

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Warning


def _resolve_model(dotted: str):
    """``"app.Model"`` → model class, or ``None`` if unresolvable."""
    try:
        app_label, model_name = str(dotted).split(".", 1)
    except ValueError:
        return None
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def check_analytics_db_alias(app_configs, **kwargs):
    alias = getattr(settings, "SNAPADMIN_ANALYTICS_DB_ALIAS", "") or ""
    if alias and alias not in settings.DATABASES:
        return [Warning(
            f"SNAPADMIN_ANALYTICS_DB_ALIAS = {alias!r} is not a configured DATABASES alias.",
            hint="Read-replica routing will be ignored (queries stay on 'default'). "
                 "Add the alias to DATABASES or clear the setting.",
            id="snapadmin.W001",
        )]
    return []


def check_masked_fields(app_configs, **kwargs):
    errors = []
    masked = getattr(settings, "SNAPADMIN_MASKED_FIELDS", None) or {}
    for key, fields in masked.items():
        model = _resolve_model(key)
        if model is None:
            errors.append(Error(
                f"SNAPADMIN_MASKED_FIELDS key {key!r} does not resolve to an installed model.",
                hint="Use 'app_label.ModelName', e.g. 'demo.Customer'.",
                id="snapadmin.E001",
            ))
            continue
        model_fields = {f.name for f in model._meta.get_fields()}
        for field in fields or []:
            if field not in model_fields:
                errors.append(Error(
                    f"SNAPADMIN_MASKED_FIELDS[{key!r}] lists unknown field {field!r}.",
                    hint=f"{key} has no field '{field}'. Check the spelling.",
                    id="snapadmin.E002",
                ))
    return errors


def check_nested_apps(app_configs, **kwargs):
    warnings = []
    installed = {c.label for c in apps.get_app_configs()}
    nested = getattr(settings, "SNAPADMIN_NESTED_APPS", None) or {}
    for source, target in nested.items():
        if target not in installed:
            warnings.append(Warning(
                f"SNAPADMIN_NESTED_APPS maps {source!r} → {target!r}, but no app "
                f"labelled {target!r} is installed.",
                hint="The models will stay under their own group until the target app exists.",
                id="snapadmin.W002",
            ))
    return warnings


def check_sso_providers(app_configs, **kwargs):
    warnings = []
    providers = getattr(settings, "SNAPADMIN_SSO_PROVIDERS", None) or {}
    for key, meta in providers.items():
        if not isinstance(meta, dict) or not (meta.get("url") or "").strip():
            warnings.append(Warning(
                f"SNAPADMIN_SSO_PROVIDERS[{key!r}] has no usable 'url' and will not render.",
                hint="Each provider needs a dict with a non-empty 'url', e.g. "
                     "{'label': '…', 'url': '/accounts/azure/login/'}.",
                id="snapadmin.W003",
            ))
    return warnings


ALL_CHECKS = [
    check_analytics_db_alias,
    check_masked_fields,
    check_nested_apps,
    check_sso_providers,
]


def register_checks():
    """Register every SnapAdmin check (idempotent — safe to call from ready())."""
    from django.core.checks import register
    for check in ALL_CHECKS:
        register(check)
