"""Tests for the ``snapadmin_info`` feature-adoption collector (#CLI5)."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from snapadmin.diagnostics import features as features_collector
from snapadmin.diagnostics import get_collector
from snapadmin.encryption import keys as encryption_keys


def raising_key_provider():
    """A KEY_PROVIDER that fails with something other than ImproperlyConfigured."""
    raise RuntimeError("vault unreachable")


def _collect(*, verbose=False):
    return get_collector("features").collect(verbose=verbose)


class TestRegistration:
    def test_registered_with_expected_metadata(self):
        collector = get_collector("features")
        assert collector is not None
        assert collector.title == "Feature adoption"
        assert collector.order == 15
        assert collector.health_probe is False

    def test_every_capability_is_a_bool(self):
        data = _collect()
        assert data  # non-empty checklist
        assert all(isinstance(v, bool) for k, v in data.items() if k != "details")

    def test_count_helper_singular_and_plural(self):
        assert features_collector._count(0, "model") == ""
        assert features_collector._count(1, "model") == "1 model"
        assert features_collector._count(3, "field") == "3 fields"

    def test_extra_missing_detail_blank_when_everything_importable(self):
        assert features_collector._extra_missing_detail("os", "sys", extra="api") == ""

    def test_extra_missing_detail_names_the_extra_when_absent(self):
        assert features_collector._extra_missing_detail(
            "no_such_module_xyz", extra="api"
        ) == "[api] extra not installed"


class TestSettingsGatedCapabilities:
    @override_settings(SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_GRAPHQL_ENABLED=False)
    def test_api_surfaces_reflect_flags(self):
        data = _collect()
        assert data["rest_api"] is False
        assert data["graphql"] is False

    def test_api_surfaces_have_no_detail_when_extras_installed(self):
        # The test env carries the [api]/[graphql] extras — an empty detail is
        # dropped entirely rather than kept as a blank string (see collect()).
        data = _collect(verbose=True)
        assert "rest_api" not in data.get("details", {})
        assert "graphql" not in data.get("details", {})

    def test_api_surface_detail_names_the_missing_extra(self, monkeypatch):
        monkeypatch.setattr(
            features_collector, "_extra_missing_detail", lambda *a, **k: "[api] extra not installed"
        )
        data = _collect(verbose=True)
        assert data["details"]["rest_api"] == "[api] extra not installed"

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True)
    def test_backups_on_when_enabled(self):
        assert _collect()["backups"] is True

    def test_backups_off_by_default(self):
        assert _collect()["backups"] is False

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True)
    def test_backup_detail_defaults_to_db_unencrypted(self):
        data = _collect(verbose=True)
        assert data["details"]["backups"] == "db, destinations: local"

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        SNAPADMIN_BACKUP_INCLUDE=["db", "media", "env"],
    )
    def test_backup_detail_reports_every_included_part(self):
        data = _collect(verbose=True)
        assert data["details"]["backups"] == "db+media+env, destinations: local"

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=True,
        SNAPADMIN_BACKUP_AGE_RECIPIENTS=["age1x", "age1y"],
    )
    def test_backup_detail_reports_encryption_and_recipient_count(self):
        data = _collect(verbose=True)
        assert data["details"]["backups"] == "db, encrypted (2 recipients), destinations: local"

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_ALIGN_TO_SCHEDULE=True)
    def test_backup_detail_reports_schedule_alignment(self):
        data = _collect(verbose=True)
        assert data["details"]["backups"] == "db, destinations: local, schedule-aligned"

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_ALIGN_TO_SCHEDULE=False)
    def test_backup_detail_omits_alignment_when_off(self):
        """The default must stay silent — an adoption audit reads a present clause
        as a deliberate choice, so printing one for the default would be a lie."""
        assert "schedule-aligned" not in _collect(verbose=True)["details"]["backups"]

    def test_backup_detail_never_reports_identity(self):
        """No setting or code path here ever touches the identity file's
        contents — only public recipients and a count reach the report."""
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True,
            SNAPADMIN_BACKUP_AGE_RECIPIENTS=["age1x"],
            SNAPADMIN_BACKUP_AGE_IDENTITY_FILE="/secret/identity.txt",
        ):
            data = _collect(verbose=True)
        assert "identity" not in data["details"]["backups"]
        assert "/secret/identity.txt" not in json.dumps(data)

    def test_backup_detail_omits_destinations_while_disabled(self):
        """`local` is the nominal staging destination even with backups off —
        it must not print as an "active" destination for a disabled feature."""
        data = _collect(verbose=True)
        assert data["details"]["backups"] == "db"

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_SFTP_HOST="offsite.example.com")
    def test_backup_detail_lists_every_active_destination(self):
        data = _collect(verbose=True)
        assert data["details"]["backups"] == "db, destinations: local+sftp"

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True)
    def test_backup_detail_reports_restore_once_one_has_run(self, tmp_path):
        from snapadmin.backup import get_backup_config
        from snapadmin.restore import record_restore_run

        with override_settings(SNAPADMIN_BACKUP_LOCAL_DIR=str(tmp_path)):
            record_restore_run(get_backup_config())
            data = _collect(verbose=True)
        assert data["details"]["backups"] == "db, destinations: local, restored"

    # Both settings are pinned, not just SNAPADMIN_MASKED_FIELDS: the demo project
    # dogfoods masking through SNAPADMIN_MASKING_RULES, and a rule declares its field
    # sensitive on its own, so leaving the rules ambient would let the demo's own
    # configuration count towards the number this test asserts.
    @override_settings(
        SNAPADMIN_MASKED_FIELDS={"demo.customer": ["email", "origin"]},
        SNAPADMIN_MASKING_RULES={},
    )
    def test_pii_masking_counts_fields(self):
        data = _collect(verbose=True)
        assert data["pii_masking"] is True
        assert data["details"]["pii_masking"] == "2 fields"

    @override_settings(SNAPADMIN_MASKED_FIELDS={}, SNAPADMIN_MASKING_RULES={})
    def test_pii_masking_off_when_unconfigured(self):
        assert _collect()["pii_masking"] is False

    @override_settings(
        SNAPADMIN_MASKED_FIELDS={},
        SNAPADMIN_MASKING_RULES={"demo.customer": {"email": {"replacement": "x"}}},
    )
    def test_pii_masking_on_from_rules_alone(self):
        # A rule declares the field sensitive, so masking is adopted even with
        # SNAPADMIN_MASKED_FIELDS empty.
        data = _collect(verbose=True)
        assert data["pii_masking"] is True
        assert data["details"]["pii_masking"] == "1 field, 1 rule"

    @override_settings(
        SNAPADMIN_MASKED_FIELDS={"demo.customer": ["email", "origin"]},
        SNAPADMIN_MASKING_RULES={"demo.customer": {"email": {"replacement": "x"}}},
    )
    def test_pii_masking_counts_the_union_not_the_sum(self):
        data = _collect(verbose=True)
        assert data["details"]["pii_masking"] == "2 fields, 1 rule"

    def test_field_encryption_off_when_no_key_resolves(self):
        """The suite settings carry a keyset (the demo encrypts a column), so
        "unconfigured" has to be stated rather than assumed."""
        with override_settings(SNAPADMIN_ENCRYPTION={}):
            encryption_keys.reset_keyset()
            try:
                assert _collect()["field_encryption"] is False
            finally:
                encryption_keys.reset_keyset()

    def test_field_encryption_on_reports_source_and_fingerprint_only(self):
        import base64

        material = base64.urlsafe_b64encode(b"k" * 32).decode()
        with override_settings(SNAPADMIN_ENCRYPTION={"KEYS": [{"id": "k1", "key": material}]}):
            encryption_keys.reset_keyset()
            try:
                data = _collect(verbose=True)
                keyset = encryption_keys.get_keyset()
            finally:
                encryption_keys.reset_keyset()
        assert data["field_encryption"] is True
        detail = data["details"]["field_encryption"]
        assert detail.startswith(f"settings, 1 key, fingerprint {keyset.fingerprint}")
        assert material not in detail

    def test_field_encryption_counts_the_fields_actually_encrypted(self):
        """A configured key with nothing encrypted is a real state, and common.

        Key management shipped a release before the field types did, so the
        adoption audit has to tell "the key is set up" apart from "the key is
        in use". The demo encrypts ``CustomerProfile.tax_id``, so the live
        count is one.
        """
        data = _collect(verbose=True)
        assert "1 field encrypted" in data["details"]["field_encryption"]

    def test_field_encryption_reports_a_broken_keyset_as_off(self):
        with override_settings(SNAPADMIN_ENCRYPTION={"KEYS": [{"id": "k1", "key": "nonsense !!"}]}):
            encryption_keys.reset_keyset()
            try:
                data = _collect(verbose=True)
            finally:
                encryption_keys.reset_keyset()
        assert data["field_encryption"] is False
        assert data["details"]["field_encryption"] == "misconfigured — run manage.py check"

    def test_a_raising_key_provider_does_not_blank_the_whole_report(self):
        """One broken probe must never cost the other 25 rows.

        `Collector.collect` replaces the entire capability report with a single
        `collector_error` if a probe raises, so a `KEY_PROVIDER` that fails with
        anything other than `ImproperlyConfigured` would turn `snapadmin_info`
        into the one thing it exists to prevent: a report that says nothing
        instead of saying what is on.
        """
        with override_settings(SNAPADMIN_ENCRYPTION={
            "KEY_PROVIDER": "tests.test_diagnostics_features.raising_key_provider",
        }):
            encryption_keys.reset_keyset()
            try:
                data = _collect(verbose=True)
            finally:
                encryption_keys.reset_keyset()
        assert "collector_error" not in data
        assert data["field_encryption"] is False
        assert data["details"]["field_encryption"] == "misconfigured — run manage.py check"
        assert data["rest_api"] is not None  # the neighbouring rows survived

    def test_sharding_off_when_unset(self):
        assert _collect()["sharding"] is False

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "STRATEGY": "hash",
        "SHARDS": {
            "s1": {"PRIMARY": "postgres://u:p@h1:5432/db", "REPLICAS": ["postgres://u:p@r1:5432/db"]},
            "s2": {"PRIMARY": "postgres://u:p@h2:5432/db"},
        },
    })
    def test_sharding_on_reports_shard_replica_counts_and_strategy(self):
        data = _collect(verbose=True)
        assert data["sharding"] is True
        assert data["details"]["sharding"] == "2 shards, 1 replica, strategy 'hash'"

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": False, "SHARDS": {
        "s1": {"PRIMARY": "postgres://u:p@h1:5432/db"}}})
    def test_sharding_off_when_disabled_even_with_shards_declared(self):
        assert _collect()["sharding"] is False

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True, "SHARDS": {"s": {"PRIMARY": "redis://a:6379/0"}}})
    def test_sharding_reports_a_broken_config_as_off(self):
        """Mirrors the encryption collector: never claim a capability is on when
        it cannot even resolve. `manage.py check` reports the specifics (E013)."""
        data = _collect(verbose=True)
        assert data["sharding"] is False
        assert data["details"]["sharding"] == "misconfigured — run manage.py check"

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=["ops@example.com"])
    def test_health_alerts_on_with_recipients(self):
        data = _collect(verbose=True)
        assert data["health_alerts"] is True
        assert data["details"]["health_alerts"] == "1 recipient"

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[])
    def test_health_alerts_off_without_recipients(self):
        assert _collect()["health_alerts"] is False

    @override_settings(SNAPADMIN_THROTTLE_ANON="60/min", SNAPADMIN_THROTTLE_USER=None)
    def test_rate_limiting_on_when_any_throttle_set(self):
        assert _collect()["rate_limiting"] is True

    @override_settings(SNAPADMIN_THROTTLE_ANON=None, SNAPADMIN_THROTTLE_USER=None)
    def test_rate_limiting_off_when_no_throttle(self):
        assert _collect()["rate_limiting"] is False

    @override_settings(SNAPADMIN_API_DELETE_GUARD="demo.guards.can_delete")
    def test_delete_guard_on_when_configured(self):
        assert _collect()["delete_guard"] is True

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_background_tasks_on_with_broker(self):
        assert _collect()["background_tasks"] is True

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_elasticsearch_on_counts_indexed_models(self):
        data = _collect(verbose=True)
        assert data["elasticsearch"] is True
        # The demo has ES-indexed SnapModels (e.g. Product, ExchangeRate).
        assert "indexed model" in data["details"]["elasticsearch"]

    @override_settings(ELASTICSEARCH_ENABLED=False)
    def test_elasticsearch_off_has_no_detail(self):
        data = _collect(verbose=True)
        assert data["elasticsearch"] is False
        assert "elasticsearch" not in data.get("details", {})

    def test_show_in_form_default_off_by_default(self):
        assert _collect()["show_in_form_default"] is False

    @override_settings(SNAPADMIN_SHOW_IN_FORM_DEFAULT=True)
    def test_show_in_form_default_on_when_raised(self):
        assert _collect()["show_in_form_default"] is True


class TestConnectivityAwareness:
    """#JS2e: "on" requires both the setting and an offline-capable model."""

    def test_on_via_demo_s_own_dogfooding(self):
        # demo/core/settings.py explicitly turns this on to dogfood
        # Customer's offline_mode=True — the ambient test settings already
        # exercise the "on" path with no override needed, the same pattern
        # TestProfile uses for SNAPADMIN_PROFILE.
        data = _collect(verbose=True)
        assert data["connectivity_awareness"] is True
        assert "offline-capable model" in data["details"]["connectivity_awareness"]

    @override_settings(SNAPADMIN_CONNECTIVITY_ENABLED=False)
    def test_off_when_setting_is_off(self):
        assert _collect()["connectivity_awareness"] is False

    def test_off_by_default_with_no_dogfooding_override(self, monkeypatch):
        # Without demo's own explicit True (and with no profile entry for this
        # setting — "full" never overrides it), the package's built-in default
        # applies: off.
        from snapadmin import conf as conf_module
        monkeypatch.delattr(conf_module.settings, "SNAPADMIN_CONNECTIVITY_ENABLED", raising=False)
        assert _collect()["connectivity_awareness"] is False

    def test_off_when_no_model_is_offline_capable(self, monkeypatch):
        monkeypatch.setattr(features_collector, "_concrete_snap_models", lambda: [])
        assert _collect()["connectivity_awareness"] is False


