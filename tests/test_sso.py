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
AZURE_EXTERNAL = {
    "label": "Login with Microsoft",
    "url": "https://login.microsoftonline.com/tenant/oauth2/authorize",
}


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

    @override_settings(SNAPADMIN_SSO_PROVIDERS={
        "evil": {"label": "Evil", "url": "//evil.example.com/login"},
        "good": {"url": "/g/"},
    })
    def test_protocol_relative_url_dropped(self):
        assert [p["key"] for p in get_sso_providers()] == ["good"]

    @override_settings(
        SNAPADMIN_SSO_PROVIDERS={"okta": {"url": "https://okta.example.com/login"}},
        SNAPADMIN_SSO_ALLOWED_HOSTS=["login.microsoftonline.com"],
    )
    def test_allowed_hosts_drops_non_matching_absolute_url(self):
        assert get_sso_providers() == []

    @override_settings(
        SNAPADMIN_SSO_PROVIDERS={"azure": AZURE_EXTERNAL},
        SNAPADMIN_SSO_ALLOWED_HOSTS=["login.microsoftonline.com"],
    )
    def test_allowed_hosts_keeps_matching_absolute_url(self):
        result = get_sso_providers()
        assert [p["key"] for p in result] == ["azure"]
        assert result[0]["url"] == AZURE_EXTERNAL["url"]

    @override_settings(
        SNAPADMIN_SSO_PROVIDERS={"azure": AZURE_EXTERNAL},
        SNAPADMIN_SSO_ALLOWED_HOSTS=["LOGIN.MICROSOFTONLINE.COM"],
    )
    def test_allowed_hosts_comparison_is_case_insensitive(self):
        assert [p["key"] for p in get_sso_providers()] == ["azure"]

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": AZURE_EXTERNAL})
    def test_allowed_hosts_unset_keeps_any_absolute_url(self):
        # Default behaviour: no host restriction unless the operator opts in.
        assert [p["key"] for p in get_sso_providers()] == ["azure"]

    @override_settings(
        SNAPADMIN_SSO_PROVIDERS={"local": {"url": "/accounts/login/"}},
        SNAPADMIN_SSO_ALLOWED_HOSTS=["login.microsoftonline.com"],
    )
    def test_allowed_hosts_does_not_affect_relative_urls(self):
        assert [p["key"] for p in get_sso_providers()] == ["local"]


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

    def test_endpoint_never_resolves_protocol_relative_url_to_external_origin(
        self, anon_client, monkeypatch
    ):
        # get_sso_providers() already filters these out, but the view has its
        # own belt-and-braces check — prove it holds even if something
        # upstream (a future refactor, a direct call from elsewhere) slips a
        # protocol-relative url past it.
        import snapadmin.sso

        monkeypatch.setattr(
            snapadmin.sso,
            "get_sso_providers",
            lambda: [{
                "key": "evil", "label": "Evil",
                "url": "//evil.example.com/login", "icon": "",
            }],
        )
        r = anon_client.get("/api/sso-providers/")
        assert r.status_code == 200
        url = r.json()["providers"][0]["url"]
        # Left untouched: not resolved into an absolute https://evil.example.com/... origin.
        assert url == "//evil.example.com/login"
        assert not url.startswith("http")
