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


# ── integration ──────────────────────────────────────────────────────────────

class TestIntegration:
    def test_default_config_is_clean(self):
        # The sandbox default settings must not raise any SnapAdmin check error.
        call_command("check")

    def test_register_is_idempotent(self):
        checks.register_checks()
        checks.register_checks()  # must not raise
