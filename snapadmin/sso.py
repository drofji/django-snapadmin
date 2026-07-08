"""
snapadmin/sso.py

Modular enterprise SSO / OAuth2 login helper.

SnapAdmin does **not** implement SSO itself — that stays with the auth backend
you already run (``django-allauth``, ``social-auth-app-django``, mozilla-django-
oidc, …). This module is a thin, dependency-free presentation wrapper: it turns
a single setting into (a) login-page buttons and (b) a headless config endpoint
so an external frontend can render the same corporate login options.

Enable by declaring the providers you already wired into
``AUTHENTICATION_BACKENDS`` / URLconf::

    SNAPADMIN_SSO_PROVIDERS = {
        "azure":    {"label": "Login with Microsoft Enterprise", "url": "/accounts/azure/login/"},
        "keycloak": {"label": "Corporate Keycloak SSO", "url": "/api/v1/auth/keycloak/"},
    }

Each entry may carry an optional ``"icon"`` (a CSS class or Material Symbols
name the template renders). Entries without a usable ``url`` are dropped — a
button that goes nowhere is worse than no button.
"""

from django.conf import settings


def get_sso_providers() -> list[dict]:
    """Normalise ``SNAPADMIN_SSO_PROVIDERS`` into an ordered list of buttons.

    Returns a list of ``{"key", "label", "url", "icon"}`` dicts, one per
    configured provider that has a non-empty ``url``. Order follows the setting
    (dicts are insertion-ordered). Malformed entries are skipped rather than
    raising, so a typo never takes down the login page.
    """
    raw = getattr(settings, "SNAPADMIN_SSO_PROVIDERS", None) or {}
    providers: list[dict] = []
    for key, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        url = (meta.get("url") or "").strip()
        if not url:
            continue
        providers.append({
            "key": key,
            "label": meta.get("label") or str(key).replace("_", " ").title(),
            "url": url,
            "icon": meta.get("icon", ""),
        })
    return providers


def sso_enabled() -> bool:
    """True when at least one usable SSO provider is configured."""
    return bool(get_sso_providers())


def sso_providers(request):
    """Template context processor: exposes ``snapadmin_sso_providers``.

    Add ``"snapadmin.sso.sso_providers"`` to your ``TEMPLATES`` context
    processors, then ``{% include "snapadmin/sso_buttons.html" %}`` inside your
    admin login override to render the provider buttons.
    """
    return {"snapadmin_sso_providers": get_sso_providers()}
