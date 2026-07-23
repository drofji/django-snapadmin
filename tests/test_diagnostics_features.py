"""Tests for the ``snapadmin_info`` feature-adoption collector (#CLI5)."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from snapadmin.diagnostics import features as features_collector
from snapadmin.diagnostics import get_collector


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


class TestSettingsGatedCapabilities:
    @override_settings(SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_GRAPHQL_ENABLED=False)
    def test_api_surfaces_reflect_flags(self):
        data = _collect()
        assert data["rest_api"] is False
        assert data["graphql"] is False

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True)
    def test_backups_on_when_enabled(self):
        assert _collect()["backups"] is True

    def test_backups_off_by_default(self):
        assert _collect()["backups"] is False

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.customer": ["email", "origin"]})
    def test_pii_masking_counts_fields(self):
        data = _collect(verbose=True)
        assert data["pii_masking"] is True
        assert data["details"]["pii_masking"] == "2 fields"

    @override_settings(SNAPADMIN_MASKED_FIELDS={})
    def test_pii_masking_off_when_unconfigured(self):
        assert _collect()["pii_masking"] is False

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


@pytest.mark.django_db
class TestModelBasedCapabilities:
    def test_read_only_models_detected(self):
        # demo.ExchangeRate ships api_read_only=True (#FEAT9).
        data = _collect(verbose=True)
        assert data["read_only_models"] is True
        assert "model" in data["details"]["read_only_models"]

    def test_retention_counts_models(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "data_retention_days", 30, raising=False)
        data = _collect(verbose=True)
        assert data["retention_purge"] is True
        assert "model" in data["details"]["retention_purge"]

    def test_write_allowlist_detected(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["name"], raising=False)
        assert _collect()["write_allowlist"] is True

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
        text = self._run(sections=["features"])
        assert "Feature adoption" in text
        assert "Rest api:" in text  # a rendered capability line

    def test_json_carries_features(self):
        payload = json.loads(self._run(as_json=True, sections=["features"]))
        assert "features" in payload
        assert isinstance(payload["features"]["rest_api"], bool)
