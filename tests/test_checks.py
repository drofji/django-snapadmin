"""
tests/test_checks.py — startup configuration checks (issue #2)

Django system checks that catch common SnapAdmin misconfiguration early with an
actionable hint, and stay quiet when a feature is unconfigured or correct.
"""

import pytest
from django.core.management import call_command
from django.test import override_settings

from snapadmin import checks


# ── read-replica alias ───────────────────────────────────────────────────────

class TestAnalyticsAlias:
    def test_unset_ok(self):
        assert checks.check_analytics_db_alias(None) == []

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="replica")
    def test_configured_alias_ok(self):
        assert checks.check_analytics_db_alias(None) == []

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="ghost")
    def test_unknown_alias_warns(self):
        result = checks.check_analytics_db_alias(None)
        assert [w.id for w in result] == ["snapadmin.W001"]


# ── masked fields ────────────────────────────────────────────────────────────

class TestMaskedFields:
    def test_unset_ok(self):
        assert checks.check_masked_fields(None) == []

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Customer": ["email"]})
    def test_valid_ok(self):
        assert checks.check_masked_fields(None) == []

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Ghost": ["x"]})
    def test_unknown_model_errors(self):
        assert [e.id for e in checks.check_masked_fields(None)] == ["snapadmin.E001"]

    @override_settings(SNAPADMIN_MASKED_FIELDS={"nodot": ["x"]})
    def test_malformed_key_errors(self):
        assert [e.id for e in checks.check_masked_fields(None)] == ["snapadmin.E001"]

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Customer": ["nope"]})
    def test_unknown_field_errors(self):
        assert [e.id for e in checks.check_masked_fields(None)] == ["snapadmin.E002"]


# ── nested apps ──────────────────────────────────────────────────────────────

class TestNestedApps:
    def test_unset_ok(self):
        assert checks.check_nested_apps(None) == []

    @override_settings(SNAPADMIN_NESTED_APPS={"snapadmin": "auth"})
    def test_installed_target_ok(self):
        assert checks.check_nested_apps(None) == []

    @override_settings(SNAPADMIN_NESTED_APPS={"snapadmin": "ghostapp"})
    def test_missing_target_warns(self):
        assert [w.id for w in checks.check_nested_apps(None)] == ["snapadmin.W002"]


# ── nesting settings vs. active AdminSite ───────────────────────────────────

class TestNestingActiveSite:
    def test_unconfigured_ok(self):
        assert checks.check_nesting_active_site(None) == []

    @override_settings(SNAPADMIN_HIDDEN_APPS=["silk"])
    def test_configured_with_only_default_site_ok(self):
        # No other AdminSite instance carries a registry, so the default site
        # (which install_nested_apps() patches) is presumably the one in use.
        assert checks.check_nesting_active_site(None) == []

    @override_settings(SNAPADMIN_HIDDEN_APPS=["silk"])
    def test_other_site_without_models_does_not_warn(self):
        from django.contrib.admin.sites import AdminSite

        empty_site = AdminSite(name="empty_custom")
        try:
            assert checks.check_nesting_active_site(None) == []
        finally:
            del empty_site

    @override_settings(SNAPADMIN_HIDDEN_APPS=["silk"])
    def test_other_site_with_registered_models_warns(self):
        from django.contrib.admin.sites import AdminSite
        from demo.models import Product

        custom_site = AdminSite(name="custom")
        custom_site.register(Product)
        try:
            result = checks.check_nesting_active_site(None)
            assert [w.id for w in result] == ["snapadmin.W006"]
            assert "custom" in result[0].msg
        finally:
            custom_site.unregister(Product)


# ── sso providers ────────────────────────────────────────────────────────────

class TestSsoProviders:
    def test_unset_ok(self):
        assert checks.check_sso_providers(None) == []

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": {"label": "MS", "url": "/a/"}})
    def test_valid_ok(self):
        assert checks.check_sso_providers(None) == []

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": {"label": "no url"}})
    def test_missing_url_warns(self):
        assert [w.id for w in checks.check_sso_providers(None)] == ["snapadmin.W003"]

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": "not-a-dict"})
    def test_non_dict_warns(self):
        assert [w.id for w in checks.check_sso_providers(None)] == ["snapadmin.W003"]

    @override_settings(SNAPADMIN_SSO_PROVIDERS={
        "evil": {"label": "Evil", "url": "//evil.example.com/login"},
    })
    def test_protocol_relative_url_warns(self):
        assert [w.id for w in checks.check_sso_providers(None)] == ["snapadmin.W005"]

    @override_settings(
        SNAPADMIN_SSO_PROVIDERS={"okta": {"url": "https://okta.example.com/login"}},
        SNAPADMIN_SSO_ALLOWED_HOSTS=["login.microsoftonline.com"],
    )
    def test_disallowed_host_warns(self):
        assert [w.id for w in checks.check_sso_providers(None)] == ["snapadmin.W005"]

    @override_settings(
        SNAPADMIN_SSO_PROVIDERS={
            "azure": {"url": "https://login.microsoftonline.com/tenant/authorize"},
        },
        SNAPADMIN_SSO_ALLOWED_HOSTS=["login.microsoftonline.com"],
    )
    def test_allowed_host_ok(self):
        assert checks.check_sso_providers(None) == []

    @override_settings(SNAPADMIN_SSO_PROVIDERS={
        "okta": {"url": "https://okta.example.com/login"},
    })
    def test_allowed_hosts_unset_does_not_warn(self):
        assert checks.check_sso_providers(None) == []


# ── API write-fields allowlist ─────────────────────────────────────────────────

class TestApiWriteFields:
    @override_settings(SNAPADMIN_REST_API_ENABLED=False)
    def test_disabled_api_returns_no_warnings(self):
        assert checks.check_api_write_fields(None) == []

    def test_model_without_write_fields_warns(self):
        result = checks.check_api_write_fields(None)
        assert result  # every demo SnapModel leaves api_write_fields unset
        assert {w.id for w in result} == {"snapadmin.W004"}

    def test_warns_once_per_unconfigured_model(self):
        from demo.models import Product
        result = checks.check_api_write_fields(None)
        assert any("demo.Product" in w.msg for w in result)

    def test_model_with_write_fields_set_does_not_warn(self, monkeypatch):
        from demo.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["name"], raising=False)
        result = checks.check_api_write_fields(None)
        assert not any("demo.Product" in w.msg for w in result)


# ── integration ──────────────────────────────────────────────────────────────

class TestIntegration:
    def test_default_config_is_clean(self):
        # The demo project's default settings must not raise any SnapAdmin check error.
        call_command("check")

    def test_register_is_idempotent(self):
        checks.register_checks()
        checks.register_checks()  # must not raise