@pytest.mark.django_db
class TestModelBasedCapabilities:
    def test_read_only_models_detected(self):
        # demo.ExchangeRate ships api_read_only=True (#FEAT9).
        data = _collect(verbose=True)
        assert data["read_only_models"] is True
        assert "model" in data["details"]["read_only_models"]

    def test_model_validation_detected(self):
        # demo.Product ships api_full_clean=True (#EXT1k).
        data = _collect(verbose=True)
        assert data["model_validation"] is True
        assert "model" in data["details"]["model_validation"]

    def test_model_validation_silent_when_no_model_opts_in(self, monkeypatch):
        from demo.apps.shop.models import Product

        monkeypatch.setattr(Product, "api_full_clean", False, raising=False)
        data = _collect(verbose=True)
        assert data["model_validation"] is False
        assert "model_validation" not in data["details"]

    def test_retention_counts_models(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "data_retention_days", 30, raising=False)
        data = _collect(verbose=True)
        assert data["retention_purge"] is True
        assert "model" in data["details"]["retention_purge"]

    def _neutralise_model_retention(self, monkeypatch):
        """demo.AuditLog and demo.Showcase both carry a permanent
        data_retention_days (#RET2a dogfood / #RET2c dogfood), and AuditLog
        also carries a data_retention_date_field (#EXT1o dogfood) — clear all
        three so a test can exercise the "nothing model-level configured"
        branch."""
        from demo.apps.shop.models import AuditLog, Showcase
        monkeypatch.setattr(AuditLog, "data_retention_days", None, raising=False)
        monkeypatch.setattr(AuditLog, "data_retention_date_field", None, raising=False)
        monkeypatch.setattr(Showcase, "data_retention_days", None, raising=False)

    def test_retention_on_via_audit_log_default_alone(self, monkeypatch):
        # SNAPADMIN_AUDIT_RETENTION_DAYS defaults to 365 (on) — #RET2a made
        # this capability true on an install with zero SnapModels configured.
        self._neutralise_model_retention(monkeypatch)
        data = _collect(verbose=True)
        assert data["retention_purge"] is True
        assert "audit log" in data["details"]["retention_purge"]

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0)
    def test_retention_off_when_nothing_configured(self, monkeypatch):
        self._neutralise_model_retention(monkeypatch)
        assert _collect()["retention_purge"] is False

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0, SNAPADMIN_EXPORT_RETENTION_DAYS=30)
    def test_retention_on_via_export_job_setting_alone(self, monkeypatch):
        self._neutralise_model_retention(monkeypatch)
        data = _collect(verbose=True)
        assert data["retention_purge"] is True
        assert "export jobs" in data["details"]["retention_purge"]

    def test_retention_files_counted_in_detail(self):
        # demo.Showcase declares data_retention_files (#RET2c).
        data = _collect(verbose=True)
        assert "data_retention_files" in data["details"]["retention_purge"]

    def test_retention_date_field_counted_in_detail(self):
        # demo.AuditLog declares data_retention_date_field (#EXT1o).
        data = _collect(verbose=True)
        assert "1 with data_retention_date_field" in data["details"]["retention_purge"]

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0)
    def test_retention_on_via_a_date_field_alone(self, monkeypatch):
        """A model whose only rule is a per-row deadline is still being purged."""
        from demo.apps.shop.models import AuditLog, Showcase
        monkeypatch.setattr(AuditLog, "data_retention_days", None, raising=False)
        monkeypatch.setattr(Showcase, "data_retention_days", None, raising=False)
        data = _collect(verbose=True)
        assert data["retention_purge"] is True
        assert "data_retention_date_field" in data["details"]["retention_purge"]

    def test_retention_date_field_silent_when_no_model_uses_one(self, monkeypatch):
        from demo.apps.shop.models import AuditLog
        monkeypatch.setattr(AuditLog, "data_retention_date_field", None, raising=False)
        data = _collect(verbose=True)
        assert "data_retention_date_field" not in data["details"]["retention_purge"]

    def test_gdpr_subject_access_on_via_demo_customer(self):
        # demo.Customer declares is_data_subject=True (#FUT4a/#FUT4b).
        data = _collect(verbose=True)
        assert data["gdpr_subject_access"] is True
        assert "subject model" in data["details"]["gdpr_subject_access"]
        assert "reachable" in data["details"]["gdpr_subject_access"]

    def test_gdpr_subject_access_off_without_a_subject_model(self, monkeypatch):
        monkeypatch.setattr(features_collector, "_concrete_snap_models", lambda: [])
        data = _collect()
        assert data["gdpr_subject_access"] is False

    def test_write_allowlist_detected(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["name"], raising=False)
        assert _collect()["write_allowlist"] is True

    def test_decorated_models_detected_in_the_demo(self):
        """The demo opts one plain model in with @snap_model, so this reads on."""
        data = _collect(verbose=True)
        assert data["decorated_models"] is True
        assert "plain model" in data["details"]["decorated_models"]

    def test_decorated_models_off_when_every_model_subclasses_snapmodel(self, monkeypatch):
        """The "off" half, scoped to an explicit model list.

        It used to read the live registry and pass only because no installed model was
        decorated — so the day the demo grew one (which is the feature working as
        intended) the test failed for a reason that had nothing to do with the collector.
        """
        from demo.apps.shop.models import Customer, Product
        monkeypatch.setattr(
            features_collector, "_concrete_snap_models", lambda: [Product, Customer],
        )
        data = _collect(verbose=True)
        assert data["decorated_models"] is False
        assert "decorated_models" not in data.get("details", {})

    def test_decorated_models_counts_registered_plain_models(self, monkeypatch):
        """A @snap_model-registered plain model has no ``register_admin`` to skip."""
        from django.db import models as django_models
        from django.test.utils import isolate_apps

        from snapadmin.models import snap_model

        with isolate_apps("snapadmin"):
            @snap_model()
            class Ledger(django_models.Model):
                class Meta:
                    app_label = "snapadmin"

        monkeypatch.setattr(features_collector, "_concrete_snap_models", lambda: [Ledger])
        data = _collect(verbose=True)
        assert data["decorated_models"] is True
        assert data["details"]["decorated_models"] == "1 plain model"

    def test_api_tokens_off_when_none_active(self):
        assert _collect()["api_tokens"] is False

    def test_api_tokens_on_when_active(self, admin_user):
        from snapadmin.models import APIToken
        APIToken.create_for_user(admin_user, "Live")
        data = _collect(verbose=True)
        assert data["api_tokens"] is True
        assert "active token" in data["details"]["api_tokens"]

    def test_api_tokens_fail_soft(self, monkeypatch):
        # A missing/broken APIToken table must degrade to False, never raise.
        from snapadmin.models import APIToken
        monkeypatch.setattr(
            APIToken.objects, "filter",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no table")),
        )
        enabled, detail = features_collector._api_tokens()
        assert enabled is False and detail == ""


