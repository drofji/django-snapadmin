"""
snapadmin/checks.py

Startup configuration checks (onboarding / drop-in DX).

SnapAdmin is settings-driven, so a typo in a setting used to fail silently or
deep inside a request. These Django system checks (run on ``manage.py check``,
``runserver``, ``migrate``, and in CI) surface misconfiguration early with an
actionable hint. Everything here is advisory: a warning never blocks boot, and
each check is a no-op when its feature is unconfigured.
"""

from urllib.parse import urlparse

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Info, Warning

from snapadmin.models import SnapModel


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
    allowed_hosts = {
        host.lower() for host in (getattr(settings, "SNAPADMIN_SSO_ALLOWED_HOSTS", None) or [])
    }
    for key, meta in providers.items():
        if not isinstance(meta, dict) or not (meta.get("url") or "").strip():
            warnings.append(Warning(
                f"SNAPADMIN_SSO_PROVIDERS[{key!r}] has no usable 'url' and will not render.",
                hint="Each provider needs a dict with a non-empty 'url', e.g. "
                     "{'label': '…', 'url': '/accounts/azure/login/'}.",
                id="snapadmin.W003",
            ))
            continue
        url = meta["url"].strip()
        netloc = urlparse(url).netloc
        if url.startswith("//"):
            warnings.append(Warning(
                f"SNAPADMIN_SSO_PROVIDERS[{key!r}]['url'] = {url!r} is protocol-relative.",
                hint="A protocol-relative URL (starting with '//') resolves to an external "
                     "origin and will not render. Use a site-relative path ('/accounts/…') "
                     "or a full absolute URL ('https://…').",
                id="snapadmin.W005",
            ))
        elif allowed_hosts and netloc and netloc.lower() not in allowed_hosts:
            warnings.append(Warning(
                f"SNAPADMIN_SSO_PROVIDERS[{key!r}]['url'] host {netloc!r} is not in "
                f"SNAPADMIN_SSO_ALLOWED_HOSTS and will not render.",
                hint="Add the host to SNAPADMIN_SSO_ALLOWED_HOSTS or point the provider "
                     "at an allowed identity provider.",
                id="snapadmin.W005",
            ))
    return warnings


def check_nesting_active_site(app_configs, **kwargs):
    """Warn when nesting settings are configured but another AdminSite is in play.

    ``install_nested_apps()`` (``snapadmin/apps.py``) only patches
    ``django.contrib.admin.site`` — the default ``AdminSite`` singleton. Reliably
    telling *which* ``AdminSite`` actually serves ``/admin/`` isn't possible from
    ``AppConfig.ready()`` (URLconf isn't guaranteed loaded yet, and app ready()
    order isn't guaranteed either), so this check runs later instead: by the time
    ``manage.py check`` runs, every ``AdminSite`` a project has instantiated and
    registered models on is discoverable via ``django.contrib.admin.sites.all_sites``.
    If one of those isn't the default site, nesting settings applied to the
    default site may never reach the index the user actually sees.
    """
    from snapadmin.nesting import nesting_configured

    if not nesting_configured():
        return []

    from django.contrib.admin.sites import all_sites, site as default_site

    other_sites = sorted(
        getattr(s, "name", repr(s))
        for s in all_sites
        if s is not default_site and getattr(s, "_registry", None)
    )
    if not other_sites:
        return []
    return [Warning(
        "SNAPADMIN_NESTED_APPS / SNAPADMIN_HIDDEN_APPS / SNAPADMIN_APP_LABELS are "
        "configured, but at least one AdminSite other than the default "
        f"django.contrib.admin.site also has models registered on it: {', '.join(other_sites)}.",
        hint="SnapAdmin only patches the default site's get_app_list. If that other "
             "site is the one serving /admin/, these settings are silently ignored there. "
             "Register your models on django.contrib.admin.site instead, or apply "
             "snapadmin.nesting.apply_nested_apps to your custom site's get_app_list yourself.",
        id="snapadmin.W006",
    )]


