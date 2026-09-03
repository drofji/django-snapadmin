"""
tests/test_checks.py — startup configuration checks (issue #2)

Django system checks that catch common SnapAdmin misconfiguration early with an
actionable hint, and stay quiet when a feature is unconfigured or correct.
"""

import re
from datetime import timedelta
from unittest.mock import patch

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


# ── backup beat cadence vs. shortest destination interval (W010) ────────────

class TestBackupScheduleCadence:
    def test_backups_disabled_is_clean(self):
        assert checks.check_backup_schedule_cadence(None) == []

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True)
    def test_enabled_with_no_matching_beat_entry_is_clean(self):
        assert checks.check_backup_schedule_cadence(None) == []

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        CELERY_BEAT_SCHEDULE={
            "other-task": {"task": "snapadmin.purge_expired_data", "schedule": 3600},
        },
    )
    def test_beat_schedule_without_the_backup_task_is_clean(self):
        assert checks.check_backup_schedule_cadence(None) == []

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS=24,
        CELERY_BEAT_SCHEDULE={
            "run-db-backups": {
                "task": "snapadmin.run_db_backups",
                "schedule": object(),  # not a timedelta, no remaining_estimate()
            },
        },
    )
    def test_unrecognisable_schedule_type_is_clean(self):
        """Cannot determine the period → stay silent rather than risk a false
        positive on a schedule shape this check does not understand."""
        assert checks.check_backup_schedule_cadence(None) == []

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS=24,
        CELERY_BEAT_SCHEDULE={
            "run-db-backups": {
                "task": "snapadmin.run_db_backups",
                "schedule": timedelta(hours=1),
            },
        },
    )
    def test_beat_more_frequent_than_shortest_interval_is_clean(self):
        assert checks.check_backup_schedule_cadence(None) == []

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS=24,
        CELERY_BEAT_SCHEDULE={
            "run-db-backups": {
                "task": "snapadmin.run_db_backups",
                "schedule": timedelta(hours=48),
            },
        },
    )
    def test_beat_slower_than_shortest_interval_warns(self):
        result = checks.check_backup_schedule_cadence(None)
        assert [w.id for w in result] == ["snapadmin.W010"]
        assert "24" in result[0].msg

    def test_frequent_crontab_is_clean(self):
        crontab = pytest.importorskip("celery.schedules").crontab
        settings = {
            "SNAPADMIN_BACKUP_ENABLED": True,
            "SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS": 24,
            "CELERY_BEAT_SCHEDULE": {
                "run-db-backups": {
                    "task": "snapadmin.run_db_backups",
                    "schedule": crontab(minute="*/5"),
                },
            },
        }
        with override_settings(**settings):
            assert checks.check_backup_schedule_cadence(None) == []

    def test_weekly_crontab_slower_than_daily_interval_warns(self):
        crontab = pytest.importorskip("celery.schedules").crontab
        settings = {
            "SNAPADMIN_BACKUP_ENABLED": True,
            "SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS": 24,
            "CELERY_BEAT_SCHEDULE": {
                "run-db-backups": {
                    "task": "snapadmin.run_db_backups",
                    "schedule": crontab(hour=0, minute=0, day_of_week=1),
                },
            },
        }
        with override_settings(**settings):
            result = checks.check_backup_schedule_cadence(None)
        assert [w.id for w in result] == ["snapadmin.W010"]

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS=48,
        SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS=1,
        CELERY_BEAT_SCHEDULE={
            "run-db-backups": {
                "task": "snapadmin.run_db_backups",
                "schedule": timedelta(hours=24),
            },
        },
    )
    def test_crontab_that_never_fires_is_clean(self):
        """February 31st never exists — the simulation exhausts its window
        without finding two occurrences and must degrade to "cannot
        determine" rather than crash or claim a bogus period."""
        crontab = pytest.importorskip("celery.schedules").crontab
        settings = {
            "SNAPADMIN_BACKUP_ENABLED": True,
            "SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS": 24,
            "CELERY_BEAT_SCHEDULE": {
                "run-db-backups": {
                    "task": "snapadmin.run_db_backups",
                    "schedule": crontab(day_of_month=31, month_of_year=2),
                },
            },
        }
        with override_settings(**settings):
            assert checks.check_backup_schedule_cadence(None) == []

    def test_inactive_destination_interval_is_not_used_as_the_shortest(self):
        """SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS=1 would make a 24h beat read
        as "too slow" if network's interval counted — but network has no
        SNAPADMIN_BACKUP_NETWORK_DIR configured, so it is not an active
        destination. Only 'local' (48h) counts, and 24h comfortably covers
        that: this must stay clean, not warn on an interval nothing uses."""
        assert checks.check_backup_schedule_cadence(None) == []