class TestSnapActions:
    def test_detected_by_default(self):
        # demo.Order.recalculate_total (#RFC1h dogfood) ships in the demo app.
        data = _collect(verbose=True)
        assert data["snap_actions"] is True
        assert "action" in data["details"]["snap_actions"]
        assert "model" in data["details"]["snap_actions"]

    def test_off_when_no_model_declares_one(self):
        assert features_collector._snap_actions([]) == (False, "")

    def test_fail_soft_when_api_views_unimportable(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "snapadmin.api.views", None)
        try:
            enabled, detail = features_collector._snap_actions(["not-used"])
        finally:
            monkeypatch.delitem(sys.modules, "snapadmin.api.views", raising=False)
        assert enabled is False and detail == ""


class TestFieldPermissions:
    def test_off_when_no_model_in_scope_declares_any(self):
        # Scoped to an explicit model list, like test_detected_when_configured below.
        # Reading the live registry instead used to work only while no demo model
        # declared api_field_permissions — LegacyStockLevel now does, on purpose.
        from demo.apps.shop.models import Product
        enabled, detail = features_collector._field_permissions([Product])
        assert enabled is False
        assert detail == ""

    def test_detected_in_the_demo(self):
        """The demo declares one guarded field, so the live report reads on."""
        assert _collect()["field_permissions"] is True

    def test_detected_when_configured(self, monkeypatch):
        # Scoped to an explicit model list (mirrors
        # test_decorated_models_counts_registered_plain_models below) rather
        # than the live registry, which other concurrently-running test
        # modules may also be registering throwaway models into.
        from demo.apps.shop.models import Customer
        monkeypatch.setattr(
            Customer, "api_field_permissions",
            {"email": {"read": "demo.view_customer"}}, raising=False,
        )
        enabled, detail = features_collector._field_permissions([Customer])
        assert enabled is True
        assert detail == "1 field on 1 model"

    def test_counts_across_several_models(self, monkeypatch):
        from demo.apps.shop.models import Customer, Order
        monkeypatch.setattr(
            Customer, "api_field_permissions",
            {"email": {"read": "demo.view_customer"}, "origin": {"write": "demo.change_customer"}},
            raising=False,
        )
        monkeypatch.setattr(
            Order, "api_field_permissions", {"total": {"write": "demo.change_order"}}, raising=False,
        )
        enabled, detail = features_collector._field_permissions([Customer, Order])
        assert enabled is True
        assert detail == "3 fields on 2 models"