def check_api_write_fields(app_configs, **kwargs):
    if not getattr(settings, "SNAPADMIN_REST_API_ENABLED", True):
        return []
    warnings = []
    for model in apps.get_models():
        if not SnapModel.is_concrete_subclass(model):
            continue
        if getattr(model, "api_write_fields", None) is None:
            warnings.append(Warning(
                f"{model._meta.label} has no api_write_fields set — every field not "
                "listed in api_exclude_fields is writable through the auto-generated "
                "API (create/update).",
                hint="Set api_write_fields = [...] on the model to restrict which "
                     "fields accept client-supplied values (a mass-assignment guard). "
                     "Leave unset only for models where every field is safe to write.",
                id="snapadmin.W004",
            ))
    return warnings


def check_api_read_only(app_configs, **kwargs):
    """Warn: a model whose fields are all read-only but whose writes still reach the API.

    ``api_write_fields = []`` makes every field read-only, so a REST create inserts a
    blank row (all defaults) and an update is a silent no-op — a confusing surface.
    Such a model almost always wants ``api_read_only = True`` (a clean 405 on writes)
    instead. Quiet once the model sets ``api_read_only`` or an explicit
    ``api_http_method_names`` policy, so the tradeoff is a deliberate choice.
    """
    if not getattr(settings, "SNAPADMIN_REST_API_ENABLED", True):
        return []
    warnings = []
    for model in apps.get_models():
        if not SnapModel.is_concrete_subclass(model):
            continue
        if getattr(model, "api_write_fields", None) != []:
            continue
        if getattr(model, "api_read_only", False):
            continue
        if getattr(model, "api_http_method_names", None) is not None:
            continue
        warnings.append(Warning(
            f"{model._meta.label} sets api_write_fields = [] (no field is writable) but "
            "still exposes create/update/delete through the API — a REST create inserts a "
            "blank row and an update is a silent no-op, rather than a clean 405.",
            hint="Set api_read_only = True to serve this model read-only "
                 "(list/retrieve/count/export) and answer 405 to POST/PUT/PATCH/DELETE, "
                 "or set api_http_method_names to an explicit allowlist.",
            id="snapadmin.W007",
        ))
    return warnings


def check_unfold_theme(app_configs, **kwargs):
    """Info: surface the stock-admin fallback so it is never silent.

    ``django-unfold`` is an optional theme (``pip install django-snapadmin[theme]``).
    SnapAdmin resolves its admin base class lazily — it uses Unfold's themed
    ``ModelAdmin``/widgets when the package is installed *and* ``'unfold'`` is in
    ``INSTALLED_APPS``, and otherwise renders on Django's built-in admin. When that
    fallback is active this emits one informational message (never an error, never
    blocks boot) so an operator who expected the themed UI can see why it isn't there.
    """
    from snapadmin.admin import UNFOLD_INSTALLED

    if UNFOLD_INSTALLED:
        return []
    return [Info(
        "SnapAdmin is running on Django's built-in admin theme — the optional "
        "django-unfold theme is not active.",
        hint="This is fully supported. For the themed UI, install the theme extra "
             "(pip install django-snapadmin[theme]) and add 'unfold', "
             "'unfold.contrib.filters', 'unfold.contrib.forms' and "
             "'unfold.contrib.inlines' to INSTALLED_APPS before 'django.contrib.admin'.",
        id="snapadmin.I001",
    )]


ALL_CHECKS = [
    check_analytics_db_alias,
    check_masked_fields,
    check_nested_apps,
    check_nesting_active_site,
    check_sso_providers,
    check_api_write_fields,
    check_api_read_only,
    check_unfold_theme,
]


def register_checks():
    """Register every SnapAdmin check (idempotent — safe to call from ready())."""
    from django.core.checks import register
    for check in ALL_CHECKS:
        register(check)