# ── S3 destination configuration (W011) ──────────────────────────────────────

class TestBackupS3Configuration:
    def test_unset_is_clean(self):
        assert checks.check_backup_s3_configuration(None) == []

    @override_settings(
        SNAPADMIN_BACKUP_S3_BUCKET="my-bucket",
        SNAPADMIN_BACKUP_S3_ACCESS_KEY_ID="AKIDEXAMPLE",
        SNAPADMIN_BACKUP_S3_SECRET_ACCESS_KEY="secret",
    )
    def test_explicit_credentials_are_clean(self):
        assert checks.check_backup_s3_configuration(None) == []

    @override_settings(SNAPADMIN_BACKUP_S3_BUCKET="my-bucket")
    def test_no_credentials_and_no_ambient_signal_warns(self, monkeypatch):
        import snapadmin.backup as backup_module
        monkeypatch.setattr(backup_module, "s3_ambient_credentials_likely", lambda: False)
        result = checks.check_backup_s3_configuration(None)
        assert [w.id for w in result] == ["snapadmin.W011"]
        assert "ACCESS_KEY_ID" in result[0].msg

    @override_settings(SNAPADMIN_BACKUP_S3_BUCKET="my-bucket")
    def test_no_explicit_credentials_but_ambient_signal_is_clean(self, monkeypatch):
        import snapadmin.backup as backup_module
        monkeypatch.setattr(backup_module, "s3_ambient_credentials_likely", lambda: True)
        assert checks.check_backup_s3_configuration(None) == []

    @override_settings(
        SNAPADMIN_BACKUP_S3_BUCKET="my-bucket",
        SNAPADMIN_BACKUP_S3_ACCESS_KEY_ID="AKIDEXAMPLE",
        SNAPADMIN_BACKUP_S3_SECRET_ACCESS_KEY="secret",
        SNAPADMIN_BACKUP_S3_ENDPOINT_URL="not-a-url",
    )
    def test_malformed_endpoint_url_warns(self):
        result = checks.check_backup_s3_configuration(None)
        assert [w.id for w in result] == ["snapadmin.W011"]
        assert "ENDPOINT_URL" in result[0].msg

    @override_settings(
        SNAPADMIN_BACKUP_S3_BUCKET="my-bucket",
        SNAPADMIN_BACKUP_S3_ACCESS_KEY_ID="AKIDEXAMPLE",
        SNAPADMIN_BACKUP_S3_SECRET_ACCESS_KEY="secret",
        SNAPADMIN_BACKUP_S3_ENDPOINT_URL="https://s3.eu-central-1.wasabisys.com",
    )
    def test_well_formed_endpoint_url_is_clean(self):
        assert checks.check_backup_s3_configuration(None) == []

    @override_settings(SNAPADMIN_BACKUP_S3_BUCKET="my-bucket", SNAPADMIN_BACKUP_S3_ENDPOINT_URL="not-a-url")
    def test_bad_endpoint_and_missing_credentials_reports_both(self, monkeypatch):
        import snapadmin.backup as backup_module
        monkeypatch.setattr(backup_module, "s3_ambient_credentials_likely", lambda: False)
        result = checks.check_backup_s3_configuration(None)
        assert [w.id for w in result] == ["snapadmin.W011", "snapadmin.W011"]


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


# ── GDPR subject-access declaration (E011/E012) ──────────────────────────────

from django.db import models as django_models
from django.test.utils import isolate_apps

from snapadmin.models import EsStorageMode, SnapModel, snap_model


def _plain_model(name, fields=None, **snap_model_kwargs):
    """A plain, isolated django.db.models.Model registered via @snap_model,
    with real (isolated) fields for exercising subject_path resolution."""
    with isolate_apps("snapadmin"):
        attrs = {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "snapadmin"})}
        attrs.update(fields or {})
        model = type(name, (django_models.Model,), attrs)
        snap_model(**snap_model_kwargs)(model)
    return model