class TestSso:
    def test_sso_off_by_default(self):
        assert _collect()["sso"] is False

    def test_sso_fail_soft(self, monkeypatch):
        import snapadmin.sso as sso
        monkeypatch.setattr(
            sso, "get_sso_providers",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        enabled, detail = features_collector._sso()
        assert enabled is False and detail == ""

    @override_settings(SNAPADMIN_SSO_PROVIDERS={"google": {"label": "Google", "url": "/sso/google/"}})
    def test_sso_on_when_configured(self):
        data = _collect(verbose=True)
        assert data["sso"] is True
        assert "provider" in data["details"]["sso"]


class TestProfile:
    """SNAPADMIN_PROFILE adoption (#SIMPL1g) — "on" means set at all, even to "full"."""

    def test_off_when_not_set(self, monkeypatch):
        from django.conf import settings as django_settings
        monkeypatch.delattr(django_settings, "SNAPADMIN_PROFILE", raising=False)
        assert _collect()["profile"] is False

    def test_on_when_the_demo_s_own_full_default_is_active(self):
        # The demo project's own settings.py sets SNAPADMIN_PROFILE = "full"
        # (a documented no-op, kept to dogfood the setting) — the ambient
        # test settings already exercise this path with no override needed.
        data = _collect(verbose=True)
        assert data["profile"] is True
        assert data["details"]["profile"] == "full"

    @override_settings(SNAPADMIN_PROFILE="admin")
    def test_detail_names_the_active_profile(self):
        data = _collect(verbose=True)
        assert data["profile"] is True
        assert data["details"]["profile"] == "admin"


class TestVerboseAndDetails:
    def test_default_has_no_details_block(self):
        assert "details" not in _collect(verbose=False)

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.customer": ["email"]})
    def test_verbose_adds_details_block(self):
        assert "details" in _collect(verbose=True)


@pytest.mark.django_db
class TestFeaturesInCommand:
    def _run(self, **kwargs):
        out = StringIO()
        call_command("snapadmin_info", stdout=out, **kwargs)
        return out.getvalue()

    def test_section_renders_checklist(self):
        """#CLI7: 16 booleans render as two wrapped on/off runs, not 16 lines."""
        text = self._run(sections=["features"])
        assert "Feature adoption" in text
        assert "✓ on" in text and "✗ off" in text
        assert "Rest api" in text                       # a listed capability
        assert "Rest api:" not in text                  # …not as its own key/value line
        # Two group lines plus their wrapped continuations — far fewer than one per flag.
        assert len(text.strip().splitlines()) < 16

    def test_json_carries_features(self):
        payload = json.loads(self._run(as_json=True, sections=["features"]))
        assert "features" in payload
        assert isinstance(payload["features"]["rest_api"], bool)


def test_verbose_with_no_details_adds_no_details_key(monkeypatch):
    """#QA1d — every capability off and none with a detail string: verbose must
    not add an empty ``details`` map."""
    monkeypatch.setattr(
        features_collector, "_capabilities", lambda: [("backups", False, ""), ("api", False, "")]
    )
    assert features_collector.collect(verbose=True) == {"backups": False, "api": False}
