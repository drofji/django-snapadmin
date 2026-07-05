"""
tests/test_sso.py — enterprise SSO login helper (issue #13)

SNAPADMIN_SSO_PROVIDERS drives (a) get_sso_providers() normalisation, (b) the
sso_providers context processor, (c) the includable button partial, and (d) the
public /api/sso-providers/ endpoint for headless frontends.
"""

import pytest
from django.template import Context, Template
from django.test import override_settings

from snapadmin.sso import get_sso_providers, sso_enabled, sso_providers

AZURE = {"label": "Login with Microsoft", "url": "/accounts/azure/login/", "icon": "az"}
KEYCLOAK = {"label": "Corporate Keycloak", "url": "https://kc.example.com/auth"}


# ── get_sso_providers() normalisation ────────────────────────────────────────

class TestGetSsoProviders:
    def test_unset_returns_empty(self):
        assert get_sso_providers() == []
        assert sso_enabled() is False

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": AZURE, "keycloak": KEYCLOAK})
    def test_normalises_and_preserves_order(self):
        result = get_sso_providers()
        assert [p["key"] for p in result] == ["azure", "keycloak"]
        assert result[0] == {
            "key": "azure", "label": "Login with Microsoft",
            "url": "/accounts/azure/login/", "icon": "az",
        }
        assert result[1]["icon"] == ""  # optional, defaults empty
        assert sso_enabled() is True

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"okta_dev": {"url": "/o/"}})
    def test_label_defaults_from_key(self):
        assert get_sso_providers()[0]["label"] == "Okta Dev"

    @override_settings(SNAPADMIN_SSO_PROVIDERS={
        "no_url": {"label": "Broken"},          # dropped: no url
        "blank_url": {"label": "Blank", "url": "  "},  # dropped: blank url
        "bad_type": "not-a-dict",               # dropped: not a mapping
        "good": {"url": "/g/"},                 # kept
    })
    def test_malformed_entries_dropped(self):
        assert [p["key"] for p in get_sso_providers()] == ["good"]

    @override_settings(SNAPADMIN_SSO_PROVIDERS=None)
    def test_none_setting_is_safe(self):
        assert get_sso_providers() == []


# ── context processor + partial template ─────────────────────────────────────

class TestTemplateRendering:
    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": AZURE})
    def test_context_processor(self):
        ctx = sso_providers(request=None)
        assert ctx["snapadmin_sso_providers"][0]["key"] == "azure"

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": AZURE})
    def test_partial_renders_buttons(self):
        html = Template('{% include "snapadmin/sso_buttons.html" %}').render(
            Context({"snapadmin_sso_providers": get_sso_providers()})
        )
        assert "/accounts/azure/login/" in html
        assert "Login with Microsoft" in html
        assert 'data-provider="azure"' in html

    def test_partial_renders_nothing_when_unconfigured(self):
        html = Template('{% include "snapadmin/sso_buttons.html" %}').render(
            Context({"snapadmin_sso_providers": []})
        )
        assert "snapadmin-sso" not in html


# ── /api/sso-providers/ endpoint ─────────────────────────────────────────────

@pytest.mark.django_db
class TestSsoProviderEndpoint:
    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": AZURE, "keycloak": KEYCLOAK})
    def test_endpoint_is_public_and_absolutises_relative_urls(self, anon_client):
        r = anon_client.get("/api/sso-providers/")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        azure, keycloak = data["providers"]
        # relative login URL is absolutised for cross-origin frontends…
        assert azure["url"].startswith("http://") and azure["url"].endswith("/accounts/azure/login/")
        # …absolute provider URLs are left untouched.
        assert keycloak["url"] == "https://kc.example.com/auth"

    def test_endpoint_empty_when_unconfigured(self, anon_client):
        r = anon_client.get("/api/sso-providers/")
        assert r.status_code == 200
        assert r.json() == {"providers": [], "count": 0}