class TestSubjectPathsCheck:
    def test_clean_when_nothing_registered(self, monkeypatch):
        monkeypatch.setattr(checks.apps, "get_models", lambda: [])
        assert checks.check_subject_paths(None) == []

    def test_unregistered_model_is_ignored(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class Plain0(django_models.Model):
                class Meta:
                    app_label = "snapadmin"
        monkeypatch.setattr(checks.apps, "get_models", lambda: [Plain0])
        assert checks.check_subject_paths(None) == []

    def test_undeclared_subject_path_is_e011(self, monkeypatch):
        model = _plain_model("Undeclared0")
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E011"]
        assert "never declares" in result[0].msg

    def test_explicit_none_is_clean(self, monkeypatch):
        model = _plain_model("None0", subject_path=None)
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        assert checks.check_subject_paths(None) == []

    def test_zero_hop_value_match_is_clean(self, monkeypatch):
        model = _plain_model(
            "ValueMatch0",
            fields={"user_email": django_models.EmailField()},
            subject_path="user_email",
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        assert checks.check_subject_paths(None) == []

    def test_subject_model_self_reference_matching_is_clean(self, monkeypatch):
        model = _plain_model(
            "Subject0",
            fields={"email": django_models.EmailField()},
            subject_path="email", is_data_subject=True, subject_identifier="email",
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        assert checks.check_subject_paths(None) == []

    def test_is_data_subject_without_identifier_is_e012(self, monkeypatch):
        model = _plain_model(
            "NoIdentifier0",
            fields={"email": django_models.EmailField()},
            subject_path="email", is_data_subject=True,
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]
        assert "subject_identifier" in result[0].msg

    def test_subject_path_not_matching_identifier_is_e012(self, monkeypatch):
        model = _plain_model(
            "Mismatch0",
            fields={"email": django_models.EmailField(), "username": django_models.CharField(max_length=20)},
            subject_path="username", is_data_subject=True, subject_identifier="email",
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]
        assert "does not equal" in result[0].msg

    def test_non_string_path_is_e012(self, monkeypatch):
        model = _plain_model("NonString0", subject_path=123)
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]
        assert "not a non-empty string" in result[0].msg

    def test_empty_string_path_is_e012(self, monkeypatch):
        model = _plain_model("Empty0", subject_path="")
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]

    def test_over_three_hops_is_e012(self, monkeypatch):
        model = _plain_model("TooDeep0", subject_path="a__b__c__d__e")
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]
        assert "hop" in result[0].msg

    def test_three_hops_exactly_is_allowed_to_proceed_to_resolution(self, monkeypatch):
        # At the cap, not over it — falls through to the resolution check,
        # which then fails for an unrelated reason (no such field), proving
        # the hop-count gate itself did not reject it.
        model = _plain_model("AtCap0", subject_path="a__b__c__d")
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]
        assert "does not resolve" in result[0].msg

    def test_hop_segment_missing_field_is_e012(self, monkeypatch):
        model = _plain_model(
            "BadHop0", fields={"name": django_models.CharField(max_length=10)},
            subject_path="ghost__email",
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]
        assert "does not resolve" in result[0].msg

    def test_hop_through_a_non_relation_field_is_e012(self, monkeypatch):
        model = _plain_model(
            "NonRelationHop0", fields={"name": django_models.CharField(max_length=10)},
            subject_path="name__email",
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]

    def test_unresolvable_terminal_field_is_e012(self, monkeypatch):
        model = _plain_model("Unresolvable0", subject_path="does_not_exist")
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]

    def test_resolvable_forward_fk_hop_is_clean(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class FkTarget0(django_models.Model):
                email = django_models.EmailField()

                class Meta:
                    app_label = "snapadmin"

            class FkSource0(django_models.Model):
                target = django_models.ForeignKey(FkTarget0, on_delete=django_models.CASCADE)

                class Meta:
                    app_label = "snapadmin"

            snap_model(subject_path="target__email")(FkSource0)
            snap_model(subject_path=None)(FkTarget0)

        monkeypatch.setattr(checks.apps, "get_models", lambda: [FkSource0, FkTarget0])
        assert checks.check_subject_paths(None) == []

    def test_resolvable_one_to_one_hop_is_clean(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class O2OTarget0(django_models.Model):
                email = django_models.EmailField()

                class Meta:
                    app_label = "snapadmin"

            class O2OSource0(django_models.Model):
                target = django_models.OneToOneField(O2OTarget0, on_delete=django_models.CASCADE)

                class Meta:
                    app_label = "snapadmin"

            snap_model(subject_path="target__email")(O2OSource0)
            snap_model(subject_path=None)(O2OTarget0)

        monkeypatch.setattr(checks.apps, "get_models", lambda: [O2OSource0, O2OTarget0])
        assert checks.check_subject_paths(None) == []

    def test_terminal_field_missing_after_valid_hop_is_e012(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class Target1(django_models.Model):
                class Meta:
                    app_label = "snapadmin"

            class Source1(django_models.Model):
                target = django_models.ForeignKey(Target1, on_delete=django_models.CASCADE)

                class Meta:
                    app_label = "snapadmin"

            snap_model(subject_path="target__ghost")(Source1)
            snap_model(subject_path=None)(Target1)

        monkeypatch.setattr(checks.apps, "get_models", lambda: [Source1, Target1])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]

    def test_many_to_many_hop_is_rejected(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class M2MTarget0(django_models.Model):
                email = django_models.EmailField()

                class Meta:
                    app_label = "snapadmin"

            class M2MSource0(django_models.Model):
                targets = django_models.ManyToManyField(M2MTarget0)

                class Meta:
                    app_label = "snapadmin"

            snap_model(subject_path="targets__email")(M2MSource0)
            snap_model(subject_path=None)(M2MTarget0)

        monkeypatch.setattr(checks.apps, "get_models", lambda: [M2MSource0, M2MTarget0])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]

    def test_es_only_zero_hop_is_clean(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class EsZeroHop0(SnapModel):
                query = django_models.CharField(max_length=100)
                es_storage_mode = EsStorageMode.ES_ONLY
                subject_path = "query"

                class Meta:
                    app_label = "snapadmin"

        monkeypatch.setattr(checks.apps, "get_models", lambda: [EsZeroHop0])
        assert checks.check_subject_paths(None) == []

    def test_es_only_multi_hop_is_e012(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class EsTarget0(django_models.Model):
                email = django_models.EmailField()

                class Meta:
                    app_label = "snapadmin"

            class EsSource0(SnapModel):
                target = django_models.ForeignKey(EsTarget0, on_delete=django_models.CASCADE)
                es_storage_mode = EsStorageMode.ES_ONLY
                subject_path = "target__email"

                class Meta:
                    app_label = "snapadmin"

            snap_model(subject_path=None)(EsTarget0)

        monkeypatch.setattr(checks.apps, "get_models", lambda: [EsSource0, EsTarget0])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E012"]
        assert "ES_ONLY" in result[0].msg

    def test_multiple_models_report_every_error(self, monkeypatch):
        undeclared = _plain_model("Undeclared1")
        clean = _plain_model("Clean1", subject_path=None)
        monkeypatch.setattr(checks.apps, "get_models", lambda: [undeclared, clean])
        result = checks.check_subject_paths(None)
        assert [e.id for e in result] == ["snapadmin.E011"]

    def test_real_demo_registry_is_clean(self):
        """Integration confirmation: every shipped demo model already
        declares a valid subject_path (#FUT4a/#FUT4b dogfood)."""
        assert checks.check_subject_paths(None) == []


# ── retention purge scheduled (W012) ─────────────────────────────────────────

class TestRetentionPurgeScheduled:
    def _no_model_retention(self):
        """Neutralise the two demo models that carry a permanent
        data_retention_days (AuditLog, Showcase) so a test can exercise the
        "nothing configured at all" branch without disabling anything real."""
        from demo.apps.shop.models import AuditLog, Showcase
        return (
            patch.object(AuditLog, "data_retention_days", None),
            patch.object(Showcase, "data_retention_days", None),
        )

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0)
    def test_nothing_configured_is_clean(self):
        p1, p2 = self._no_model_retention()
        with p1, p2:
            assert checks.check_retention_purge_scheduled(None) == []

    def test_audit_default_alone_triggers_when_unscheduled(self):
        # SNAPADMIN_AUDIT_RETENTION_DAYS defaults to 365 (on) — this is the
        # exact "the audit log always is" case from the check's own docstring.
        p1, p2 = self._no_model_retention()
        with p1, p2, override_settings(CELERY_BEAT_SCHEDULE={}):
            result = checks.check_retention_purge_scheduled(None)
        assert [w.id for w in result] == ["snapadmin.W012"]

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0, CELERY_BEAT_SCHEDULE={})
    def test_export_retention_alone_triggers_when_unscheduled(self):
        p1, p2 = self._no_model_retention()
        with p1, p2, override_settings(SNAPADMIN_EXPORT_RETENTION_DAYS=30):
            result = checks.check_retention_purge_scheduled(None)
        assert [w.id for w in result] == ["snapadmin.W012"]

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0, CELERY_BEAT_SCHEDULE={})
    def test_model_retention_alone_triggers_when_unscheduled(self):
        from demo.apps.shop.models import Showcase
        with patch.object(Showcase, "data_retention_days", 90):
            result = checks.check_retention_purge_scheduled(None)
        assert [w.id for w in result] == ["snapadmin.W012"]

    def test_scheduled_beat_entry_is_clean(self):
        # The demo project's own CELERY_BEAT_SCHEDULE already carries this
        # entry — the default config must stay clean.
        assert checks.check_retention_purge_scheduled(None) == []

    @override_settings(CELERY_BEAT_SCHEDULE={
        "other-task": {"task": "snapadmin.purge_expired_tokens", "schedule": 3600},
    })
    def test_beat_schedule_without_the_purge_task_warns(self):
        result = checks.check_retention_purge_scheduled(None)
        assert [w.id for w in result] == ["snapadmin.W012"]

    @override_settings(CELERY_BEAT_SCHEDULE={
        "purge-expired-data": {"task": "snapadmin.purge_expired_data", "schedule": 3600},
        "not-a-dict": "oops",
    })
    def test_malformed_beat_entry_is_ignored_not_fatal(self):
        # A non-dict entry (a typo'd schedule config) must not crash the
        # check — it is simply not a match, same as any other unrelated entry.
        assert checks.check_retention_purge_scheduled(None) == []


# ── @snap_action / api_read_only conflict (E008) ─────────────────────────────

class TestSnapActionReadOnlyConflict:
    def test_no_actions_is_clean(self):
        from demo.apps.shop.models import Product
        with patch.object(Product, "api_read_only", True):
            assert checks.check_snap_action_read_only_conflict(None) == []

    def test_missing_dependency_is_a_silent_no_op(self):
        # The REST API is enabled but snapadmin.api.views can't be imported
        # (DRF/drf-spectacular absent) — urls.py already raises a pointed
        # ImproperlyConfigured for that; this check has nothing to add.
        import sys
        from unittest import mock

        from demo.apps.shop.models import Order
        with patch.object(Order, "api_read_only", True):
            with mock.patch.dict(sys.modules, {"snapadmin.api.views": None}):
                assert checks.check_snap_action_read_only_conflict(None) == []

    def test_full_crud_model_is_never_flagged(self):
        # Order.recalculate_total is methods=("post",); the demo default
        # (full CRUD, not api_read_only) must stay clean.
        assert checks.check_snap_action_read_only_conflict(None) == []

    def test_read_only_model_with_a_post_action_errors(self):
        from demo.apps.shop.models import Order
        with patch.object(Order, "api_read_only", True):
            result = checks.check_snap_action_read_only_conflict(None)
        assert [e.id for e in result] == ["snapadmin.E008"]
        assert "recalculate_total" in result[0].msg

    def test_api_http_method_names_without_post_errors(self):
        from demo.apps.shop.models import Order
        with patch.object(Order, "api_http_method_names", ["get"]):
            result = checks.check_snap_action_read_only_conflict(None)
        assert [e.id for e in result] == ["snapadmin.E008"]

    def test_api_http_method_names_including_post_is_clean(self):
        from demo.apps.shop.models import Order
        with patch.object(Order, "api_http_method_names", ["get", "post"]):
            assert checks.check_snap_action_read_only_conflict(None) == []

    def test_read_only_model_with_only_a_get_action_is_clean(self):
        # Product carries no @snap_action of its own, unlike Order
        # (recalculate_total) — isolates this case from that conflict.
        from demo.apps.shop.models import Product
        from snapadmin.api.views import snap_action

        @snap_action(methods=("get",))
        def summary(self, request):
            return {}

        with patch.object(Product, "api_read_only", True), \
             patch.object(Product, "summary", summary, create=True):
            assert checks.check_snap_action_read_only_conflict(None) == []

    @override_settings(SNAPADMIN_REST_API_ENABLED=False)
    def test_rest_api_disabled_short_circuits(self):
        from demo.apps.shop.models import Order
        with patch.object(Order, "api_read_only", True):
            assert checks.check_snap_action_read_only_conflict(None) == []


# ── extras behind SNAPADMIN_REST_API_ENABLED / _SWAGGER_ENABLED / _GRAPHQL_ENABLED (E010) ────

class TestApiExtrasInstalled:
    def _hide(self, monkeypatch, *missing: str):
        import importlib.util
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name in missing:
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    def test_clean_when_everything_installed(self):
        # The test env carries the full [api]/[graphql] stack.
        assert checks.check_api_extras_installed(None) == []

    def test_rest_enabled_but_drf_missing_is_e010(self, monkeypatch):
        self._hide(monkeypatch, "rest_framework")
        result = checks.check_api_extras_installed(None)
        assert [e.id for e in result] == ["snapadmin.E010"]
        assert "[api]" in result[0].msg
        assert "pip install django-snapadmin[api]" in result[0].hint

    def test_partial_api_extra_still_flagged(self, monkeypatch):
        # rest_framework present, django-filter (module: django_filters) missing —
        # [api] is one extra, so a partial install is still flagged.
        self._hide(monkeypatch, "django_filters")
        result = checks.check_api_extras_installed(None)
        assert [e.id for e in result] == ["snapadmin.E010"]

    @override_settings(SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_SWAGGER_ENABLED=True)
    def test_swagger_alone_still_needs_the_api_extra(self, monkeypatch):
        self._hide(monkeypatch, "drf_spectacular")
        result = checks.check_api_extras_installed(None)
        assert [e.id for e in result] == ["snapadmin.E010"]
        assert "[api]" in result[0].msg

    @override_settings(SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_SWAGGER_ENABLED=False)
    def test_rest_and_swagger_off_stays_clean_even_without_drf(self, monkeypatch):
        self._hide(monkeypatch, "rest_framework", "drf_spectacular", "django_filter")
        assert checks.check_api_extras_installed(None) == []

    def test_graphql_enabled_but_graphene_django_missing_is_e010(self, monkeypatch):
        self._hide(monkeypatch, "graphene_django")
        result = checks.check_api_extras_installed(None)
        assert [e.id for e in result] == ["snapadmin.E010"]
        assert "[graphql]" in result[0].msg
        assert "pip install django-snapadmin[graphql]" in result[0].hint

    @override_settings(SNAPADMIN_GRAPHQL_ENABLED=False)
    def test_graphql_off_stays_clean_even_without_graphene_django(self, monkeypatch):
        self._hide(monkeypatch, "graphene_django")
        assert checks.check_api_extras_installed(None) == []

    def test_both_missing_reports_both_independently(self, monkeypatch):
        self._hide(monkeypatch, "rest_framework", "graphene_django")
        result = checks.check_api_extras_installed(None)
        assert [e.id for e in result] == ["snapadmin.E010", "snapadmin.E010"]
        assert "[api]" in result[0].msg
        assert "[graphql]" in result[1].msg


# ── empty generated admin form (W015) ─────────────────────────────────────────

def _w015_message() -> str:
    result = checks.check_empty_admin_forms(None)
    return result[0].msg if result else ""


class TestEmptyAdminForms:
    def test_default_config_is_clean(self):
        # Every demo model has at least one show_in_form=True field.
        assert checks.check_empty_admin_forms(None) == []

    def test_model_with_no_show_in_form_field_warns(self, monkeypatch):
        from demo.apps.shop.models import ExchangeRate
        for name in ("code", "base", "rate"):
            monkeypatch.setattr(
                ExchangeRate._meta.get_field(name), "show_in_form", False, raising=False
            )
        result = checks.check_empty_admin_forms(None)
        assert [w.id for w in result] == ["snapadmin.W015"]
        assert "demo.ExchangeRate" in result[0].msg

    def test_silent_once_a_single_field_still_shows_in_form(self, monkeypatch):
        from demo.apps.shop.models import ExchangeRate
        monkeypatch.setattr(
            ExchangeRate._meta.get_field("base"), "show_in_form", False, raising=False
        )
        monkeypatch.setattr(
            ExchangeRate._meta.get_field("rate"), "show_in_form", False, raising=False
        )
        # "code" is untouched and still show_in_form=True.
        assert "demo.ExchangeRate" not in _w015_message()

    def test_admin_disabled_model_is_skipped(self, monkeypatch):
        from demo.apps.shop.models import ExchangeRate
        for name in ("code", "base", "rate"):
            monkeypatch.setattr(
                ExchangeRate._meta.get_field(name), "show_in_form", False, raising=False
            )
        monkeypatch.setattr(ExchangeRate, "admin_enabled", False, raising=False)
        assert "demo.ExchangeRate" not in _w015_message()


# ── integration ──────────────────────────────────────────────────────────────

class TestIntegration:
    def test_default_config_is_clean(self):
        # The demo project's default settings must not raise any SnapAdmin check error.
        call_command("check")

    def test_register_is_idempotent(self):
        checks.register_checks()
        checks.register_checks()  # must not raise
