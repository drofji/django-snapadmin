"""
tests/test_checks.py — startup configuration checks (issue #2)

Django system checks that catch common SnapAdmin misconfiguration early with an
actionable hint, and stay quiet when a feature is unconfigured or correct.
"""

import re

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
        from demo.apps.shop.models import Product

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

#: Every model the demo app declares — the set that must stay out of W004.
DEMO_MODELS = [
    "AuditLog", "Category", "Customer", "CustomerProfile", "ExchangeRate",
    "Order", "OrderItem", "Product", "SearchLog", "Showcase", "Tag",
]


def _w004_message() -> str:
    """The grouped W004 message, or "" when the check is silent."""
    result = checks.check_api_write_fields(None)
    return result[0].msg if result else ""


class TestApiWriteFields:
    @override_settings(SNAPADMIN_REST_API_ENABLED=False)
    def test_disabled_api_returns_no_warnings(self):
        assert checks.check_api_write_fields(None) == []

    def test_demo_models_are_all_guarded(self):
        """The demo dogfoods the allowlist, so no demo model is ever named in W004.

        Asserted per-model rather than as ``result == []`` because other test
        modules define throwaway SnapModels under the ``demo`` app label, and those
        stay in the app registry for the rest of the session.
        """
        msg = _w004_message()
        for name in DEMO_MODELS:
            assert f"demo.{name}" not in msg

    def test_silent_when_every_model_is_guarded(self, monkeypatch):
        """No writable model left unguarded → no warning at all.

        Patched rather than relying on the real registry: throwaway SnapModels
        defined by other test modules linger under the ``demo`` app label.
        """
        monkeypatch.setattr(checks, "_api_writable_models", lambda: iter(()))
        assert checks.check_api_write_fields(None) == []

    def test_model_without_write_fields_warns(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.delattr(Product, "api_write_fields")
        result = checks.check_api_write_fields(None)
        assert [w.id for w in result] == ["snapadmin.W004"]
        assert "demo.Product" in result[0].msg

    def test_warns_once_for_many_unguarded_models(self, monkeypatch):
        """#CHK1: one grouped warning, not one block per model."""
        from demo.apps.shop.models import Category, Customer, Product
        for model in (Product, Customer, Category):
            monkeypatch.delattr(model, "api_write_fields")
        result = checks.check_api_write_fields(None)
        assert len(result) == 1
        assert re.match(r"^\d+ model\(s\) have no api_write_fields set", result[0].msg)
        for label in ("demo.Category", "demo.Customer", "demo.Product"):
            assert label in result[0].msg

    def test_read_only_model_needs_no_allowlist(self):
        """ExchangeRate is api_read_only — writes 405, so there is nothing to guard."""
        from demo.apps.shop.models import ExchangeRate

        assert getattr(ExchangeRate, "api_write_fields", None) is None
        assert ExchangeRate not in list(checks._api_writable_models())

    def test_read_verb_allowlist_model_needs_no_allowlist(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.delattr(Product, "api_write_fields")
        monkeypatch.setattr(Product, "api_http_method_names", ["get", "head"], raising=False)
        assert "demo.Product" not in _w004_message()

    def test_write_verb_allowlist_still_needs_a_guard(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.delattr(Product, "api_write_fields")
        monkeypatch.setattr(Product, "api_http_method_names", ["get", "PATCH"], raising=False)
        result = checks.check_api_write_fields(None)
        assert "demo.Product" in result[0].msg

    def test_model_with_write_fields_set_does_not_warn(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["name"], raising=False)
        result = checks.check_api_write_fields(None)
        assert not any("demo.Product" in w.msg for w in result)


class TestFormatLabels:
    def test_short_list_is_listed_in_full(self):
        assert checks._format_labels(["a.A", "b.B"]) == "a.A, b.B"

    def test_long_list_is_truncated_with_a_count(self):
        labels = [f"app.Model{i:03d}" for i in range(checks.MODEL_LIST_CAP + 5)]
        rendered = checks._format_labels(labels)
        assert rendered.endswith("(+5 more)")
        assert labels[checks.MODEL_LIST_CAP] not in rendered


# ── field-read-only but still write-exposed (W007) ───────────────────────────

class TestApiReadOnlyNudge:
    @override_settings(SNAPADMIN_REST_API_ENABLED=False)
    def test_disabled_api_returns_no_warnings(self):
        assert checks.check_api_read_only(None) == []

    def test_no_warning_when_no_model_is_field_read_only(self):
        # No demo model sets api_write_fields = [] by default.
        assert not any(w.id == "snapadmin.W007" for w in checks.check_api_read_only(None))

    def test_warns_for_field_read_only_but_write_exposed(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", [], raising=False)
        result = checks.check_api_read_only(None)
        assert any(w.id == "snapadmin.W007" and "demo.Product" in w.msg for w in result)

    def test_silent_when_read_only_already_set(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", [], raising=False)
        monkeypatch.setattr(Product, "api_read_only", True, raising=False)
        result = checks.check_api_read_only(None)
        assert not any("demo.Product" in w.msg for w in result)

    def test_silent_when_explicit_method_names_set(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", [], raising=False)
        monkeypatch.setattr(Product, "api_http_method_names", ["get"], raising=False)
        result = checks.check_api_read_only(None)
        assert not any("demo.Product" in w.msg for w in result)

    def test_warns_once_for_many_models(self, monkeypatch):
        """#CHK1: W007 groups too — one warning naming every affected model."""
        from demo.apps.shop.models import Customer, Product
        for model in (Product, Customer):
            monkeypatch.setattr(model, "api_write_fields", [], raising=False)
        result = checks.check_api_read_only(None)
        assert len(result) == 1
        assert result[0].msg.startswith("2 model(s)")
        assert "demo.Customer" in result[0].msg and "demo.Product" in result[0].msg


# ── optional Unfold theme ────────────────────────────────────────────────────

class TestUnfoldTheme:
    def test_no_info_when_unfold_active(self, monkeypatch):
        # The test settings install Unfold, so the check stays quiet — the themed
        # UI is active and there is nothing to surface.
        import snapadmin.admin as admin_module
        monkeypatch.setattr(admin_module, "UNFOLD_INSTALLED", True)
        assert checks.check_unfold_theme(None) == []

    def test_info_emitted_when_unfold_absent(self, monkeypatch):
        # With Unfold absent SnapAdmin falls back to the stock admin theme; the
        # check emits one informational (never error) message so it is not silent.
        import snapadmin.admin as admin_module
        monkeypatch.setattr(admin_module, "UNFOLD_INSTALLED", False)
        result = checks.check_unfold_theme(None)
        assert [i.id for i in result] == ["snapadmin.I001"]
        assert result[0].level == 20  # Info
        assert "django-snapadmin[theme]" in result[0].hint


# ── backup encryption recipients (W008) ──────────────────────────────────────

class TestBackupAgeRecipients:
    def test_unset_is_clean(self):
        assert checks.check_backup_age_recipients(None) == []

    def test_empty_list_is_clean(self):
        with override_settings(SNAPADMIN_BACKUP_AGE_RECIPIENTS=[]):
            assert checks.check_backup_age_recipients(None) == []

    @override_settings(SNAPADMIN_BACKUP_AGE_RECIPIENTS=[
        "age1scr8rpq5lxtaqqskkawrft82at865e4j3gvs30cjv79q5qq3gc7qwj8um3",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBaU comment",
    ])
    def test_plausible_recipients_are_clean(self):
        assert checks.check_backup_age_recipients(None) == []

    @override_settings(SNAPADMIN_BACKUP_AGE_RECIPIENTS=["not-a-key"])
    def test_malformed_recipient_warns(self):
        result = checks.check_backup_age_recipients(None)
        assert len(result) == 1
        assert result[0].id == "snapadmin.W008"
        assert "not-a-key" in result[0].msg

    @override_settings(SNAPADMIN_BACKUP_AGE_RECIPIENTS=[
        "age1scr8rpq5lxtaqqskkawrft82at865e4j3gvs30cjv79q5qq3gc7qwj8um3", "garbage",
    ])
    def test_one_good_one_bad_only_names_the_bad_one(self):
        result = checks.check_backup_age_recipients(None)
        assert len(result) == 1
        assert "garbage" in result[0].msg
        assert "age1scr8rpq5lxtaqqskkawrft82at865e4j3gvs30cjv79q5qq3gc7qwj8um3" not in result[0].msg

    def test_does_not_require_pyrage_or_age_binary(self):
        """The check validates shape only — it must work even with neither
        backend installed, since a project may intend to use just one."""
        import sys
        from unittest import mock

        with override_settings(SNAPADMIN_BACKUP_AGE_RECIPIENTS=["age1x"]):
            with mock.patch.dict(sys.modules, {"pyrage": None}):
                result = checks.check_backup_age_recipients(None)
        assert result == []  # "age1x" is a plausible shape, no import was needed to say so


class TestBackupEnvRequiresEncryption:
    def test_default_include_is_clean(self):
        assert checks.check_backup_env_requires_encryption(None) == []

    @override_settings(SNAPADMIN_BACKUP_INCLUDE=["db", "media"])
    def test_env_absent_is_clean_regardless_of_recipients(self):
        assert checks.check_backup_env_requires_encryption(None) == []

    @override_settings(
        SNAPADMIN_BACKUP_INCLUDE=["db", "env"],
        SNAPADMIN_BACKUP_AGE_RECIPIENTS=["age1scr8rpq5lxtaqqskkawrft82at865e4j3gvs30cjv79q5qq3gc7qwj8um3"],
    )
    def test_env_with_recipients_is_clean(self):
        assert checks.check_backup_env_requires_encryption(None) == []

    @override_settings(SNAPADMIN_BACKUP_INCLUDE=["env"])
    def test_env_with_no_recipients_is_e007(self):
        result = checks.check_backup_env_requires_encryption(None)
        assert [e.id for e in result] == ["snapadmin.E007"]
        assert "env" in result[0].msg

    @override_settings(SNAPADMIN_BACKUP_INCLUDE=["env"], SNAPADMIN_BACKUP_AGE_RECIPIENTS=[])
    def test_env_with_empty_recipients_list_is_e007(self):
        result = checks.check_backup_env_requires_encryption(None)
        assert [e.id for e in result] == ["snapadmin.E007"]


class TestMaskingRules:
    """SNAPADMIN_MASKING_RULES fails open — a bad rule masks nothing and says
    nothing — so every way of getting it wrong is an error, not a warning."""

    def test_unset_is_clean(self):
        assert checks.check_masking_rules(None) == []

    @override_settings(SNAPADMIN_MASKING_RULES={
        "demo.Customer": {"email": {"pattern": r"[^@]", "replacement": "#"}},
    })
    def test_valid_rule_is_clean(self):
        assert checks.check_masking_rules(None) == []

    @override_settings(SNAPADMIN_MASKING_RULES={"demo.Ghost": {"x": {"replacement": "y"}}})
    def test_unknown_model_is_e003(self):
        assert [e.id for e in checks.check_masking_rules(None)] == ["snapadmin.E003"]

    @override_settings(SNAPADMIN_MASKING_RULES={"nodot": {"x": {"replacement": "y"}}})
    def test_unparsable_key_is_e003(self):
        assert [e.id for e in checks.check_masking_rules(None)] == ["snapadmin.E003"]

    @override_settings(SNAPADMIN_MASKING_RULES={"demo.Customer": {"nope": {"replacement": "y"}}})
    def test_unknown_field_is_e004(self):
        assert [e.id for e in checks.check_masking_rules(None)] == ["snapadmin.E004"]

    @override_settings(SNAPADMIN_MASKING_RULES={"demo.Customer": {"email": "not-a-dict"}})
    def test_non_dict_rule_is_e005(self):
        assert [e.id for e in checks.check_masking_rules(None)] == ["snapadmin.E005"]

    @override_settings(SNAPADMIN_MASKING_RULES={
        "demo.Customer": {"email": {"pattern": r"(a+)+", "replacement": "*"}},
    })
    def test_catastrophic_pattern_is_e005(self):
        result = checks.check_masking_rules(None)
        assert [e.id for e in result] == ["snapadmin.E005"]
        assert "quantifies a group" in result[0].msg

    @override_settings(SNAPADMIN_MASKING_RULES={
        "demo.Customer": {"email": {"pattern": "([a-z]", "replacement": "*"}},
    })
    def test_uncompilable_pattern_is_e005(self):
        result = checks.check_masking_rules(None)
        assert [e.id for e in result] == ["snapadmin.E005"]
        assert "not a valid regex" in result[0].msg

    @override_settings(SNAPADMIN_MASKING_RULES={
        "demo.Customer": {"email": {"permission": "demo.view_customer"}},
    })
    def test_rule_without_a_pattern_is_clean(self):
        assert checks.check_masking_rules(None) == []


# ── SNAPADMIN_PROFILE (#SIMPL1g) ──────────────────────────────────────────────

class TestSnapadminProfile:
    def test_unset_is_clean(self):
        assert checks.check_snapadmin_profile(None) == []

    @override_settings(SNAPADMIN_PROFILE="admin")
    def test_recognised_profile_is_clean(self):
        assert checks.check_snapadmin_profile(None) == []

    @override_settings(SNAPADMIN_PROFILE="api")
    def test_api_profile_is_clean(self):
        assert checks.check_snapadmin_profile(None) == []

    @override_settings(SNAPADMIN_PROFILE="full")
    def test_full_profile_is_clean(self):
        assert checks.check_snapadmin_profile(None) == []

    @override_settings(SNAPADMIN_PROFILE="production")
    def test_unrecognised_profile_errors(self):
        result = checks.check_snapadmin_profile(None)
        assert len(result) == 1
        assert result[0].id == "snapadmin.E006"
        assert "production" in result[0].msg


class TestSnapadminProfileContradiction:
    def test_no_profile_is_clean(self):
        assert checks.check_snapadmin_profile_contradiction(None) == []

    @override_settings(SNAPADMIN_PROFILE="full")
    def test_full_profile_has_nothing_to_contradict(self):
        """`full` ships no preset entries — it *is* the built-in defaults."""
        assert checks.check_snapadmin_profile_contradiction(None) == []

    @override_settings(SNAPADMIN_PROFILE="admin")
    def test_admin_profile_with_no_explicit_override_is_clean(self, monkeypatch):
        # The demo project's own settings.py declares every SNAPADMIN_* name
        # explicitly (project convention) — including the four this profile
        # differs on, each pinned to what "full" already defaults to. Remove
        # them to simulate the project this check is actually for: one that
        # never set them and relies on the profile.
        from django.conf import settings as django_settings
        for name in checks.conf._PRESETS["admin"]:
            monkeypatch.delattr(django_settings, name, raising=False)
        assert checks.check_snapadmin_profile_contradiction(None) == []

    @override_settings(SNAPADMIN_PROFILE="admin", SNAPADMIN_REST_API_ENABLED=True)
    def test_explicit_setting_disagreeing_with_the_profile_warns(self):
        result = checks.check_snapadmin_profile_contradiction(None)
        ids = [w.id for w in result]
        assert "snapadmin.W009" in ids
        matching = next(w for w in result if "SNAPADMIN_REST_API_ENABLED" in w.msg)
        assert "admin" in matching.msg
        assert "True" in matching.msg

    @override_settings(SNAPADMIN_PROFILE="admin", SNAPADMIN_REST_API_ENABLED=False)
    def test_explicit_setting_matching_the_profile_is_clean(self):
        """Agreeing with the profile is not a contradiction — no warning."""
        result = checks.check_snapadmin_profile_contradiction(None)
        assert not any("SNAPADMIN_REST_API_ENABLED" in w.msg for w in result)

    @override_settings(SNAPADMIN_PROFILE="production")
    def test_unrecognised_profile_is_ignored_here(self):
        """check_snapadmin_profile (E006) owns reporting an invalid profile name."""
        assert checks.check_snapadmin_profile_contradiction(None) == []


# ── integration ──────────────────────────────────────────────────────────────

class TestIntegration:
    def test_default_config_is_clean(self):
        # The demo project's default settings must not raise any SnapAdmin check error.
        call_command("check")

    def test_register_is_idempotent(self):
        checks.register_checks()
        checks.register_checks()  # must not raise
