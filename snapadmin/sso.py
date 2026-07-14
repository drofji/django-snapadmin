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
button that goes nowhere is worse than no button. Entries with an unsafe
``url`` (protocol-relative, or absolute but outside an optional
``SNAPADMIN_SSO_ALLOWED_HOSTS`` allowlist) are dropped the same way — see
``get_sso_providers`` for details.
"""

from urllib.parse import urlparse

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)


def _is_protocol_relative(url: str) -> bool:
    return url.startswith("//")


def get_sso_providers() -> list[dict]:
    """Normalise ``SNAPADMIN_SSO_PROVIDERS`` into an ordered list of buttons.

    Returns a list of ``{"key", "label", "url", "icon"}`` dicts, one per
    configured provider that has a non-empty, safe ``url``. Order follows the
    setting (dicts are insertion-ordered). Malformed entries are skipped
    rather than raising, so a typo never takes down the login page.

    Two URLs are treated as unsafe and dropped (with a warning log so a
    misconfiguration isn't silently invisible):

    - Protocol-relative URLs (``//host/path``) — these have no legitimate use
      here (a same-site path is always written with a single leading slash)
      and resolve to an external origin via ``request.build_absolute_uri``.
    - Absolute URLs whose host isn't in ``SNAPADMIN_SSO_ALLOWED_HOSTS``, when
      that setting is non-empty. This is opt-in: most deployments legitimately
      point providers at external identity providers, so by default no host
      restriction applies.
    """
    raw = getattr(settings, "SNAPADMIN_SSO_PROVIDERS", None) or {}
    allowed_hosts = {
        host.lower() for host in (getattr(settings, "SNAPADMIN_SSO_ALLOWED_HOSTS", None) or [])
    }
    providers: list[dict] = []
    for key, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        url = (meta.get("url") or "").strip()
        if not url:
            continue
        if _is_protocol_relative(url):
            logger.warning("sso_provider_unsafe_url_dropped", key=key, url=url)
            continue
        netloc = urlparse(url).netloc
        if allowed_hosts and netloc and netloc.lower() not in allowed_hosts:
            logger.warning("sso_provider_unsafe_url_dropped", key=key, url=url)
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
