"""
tests/test_tasks.py

Tests for Celery tasks (api/tasks.py and demo/tasks.py).

All tasks are called synchronously via task.apply() so no broker is needed.
External dependencies (Elasticsearch) are mocked.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# api.tasks.purge_expired_tokens
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredTokens:
    def test_deletes_expired_tokens(self, db, admin_user):
        from snapadmin.models import APIToken
        from snapadmin.tasks import purge_expired_tokens

        APIToken.objects.create(
            user=admin_user,
            token_name="Expired 1",
            expiration_date=timezone.now() - timedelta(days=1),
        )
        APIToken.objects.create(
            user=admin_user,
            token_name="Expired 2",
            expiration_date=timezone.now() - timedelta(hours=2),
        )
        result = purge_expired_tokens()
        assert result["deleted"] >= 2

    def test_keeps_non_expired_tokens(self, api_token):
        from snapadmin.tasks import purge_expired_tokens

        before = __import__("snapadmin.models", fromlist=["APIToken"]).APIToken.objects.count()
        purge_expired_tokens()
        after = __import__("snapadmin.models", fromlist=["APIToken"]).APIToken.objects.count()
        assert after == before  # active, non-expired token must survive

    def test_keeps_inactive_tokens(self, inactive_token):
        """Inactive (but non-expired) tokens are NOT deleted – only expired ones are."""
        from snapadmin.models import APIToken
        from snapadmin.tasks import purge_expired_tokens

        pk = inactive_token.pk
        purge_expired_tokens()
        assert APIToken.objects.filter(pk=pk).exists()

    def test_returns_deleted_count(self, db, admin_user):
        from snapadmin.models import APIToken
        from snapadmin.tasks import purge_expired_tokens

        APIToken.objects.create(
            user=admin_user,
            token_name="Old",
            expiration_date=timezone.now() - timedelta(seconds=1),
        )
        result = purge_expired_tokens()
        assert isinstance(result["deleted"], int)
        assert result["deleted"] >= 1

    def test_returns_cutoff_timestamp(self, db):
        from snapadmin.tasks import purge_expired_tokens
        result = purge_expired_tokens()
        assert "cutoff" in result

    def test_zero_deleted_when_none_expired(self, api_token):
        from snapadmin.tasks import purge_expired_tokens
        result = purge_expired_tokens()
        # api_token never expires – nothing should be deleted
        assert result["deleted"] == 0

    def test_reports_ok_status(self, db):
        """A real DB failure here already propagates naturally (no swallowed
        exception around the bulk delete), so this task always reports "ok"
        — see the outcome convention in the module docstring."""
        from snapadmin.tasks import purge_expired_tokens
        result = purge_expired_tokens()
        assert result["status"] == "ok"
        assert result["failed"] == []


# ─────────────────────────────────────────────────────────────────────────────
# snapadmin.tasks.purge_expired_data — the outcome convention (#OPS2c)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredDataOutcome:
    """Status/failed/raise on top of the retention sweep — see
    tests/test_data_retention.py for the retention behaviour itself.

    ``apps.get_models()`` is monkeypatched to an explicit, small model set in
    every test here rather than relying on however many demo models happen to
    have ``data_retention_days`` set today — that count is not this test's
    concern and changes independently (#RET2/#RET2c dogfood models). Only
    ``SnapadminAuditLog`` (#RET2a) and the export-job sweep (#RET2b) are
    always considered outside that loop; ``SNAPADMIN_EXPORT_RETENTION_DAYS``
    is unset by default (opt-in), so only the audit log needs neutralising
    via ``SNAPADMIN_AUDIT_RETENTION_DAYS=0`` where a test wants "nothing but
    the model(s) it names" considered.
    """

    def test_noop_when_nothing_has_retention_configured(self, monkeypatch):
        from django.apps import apps
        from snapadmin.tasks import purge_expired_data

        monkeypatch.setattr(apps, "get_models", lambda: [])
        with override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0):
            result = purge_expired_data()
        assert result["status"] == "noop"
        assert result["failed"] == []

    def test_ok_when_every_considered_model_succeeds(self, monkeypatch):
        from django.apps import apps
        from demo.apps.shop.models import AuditLog
        from snapadmin.tasks import purge_expired_data

        monkeypatch.setattr(apps, "get_models", lambda: [AuditLog])
        with override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0):
            result = purge_expired_data()
        assert result["status"] == "ok"
        assert result["failed"] == []

    def test_partial_when_some_but_not_every_model_fails(self, monkeypatch):
        """demo.AuditLog fails; SnapadminAuditLog (also considered, since its
        own retention is left at its default here) succeeds — some, not
        every, unit failed, so this must never raise (D1)."""
        from django.apps import apps
        from demo.apps.shop.models import AuditLog
        from snapadmin.tasks import purge_expired_data

        monkeypatch.setattr(apps, "get_models", lambda: [AuditLog])
        with patch.object(AuditLog, "purge_expired", side_effect=RuntimeError("db locked")):
            result = purge_expired_data()
        assert result["status"] == "partial"
        assert result["failed"] == ["demo.AuditLog"]

    def test_total_failure_raises(self, monkeypatch):
        """The only model considered fails outright — every unit failed, so
        this raises SnapPurgeError instead of returning (D1)."""
        from django.apps import apps
        from demo.apps.shop.models import AuditLog
        from snapadmin.models import SnapPurgeError
        from snapadmin.tasks import purge_expired_data

        monkeypatch.setattr(apps, "get_models", lambda: [AuditLog])
        with override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0), \
                patch.object(AuditLog, "purge_expired", side_effect=RuntimeError("db locked")):
            with pytest.raises(SnapPurgeError, match="demo.AuditLog"):
                purge_expired_data()

    def test_export_jobs_considered_and_reported_when_enabled(self, monkeypatch):
        from django.apps import apps
        from snapadmin.tasks import purge_expired_data

        monkeypatch.setattr(apps, "get_models", lambda: [])
        with override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0,
                                SNAPADMIN_EXPORT_RETENTION_DAYS=30):
            result = purge_expired_data()
        assert result["status"] == "ok"
        assert "snapadmin.export_jobs" in result["purged"]
        assert result["export_jobs"]["enabled"] is True

    def test_export_jobs_failure_reported_as_partial(self, monkeypatch):
        """SNAPADMIN_AUDIT_RETENTION_DAYS is left at its default (on) here so
        the audit-log purge is also considered and succeeds trivially (no
        rows) alongside the failing export-jobs sweep — partial needs at
        least one success next to the failure; all-considered-failed raises
        instead (see test_total_failure_raises above)."""
        from django.apps import apps
        from snapadmin import exporting
        from snapadmin.tasks import purge_expired_data

        monkeypatch.setattr(apps, "get_models", lambda: [])
        monkeypatch.setattr(
            exporting, "purge_expired_export_jobs",
            lambda now=None: {
                "enabled": True, "jobs_deleted": {}, "files_deleted": 0,
                "orphan_files_deleted": 0, "failed": ["boom"],
            },
        )
        result = purge_expired_data()
        assert result["status"] == "partial"
        assert "snapadmin.export_jobs" in result["errors"]
        assert "boom" in result["errors"]["snapadmin.export_jobs"]


# ─────────────────────────────────────────────────────────────────────────────
# snapadmin.tasks.send_error_digest — the outcome convention (#OPS2c)
# ─────────────────────────────────────────────────────────────────────────────

class TestSendErrorDigestOutcome:
    def test_sent_is_ok(self):
        from snapadmin.tasks import send_error_digest

        fake = {"sent": True, "errors": 3, "groups": 1, "channels": "email", "purged": 0}
        with patch("snapadmin.monitoring.send_error_digest", return_value=fake):
            result = send_error_digest()
        assert result["status"] == "ok"
        assert result["failed"] == []

    @pytest.mark.parametrize("reason", ["disabled", "no_recipients"])
    def test_disabled_or_no_recipients_is_disabled_status(self, reason):
        from snapadmin.tasks import send_error_digest

        fake = {"sent": False, "reason": reason, "errors": 0, "purged": 0}
        with patch("snapadmin.monitoring.send_error_digest", return_value=fake):
            result = send_error_digest()
        assert result["status"] == "disabled"

    def test_no_errors_is_noop(self):
        from snapadmin.tasks import send_error_digest

        fake = {"sent": False, "reason": "no_errors", "errors": 0, "purged": 0}
        with patch("snapadmin.monitoring.send_error_digest", return_value=fake):
            result = send_error_digest()
        assert result["status"] == "noop"

    def test_delivery_failed_raises(self):
        """Every channel failed to deliver — a total failure (D1), so this
        raises instead of returning a value a monitor could read as sent."""
        from snapadmin.alerts import AlertDeliveryError
        from snapadmin.tasks import send_error_digest

        fake = {"sent": False, "reason": "delivery_failed", "errors": 5, "groups": 1, "purged": 0}
        with patch("snapadmin.monitoring.send_error_digest", return_value=fake):
            with pytest.raises(AlertDeliveryError):
                send_error_digest()


# ─────────────────────────────────────────────────────────────────────────────
# snapadmin.tasks.run_es_reindex — the outcome convention (#OPS2c)
# ─────────────────────────────────────────────────────────────────────────────

class TestRunEsReindexOutcome:
    def test_noop_when_nothing_was_attempted(self):
        from snapadmin.tasks import run_es_reindex

        fake = {
            "models": 1, "indexed_models": 0, "errored_models": 0,
            "results": {"demo.Product": {"skipped": True, "reason": "Elasticsearch not available"}},
        }
        with patch("snapadmin.models.run_reindex", return_value=fake):
            result = run_es_reindex()
        assert result["status"] == "noop"
        assert result["failed"] == []

    def test_ok_when_every_attempted_model_succeeds(self):
        from snapadmin.tasks import run_es_reindex

        fake = {
            "models": 1, "indexed_models": 1, "errored_models": 0,
            "results": {"demo.Product": {"indexed": 10}},
        }
        with patch("snapadmin.models.run_reindex", return_value=fake):
            result = run_es_reindex()
        assert result["status"] == "ok"
        assert result["failed"] == []

    def test_partial_when_some_models_error_including_rejected_documents(self):
        """A model with some rejected documents (indexed > 0 alongside a
        non-empty errors list) is partial, never ok — see #OPS2c."""
        from snapadmin.tasks import run_es_reindex

        fake = {
            "models": 2, "indexed_models": 1, "errored_models": 1,
            "results": {
                "demo.Product": {"indexed": 10},
                "demo.Customer": {"indexed": 8, "errors": ["doc rejected: mapping mismatch"]},
            },
        }
        with patch("snapadmin.models.run_reindex", return_value=fake):
            result = run_es_reindex()
        assert result["status"] == "partial"
        assert result["failed"] == ["demo.Customer"]

    def test_total_failure_raises(self):
        from snapadmin.tasks import ReindexError, run_es_reindex

        fake = {
            "models": 1, "indexed_models": 0, "errored_models": 1,
            "results": {"demo.Product": {"indexed": 0, "errors": ["connection refused"]}},
        }
        with patch("snapadmin.models.run_reindex", return_value=fake):
            with pytest.raises(ReindexError, match="demo.Product"):
                run_es_reindex()


# ─────────────────────────────────────────────────────────────────────────────
# snapadmin.tasks.send_health_alert — the outcome convention (#OPS2c)
# ─────────────────────────────────────────────────────────────────────────────

class TestSendHealthAlertOutcome:
    def test_sent_is_ok(self):
        from snapadmin.tasks import send_health_alert

        fake = {"sent": True, "checked": 4, "failing": 1, "failing_names": "database"}
        with patch("snapadmin.health.send_health_alert", return_value=fake):
            result = send_health_alert()
        assert result["status"] == "ok"

    def test_healthy_is_ok(self):
        from snapadmin.tasks import send_health_alert

        fake = {"sent": False, "reason": "healthy", "checked": 4, "failing": 0}
        with patch("snapadmin.health.send_health_alert", return_value=fake):
            result = send_health_alert()
        assert result["status"] == "ok"

    @pytest.mark.parametrize("reason", ["disabled", "no_recipients"])
    def test_disabled_or_no_recipients_is_disabled_status(self, reason):
        from snapadmin.tasks import send_health_alert

        fake = {"sent": False, "reason": reason, "checked": 4, "failing": 1}
        with patch("snapadmin.health.send_health_alert", return_value=fake):
            result = send_health_alert()
        assert result["status"] == "disabled"

    def test_cooldown_is_noop(self):
        """A persistent outage under cooldown: nothing new to report on this
        run — the original alert already went out when the cooldown armed."""
        from snapadmin.tasks import send_health_alert

        fake = {"sent": False, "reason": "cooldown", "checked": 4, "failing": 1}
        with patch("snapadmin.health.send_health_alert", return_value=fake):
            result = send_health_alert()
        assert result["status"] == "noop"

    def test_delivery_failed_raises(self):
        from snapadmin.alerts import AlertDeliveryError
        from snapadmin.tasks import send_health_alert

        fake = {
            "sent": False, "reason": "delivery_failed", "checked": 4, "failing": 1,
            "failing_names": "database",
        }
        with patch("snapadmin.health.send_health_alert", return_value=fake):
            with pytest.raises(AlertDeliveryError):
                send_health_alert()


# ─────────────────────────────────────────────────────────────────────────────
# snapadmin.tasks.run_db_backups — thin pass-through, see tests/test_backup.py
# ─────────────────────────────────────────────────────────────────────────────

class TestRunDbBackupsOutcome:
    def test_pass_through_carries_status_and_failed(self):
        """run_due_backups() already applies the outcome convention and is
        exhaustively tested in tests/test_backup.py — this just pins that
        the task returns it unmodified."""
        from snapadmin.tasks import run_db_backups

        fake = {"ran": False, "reason": "disabled", "results": {}, "status": "disabled", "failed": []}
        with patch("snapadmin.backup.run_due_backups", return_value=fake):
            result = run_db_backups()
        assert result == fake


# ─────────────────────────────────────────────────────────────────────────────
# demo.tasks.generate_daily_stats
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGenerateDailyStats:
    def test_returns_dict(self, product, customer, order):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert isinstance(result, dict)

    def test_has_required_keys(self, product, customer, order):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        for key in ("date", "total_products", "active_products",
                    "total_customers", "active_customers",
                    "total_orders", "total_revenue", "avg_order_value"):
            assert key in result, f"Missing key: {key}"

    def test_counts_products(self, product, product_unavailable):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_products"] >= 2

    def test_counts_only_available_products(self, product, product_unavailable):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["active_products"] >= 1
        assert result["active_products"] < result["total_products"]

    def test_counts_customers(self, customer, customer_inactive):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_customers"] >= 2

    def test_counts_only_active_customers(self, customer, customer_inactive):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["active_customers"] < result["total_customers"]

    def test_revenue_is_float(self, order):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert isinstance(result["total_revenue"], float)

    def test_revenue_sums_orders(self, order):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_revenue"] >= float(order.total)

    def test_avg_order_value_is_float(self, order):
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert isinstance(result["avg_order_value"], float)

    def test_date_is_today(self):
        from datetime import date
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["date"] == date.today().isoformat()

    def test_works_with_empty_db(self, db):
        """Task should not crash on an empty database."""
        from demo.apps.shop.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_products"] == 0
        assert result["total_revenue"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# demo.tasks.reindex_products_to_elasticsearch
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReindexProductsToElasticsearch:
    def test_skips_when_es_unavailable(self, product):
        with patch("demo.apps.shop.search.is_es_available", return_value=False):
            from demo.apps.shop.tasks import reindex_products_to_elasticsearch
            result = reindex_products_to_elasticsearch()
        assert result["skipped"] is True

    def test_skip_reason_in_result(self, product):
        with patch("demo.apps.shop.search.is_es_available", return_value=False):
            from demo.apps.shop.tasks import reindex_products_to_elasticsearch
            result = reindex_products_to_elasticsearch()
        assert "reason" in result

    def test_indexes_products_when_es_available(self, product):
        from unittest.mock import MagicMock
        mock_es = MagicMock()
        with patch("demo.apps.shop.search.is_es_available", return_value=True), \
             patch("demo.apps.shop.search.get_es_client", return_value=mock_es):
            from demo.apps.shop.tasks import reindex_products_to_elasticsearch
            result = reindex_products_to_elasticsearch()
        assert result["indexed"] >= 1
        mock_es.index.assert_called()

    def test_returns_indexed_count(self, many_products):
        from unittest.mock import MagicMock
        mock_es = MagicMock()
        with patch("demo.apps.shop.search.is_es_available", return_value=True), \
             patch("demo.apps.shop.search.get_es_client", return_value=mock_es):
            from demo.apps.shop.tasks import reindex_products_to_elasticsearch
            result = reindex_products_to_elasticsearch()
        assert result["indexed"] == 30  # many_products creates 30
