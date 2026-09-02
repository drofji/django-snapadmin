"""
tests/test_data_retention.py

Tests for GDPR data retention:
  - SnapModel.data_retention_days / data_retention_field class attributes
  - purge_expired_data Celery task
  - purge_expired_data management command (dry-run and live)
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# SnapModel attribute defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestRetentionDefaults:
    def test_default_retention_days_is_none(self):
        from snapadmin.models import SnapModel
        assert SnapModel.data_retention_days is None

    def test_default_retention_field_is_created_at(self):
        from snapadmin.models import SnapModel
        assert SnapModel.data_retention_field == "created_at"


# ─────────────────────────────────────────────────────────────────────────────
# AuditLog demo model
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLogRetentionConfig:
    def test_audit_log_has_retention_days(self):
        from demo.apps.shop.models import AuditLog
        assert AuditLog.data_retention_days == 90

    def test_audit_log_retention_field(self):
        from demo.apps.shop.models import AuditLog
        assert AuditLog.data_retention_field == "created_at"


# ─────────────────────────────────────────────────────────────────────────────
# purge_expired_data task
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredDataTask:
    def _create_old_log(self, days_old: int):
        from demo.apps.shop.models import AuditLog
        obj = AuditLog.objects.create(action="login", user_email="test@example.com")
        stale_ts = timezone.now() - timedelta(days=days_old)
        AuditLog.objects.filter(pk=obj.pk).update(created_at=stale_ts)
        return obj

    def test_deletes_records_older_than_retention(self):
        from snapadmin.tasks import purge_expired_data
        old = self._create_old_log(days_old=91)
        result = purge_expired_data()
        from demo.apps.shop.models import AuditLog
        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert result["total"] >= 1

    def test_keeps_records_within_retention(self):
        from snapadmin.tasks import purge_expired_data
        from demo.apps.shop.models import AuditLog
        recent = AuditLog.objects.create(action="logout", user_email="user@example.com")
        purge_expired_data()
        assert AuditLog.objects.filter(pk=recent.pk).exists()

    def test_returns_summary_dict(self):
        from snapadmin.tasks import purge_expired_data
        result = purge_expired_data()
        assert "purged" in result
        assert "total" in result
        assert isinstance(result["total"], int)

    def test_purge_returns_per_model_counts(self):
        from snapadmin.tasks import purge_expired_data
        self._create_old_log(days_old=100)
        result = purge_expired_data()
        assert "demo.AuditLog" in result["purged"]
        assert result["purged"]["demo.AuditLog"] >= 1

    def test_no_retention_model_not_in_summary(self):
        from snapadmin.tasks import purge_expired_data
        from demo.apps.shop.models import Product
        Product.objects.create(name="Safe Product", price=10)
        result = purge_expired_data()
        assert "demo.Product" not in result["purged"]

    def test_empty_db_returns_zero_total(self):
        from snapadmin.tasks import purge_expired_data
        result = purge_expired_data()
        assert isinstance(result["total"], int)


# ─────────────────────────────────────────────────────────────────────────────
# purge_expired_data management command
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredDataCommand:
    def _create_old_log(self, days_old: int):
        from demo.apps.shop.models import AuditLog
        obj = AuditLog.objects.create(action="cmd_test", user_email="cmd@example.com")
        stale_ts = timezone.now() - timedelta(days=days_old)
        AuditLog.objects.filter(pk=obj.pk).update(created_at=stale_ts)
        return obj

    def _call_command(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("snapadmin_purge_expired_data", *args, stdout=out)
        return out.getvalue()

    def test_command_runs_without_error(self):
        output = self._call_command()
        assert "Total deleted" in output

    def test_dry_run_does_not_delete(self):
        from demo.apps.shop.models import AuditLog
        old = self._create_old_log(days_old=91)
        self._call_command("--dry-run")
        assert AuditLog.objects.filter(pk=old.pk).exists()

    def test_dry_run_output_mentions_dry_run(self):
        self._create_old_log(days_old=91)
        output = self._call_command("--dry-run")
        assert "DRY RUN" in output or "dry run" in output.lower()

    def test_live_run_deletes_old_records(self):
        from demo.apps.shop.models import AuditLog
        old = self._create_old_log(days_old=91)
        self._call_command()
        assert not AuditLog.objects.filter(pk=old.pk).exists()


# ─────────────────────────────────────────────────────────────────────────────
# SnapModel.purge_expired() — the centralised, multi-storage purge
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredDbOnly:
    """DB_ONLY models (e.g. AuditLog) purge straight from the database."""

    def _old_log(self, days_old: int):
        from demo.apps.shop.models import AuditLog
        obj = AuditLog.objects.create(action="x", user_email="a@b.c")
        AuditLog.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return obj

    def test_deletes_expired(self):
        from demo.apps.shop.models import AuditLog
        old = self._old_log(91)
        assert AuditLog.purge_expired() == 1
        assert not AuditLog.objects.filter(pk=old.pk).exists()

    def test_keeps_recent(self):
        from demo.apps.shop.models import AuditLog
        recent = AuditLog.objects.create(action="y", user_email="r@b.c")
        AuditLog.purge_expired()
        assert AuditLog.objects.filter(pk=recent.pk).exists()

    def test_dry_run_counts_without_deleting(self):
        from demo.apps.shop.models import AuditLog
        old = self._old_log(91)
        assert AuditLog.purge_expired(dry_run=True) == 1
        assert AuditLog.objects.filter(pk=old.pk).exists()

    def test_no_retention_returns_zero(self):
        from demo.apps.shop.models import Category
        assert Category.purge_expired() == 0


@pytest.mark.django_db
class TestPurgeExpiredDual:
    """DUAL models must clear the ES mirror as well as the DB rows.

    Driven by patching AuditLog (which has both ``created_at`` and a retention
    window) into DUAL mode — there is no demo model that is DUAL + retention.
    """

    def _old_log(self, days_old: int):
        from demo.apps.shop.models import AuditLog
        obj = AuditLog.objects.create(action="x", user_email="a@b.c")
        AuditLog.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return obj

    def test_deletes_db_and_es(self):
        from demo.apps.shop.models import AuditLog
        from snapadmin.models import EsStorageMode
        from unittest.mock import MagicMock, patch
        from django.test import override_settings

        old = self._old_log(91)
        mock_es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(AuditLog, "es_storage_mode", EsStorageMode.DUAL), \
             patch.object(AuditLog, "get_es_client", return_value=mock_es):
            count = AuditLog.purge_expired()

        assert count == 1
        assert not AuditLog.objects.filter(pk=old.pk).exists()
        mock_es.delete_by_query.assert_called_once()
        _, kwargs = mock_es.delete_by_query.call_args
        assert kwargs["index"] == AuditLog.get_es_index_name()
        assert kwargs["body"]["query"]["ids"]["values"] == [old.pk]

    def test_dry_run_skips_db_and_es(self):
        from demo.apps.shop.models import AuditLog
        from snapadmin.models import EsStorageMode
        from unittest.mock import MagicMock, patch
        from django.test import override_settings

        old = self._old_log(91)
        mock_es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(AuditLog, "es_storage_mode", EsStorageMode.DUAL), \
             patch.object(AuditLog, "get_es_client", return_value=mock_es):
            count = AuditLog.purge_expired(dry_run=True)

        assert count == 1
        assert AuditLog.objects.filter(pk=old.pk).exists()
        mock_es.delete_by_query.assert_not_called()

    def test_es_failure_raises_and_is_not_reported_as_success(self):
        """If the ES mirror can't be cleared, purge_expired() must not report a
        clean success — the DB rows are already gone, but the caller (the Celery
        task / management command) needs to know this model's purge is partial.
        """
        from demo.apps.shop.models import AuditLog
        from snapadmin.models import EsStorageMode, SnapPurgeError
        from unittest.mock import MagicMock, patch
        from django.test import override_settings

        old = self._old_log(91)
        mock_es = MagicMock()
        mock_es.delete_by_query.side_effect = RuntimeError("es unreachable")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(AuditLog, "es_storage_mode", EsStorageMode.DUAL), \
             patch.object(AuditLog, "get_es_client", return_value=mock_es):
            with pytest.raises(SnapPurgeError):
                AuditLog.purge_expired()

        # The DB delete already happened (no 2PC across stores) — but the
        # exception is what tells the caller this purge was not clean.
        assert not AuditLog.objects.filter(pk=old.pk).exists()

    def test_task_reports_es_failure_as_error_not_purged(self):
        """The Celery task must surface a partial DUAL purge as an error, not
        silently count the model as fully purged.
        """
        from demo.apps.shop.models import AuditLog
        from snapadmin.models import EsStorageMode
        from snapadmin.tasks import purge_expired_data
        from unittest.mock import MagicMock, patch
        from django.test import override_settings

        self._old_log(91)
        mock_es = MagicMock()
        mock_es.delete_by_query.side_effect = RuntimeError("es unreachable")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(AuditLog, "es_storage_mode", EsStorageMode.DUAL), \
             patch.object(AuditLog, "get_es_client", return_value=mock_es):
            result = purge_expired_data()

        assert "demo.AuditLog" not in result["purged"]
        assert "demo.AuditLog" in result["errors"]


class TestDeletePksFromEs:
    """_delete_pks_from_es issues one bulk delete_by_query call and reports
    success/failure via its return value instead of swallowing failures silently.
    """

    def test_no_pks_is_noop(self):
        from demo.apps.shop.models import Product
        from unittest.mock import patch
        with patch.object(Product, "get_es_client") as client:
            assert Product._delete_pks_from_es([]) is True
            client.assert_not_called()

    def test_es_disabled_is_noop(self):
        from demo.apps.shop.models import Product
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=False), \
             patch.object(Product, "get_es_client") as client:
            assert Product._delete_pks_from_es([1, 2]) is True
            client.assert_not_called()

    def test_bulk_deletes_all_pks_in_one_call(self):
        from demo.apps.shop.models import Product
        from unittest.mock import MagicMock, patch
        from django.test import override_settings
        mock_es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(Product, "get_es_client", return_value=mock_es):
            assert Product._delete_pks_from_es([7, 8, 9]) is True
        mock_es.delete_by_query.assert_called_once()
        mock_es.delete.assert_not_called()
        _, kwargs = mock_es.delete_by_query.call_args
        assert kwargs["index"] == Product.get_es_index_name()
        assert kwargs["body"] == {"query": {"ids": {"values": [7, 8, 9]}}}
        assert kwargs["ignore"] == [404]

    def test_reports_failure_without_raising(self):
        from demo.apps.shop.models import Product
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(Product, "get_es_client", side_effect=RuntimeError("boom")):
            assert Product._delete_pks_from_es([1]) is False  # must not raise


class TestPurgeExpiredEsOnly:
    """ES_ONLY models purge via a range delete_by_query against the index."""

    def test_es_disabled_returns_zero(self):
        from demo.apps.shop.models import SearchLog
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=False), \
             patch.object(SearchLog, "data_retention_days", 30):
            assert SearchLog.purge_expired() == 0

    def test_delete_by_query(self):
        from demo.apps.shop.models import SearchLog
        from unittest.mock import MagicMock, patch
        from django.test import override_settings
        mock_es = MagicMock()
        mock_es.delete_by_query.return_value = {"deleted": 5}
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(SearchLog, "data_retention_days", 30), \
             patch.object(SearchLog, "data_retention_field", "timestamp"), \
             patch.object(SearchLog, "get_es_client", return_value=mock_es):
            assert SearchLog.purge_expired() == 5
        args, kwargs = mock_es.delete_by_query.call_args
        assert kwargs["index"] == SearchLog.get_es_index_name()
        assert "timestamp" in kwargs["body"]["query"]["range"]

    def test_dry_run_uses_count(self):
        from demo.apps.shop.models import SearchLog
        from unittest.mock import MagicMock, patch
        from django.test import override_settings
        mock_es = MagicMock()
        mock_es.count.return_value = {"count": 3}
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(SearchLog, "data_retention_days", 30), \
             patch.object(SearchLog, "get_es_client", return_value=mock_es):
            assert SearchLog.purge_expired(dry_run=True) == 3
        mock_es.delete_by_query.assert_not_called()

    def test_swallows_exceptions(self):
        from demo.apps.shop.models import SearchLog
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(SearchLog, "data_retention_days", 30), \
             patch.object(SearchLog, "get_es_client", side_effect=RuntimeError("boom")):
            assert SearchLog.purge_expired() == 0


# ─────────────────────────────────────────────────────────────────────────────
# SnapadminAuditLog.purge_expired() — the audit trail is not a SnapModel, so it
# carries its own retention machinery (#RET2a)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuditLogPurgeExpired:
    def _old_entry(self, days_old: int):
        from snapadmin.models import SnapadminAuditLog
        entry = SnapadminAuditLog.objects.create(action="create", actor_repr="tester")
        SnapadminAuditLog.objects.filter(pk=entry.pk).update(
            timestamp=timezone.now() - timedelta(days=days_old)
        )
        return entry

    def test_default_retention_is_365_days(self):
        from snapadmin.models import SnapadminAuditLog
        assert SnapadminAuditLog.data_retention_days() == 365

    def test_retention_days_reads_the_setting_live(self, settings):
        from snapadmin.models import SnapadminAuditLog
        settings.SNAPADMIN_AUDIT_RETENTION_DAYS = 10
        assert SnapadminAuditLog.data_retention_days() == 10

    def test_retention_field_is_timestamp(self):
        from snapadmin.models import SnapadminAuditLog
        assert SnapadminAuditLog.data_retention_field == "timestamp"

    def test_deletes_expired_rows(self):
        from snapadmin.models import SnapadminAuditLog
        old = self._old_entry(366)
        assert SnapadminAuditLog.purge_expired() == 1
        assert not SnapadminAuditLog.objects.filter(pk=old.pk).exists()

    def test_keeps_recent_rows(self):
        from snapadmin.models import SnapadminAuditLog
        recent = SnapadminAuditLog.objects.create(action="create", actor_repr="tester")
        SnapadminAuditLog.purge_expired()
        assert SnapadminAuditLog.objects.filter(pk=recent.pk).exists()

    def test_dry_run_counts_without_deleting(self):
        from snapadmin.models import SnapadminAuditLog
        old = self._old_entry(366)
        assert SnapadminAuditLog.purge_expired(dry_run=True) == 1
        assert SnapadminAuditLog.objects.filter(pk=old.pk).exists()

    def test_disabled_returns_zero(self, settings):
        from snapadmin.models import SnapadminAuditLog
        settings.SNAPADMIN_AUDIT_RETENTION_DAYS = 0
        self._old_entry(9999)
        assert SnapadminAuditLog.purge_expired() == 0

    def test_purge_uses_queryset_delete_not_instance_delete(self):
        """QuerySet.delete() is the one sanctioned bypass of the append-only
        guard — purge_expired() must not go through the instance .delete()
        (which raises ValidationError) or the purge itself would explode.
        """
        from snapadmin.models import SnapadminAuditLog
        self._old_entry(366)
        # No exception means the guard was correctly bypassed via QuerySet.delete().
        SnapadminAuditLog.purge_expired()


@pytest.mark.django_db
class TestPurgeExpiredDataTaskIncludesAuditLog:
    def _old_entry(self, days_old: int):
        from snapadmin.models import SnapadminAuditLog
        entry = SnapadminAuditLog.objects.create(action="create", actor_repr="tester")
        SnapadminAuditLog.objects.filter(pk=entry.pk).update(
            timestamp=timezone.now() - timedelta(days=days_old)
        )
        return entry

    def test_task_purges_the_audit_log_too(self):
        from snapadmin.tasks import purge_expired_data
        old = self._old_entry(366)
        result = purge_expired_data()
        from snapadmin.models import SnapadminAuditLog
        assert not SnapadminAuditLog.objects.filter(pk=old.pk).exists()
        assert result["purged"]["snapadmin.SnapadminAuditLog"] >= 1

    def test_task_skips_audit_log_when_disabled(self, settings):
        from snapadmin.tasks import purge_expired_data
        settings.SNAPADMIN_AUDIT_RETENTION_DAYS = 0
        self._old_entry(9999)
        result = purge_expired_data()
        assert "snapadmin.SnapadminAuditLog" not in result["purged"]

    def test_task_reports_audit_log_purge_error(self, monkeypatch):
        from snapadmin.models import SnapadminAuditLog
        from snapadmin.tasks import purge_expired_data

        def boom(*, now=None):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(SnapadminAuditLog, "purge_expired", staticmethod(boom))
        result = purge_expired_data()
        assert "snapadmin.SnapadminAuditLog" in result["errors"]


@pytest.mark.django_db
class TestPurgeExpiredDataCommandIncludesAuditLog:
    def _call_command(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("snapadmin_purge_expired_data", *args, stdout=out)
        return out.getvalue()

    def _old_entry(self, days_old: int):
        from snapadmin.models import SnapadminAuditLog
        entry = SnapadminAuditLog.objects.create(action="create", actor_repr="tester")
        SnapadminAuditLog.objects.filter(pk=entry.pk).update(
            timestamp=timezone.now() - timedelta(days=days_old)
        )
        return entry

    def test_command_purges_the_audit_log(self):
        from snapadmin.models import SnapadminAuditLog
        old = self._old_entry(366)
        output = self._call_command()
        assert not SnapadminAuditLog.objects.filter(pk=old.pk).exists()
        assert "snapadmin.SnapadminAuditLog" in output

    def test_command_dry_run_does_not_delete_audit_log(self):
        from snapadmin.models import SnapadminAuditLog
        old = self._old_entry(366)
        self._call_command("--dry-run")
        assert SnapadminAuditLog.objects.filter(pk=old.pk).exists()

    def test_command_reports_audit_log_purge_error(self):
        from snapadmin.models import SnapadminAuditLog

        def boom(*, now=None, dry_run=False):
            raise RuntimeError("db unavailable")

        with patch.object(SnapadminAuditLog, "purge_expired", staticmethod(boom)):
            output = self._call_command()
        assert "ERROR snapadmin.SnapadminAuditLog" in output


@pytest.mark.django_db
class TestPurgeExpiredDataCommandIncludesExportJobs:
    def _call_command(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("snapadmin_purge_expired_data", *args, stdout=out)
        return out.getvalue()

    def _old_export_job(self, days_old: int):
        from snapadmin.models import SnapExportJob
        job = SnapExportJob.objects.create(
            app_label="demo", model="Product", export_format="csv",
            status=SnapExportJob.Status.COMPLETED, file_name="cmd_export.csv",
        )
        SnapExportJob.objects.filter(pk=job.pk).update(
            finished_at=timezone.now() - timedelta(days=days_old)
        )
        return job

    def test_disabled_by_default_prints_nothing_about_jobs(self):
        output = self._call_command()
        assert "export_jobs" not in output
        assert "export_files" not in output

    def test_command_purges_export_jobs_and_files(self, settings):
        from snapadmin.models import SnapExportJob
        settings.SNAPADMIN_EXPORT_RETENTION_DAYS = 30
        old = self._old_export_job(31)
        output = self._call_command()
        assert not SnapExportJob.objects.filter(pk=old.pk).exists()
        assert "snapadmin.SnapExportJob" in output
        assert "snapadmin.export_files" in output

    def test_command_dry_run_does_not_delete_export_jobs(self, settings):
        from snapadmin.models import SnapExportJob
        settings.SNAPADMIN_EXPORT_RETENTION_DAYS = 30
        old = self._old_export_job(31)
        output = self._call_command("--dry-run")
        assert SnapExportJob.objects.filter(pk=old.pk).exists()
        assert "DRY RUN snapadmin.SnapExportJob" in output
        assert "DRY RUN snapadmin.export_files" in output

    def test_command_reports_export_purge_failure(self, settings, monkeypatch):
        from snapadmin import exporting
        settings.SNAPADMIN_EXPORT_RETENTION_DAYS = 30
        monkeypatch.setattr(
            exporting, "purge_expired_export_jobs",
            lambda now=None, dry_run=False: {
                "enabled": True, "jobs_deleted": {"SnapExportJob": 0},
                "files_deleted": 0, "orphan_files_deleted": 0, "failed": ["boom"],
            },
        )
        output = self._call_command()
        assert "ERROR snapadmin.export_jobs: boom" in output


# ─────────────────────────────────────────────────────────────────────────────
# purge_expired() counts must reflect the target model's own rows, not
# Django's cascade-inflated QuerySet.delete() total (on_delete=CASCADE children)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredCountNotCascadeInflated:
    """Order -> OrderItem is on_delete=CASCADE: purging an old Order also
    deletes its OrderItem rows in the same SQL DELETE. QuerySet.delete()'s
    total would then count both, inflating the reported purge size well past
    what a preceding dry_run=True (a plain qs.count() on Order alone) reports.
    """

    def _old_order_with_items(self, days_old: int, item_count: int = 3):
        from decimal import Decimal
        from demo.apps.shop.models import Customer, Order, OrderItem, Product
        from snapadmin.tenancy import use_all_tenants

        customer = Customer.objects.create(
            first_name="Cascade", last_name="Test", email="cascade@example.com",
            origin="status_a", active=True,
        )
        order = Order.objects.create(customer=customer, total=Decimal("42.00"))
        # Order is tenant-scoped (#FUT1) — .filter().update() goes through
        # the same scoped manager as a read, so it needs a bound context too
        # (there is none here; the order above carries no tenant either).
        with use_all_tenants():
            Order.objects.filter(pk=order.pk).update(
                created_at=timezone.now() - timedelta(days=days_old)
            )
        product = Product.objects.create(name="Cascade Product", price=Decimal("5.00"))
        for _ in range(item_count):
            OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal("5.00"))
        return order

    def test_purge_count_matches_dry_run_despite_cascade(self):
        from demo.apps.shop.models import Order, OrderItem
        from snapadmin.tenancy import use_all_tenants

        with patch.object(Order, "data_retention_days", 30, create=True):
            old_order = self._old_order_with_items(days_old=45, item_count=3)

            dry_run_count = Order.purge_expired(dry_run=True)
            assert dry_run_count == 1

            live_count = Order.purge_expired()

        # The cascade actually happened...
        with use_all_tenants():
            assert not Order.objects.filter(pk=old_order.pk).exists()
        assert OrderItem.objects.filter(order_id=old_order.pk).count() == 0
        # ...but the reported count is the target row count, matching dry_run,
        # not Django's cascade-inflated delete() total (1 order + 3 items = 4).
        assert live_count == 1
        assert live_count == dry_run_count


# ─────────────────────────────────────────────────────────────────────────────
# data_retention_files (#RET2c) — purge_expired() takes storage-backed files
# with it, files before rows, a shared path is never orphaned, and dry_run
# touches nothing. Exercised against Showcase (the demo model with real
# SnapFileField/SnapImageField columns) with its retention window patched to
# a short value for the test, rather than its permanent 10-year demo config.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredFiles:
    def _showcase(self, **kw):
        from demo.apps.shop.models import Showcase
        defaults = {"char_field": "x"}
        defaults.update(kw)
        return Showcase.objects.create(**defaults)

    def _age(self, obj, days_old: int):
        from demo.apps.shop.models import Showcase
        Showcase.objects.filter(pk=obj.pk).update(
            datetime_field=timezone.now() - timedelta(days=days_old)
        )
        obj.refresh_from_db()
        return obj

    def _retention(self, files):
        from demo.apps.shop.models import Showcase
        return (
            patch.object(Showcase, "data_retention_days", 30, create=True),
            patch.object(Showcase, "data_retention_field", "datetime_field", create=True),
            patch.object(Showcase, "data_retention_files", files, create=True),
        )

    def test_deletes_file_before_row(self):
        from django.core.files.base import ContentFile
        from demo.apps.shop.models import Showcase

        obj = self._showcase()
        obj.file_field.save("doc.txt", ContentFile(b"hello"), save=True)
        storage, path = obj.file_field.storage, obj.file_field.name
        self._age(obj, 31)

        p1, p2, p3 = self._retention(["file_field"])
        with p1, p2, p3:
            count = Showcase.purge_expired()

        assert count == 1
        assert not storage.exists(path)
        assert not Showcase.objects.filter(pk=obj.pk).exists()

    def test_no_files_declared_purges_rows_only(self):
        from django.core.files.base import ContentFile
        from demo.apps.shop.models import Showcase

        obj = self._showcase()
        obj.file_field.save("kept.txt", ContentFile(b"hello"), save=True)
        storage, path = obj.file_field.storage, obj.file_field.name
        self._age(obj, 31)

        p1, p2, p3 = self._retention(None)
        with p1, p2, p3:
            Showcase.purge_expired()

        # data_retention_files unset (None) — today's behaviour, unchanged:
        # the row goes, the file is left exactly where it was.
        assert storage.exists(path)

    def test_missing_file_is_not_a_failure(self):
        from demo.apps.shop.models import Showcase

        obj = self._showcase()  # file_field/image_field both blank
        self._age(obj, 31)

        p1, p2, p3 = self._retention(["file_field"])
        with p1, p2, p3:
            count = Showcase.purge_expired()  # must not raise

        assert count == 1
        assert not Showcase.objects.filter(pk=obj.pk).exists()

    def test_storage_error_raises_and_keeps_the_row(self):
        from django.core.files.base import ContentFile
        from demo.apps.shop.models import Showcase
        from snapadmin.models import SnapPurgeError

        obj = self._showcase()
        obj.file_field.save("boom.txt", ContentFile(b"hello"), save=True)
        self._age(obj, 31)

        p1, p2, p3 = self._retention(["file_field"])
        with p1, p2, p3, \
             patch("django.core.files.storage.FileSystemStorage.delete",
                   side_effect=OSError("disk full")):
            with pytest.raises(SnapPurgeError):
                Showcase.purge_expired()

        # Files-before-rows: a file failure must leave the row (and its file
        # name) intact so the purge is retryable, not orphan one from the other.
        assert Showcase.objects.filter(pk=obj.pk).exists()

    def test_dry_run_touches_nothing(self):
        from django.core.files.base import ContentFile
        from demo.apps.shop.models import Showcase

        obj = self._showcase()
        obj.file_field.save("preview.txt", ContentFile(b"hello"), save=True)
        storage, path = obj.file_field.storage, obj.file_field.name
        self._age(obj, 31)

        p1, p2, p3 = self._retention(["file_field"])
        with p1, p2, p3:
            count = Showcase.purge_expired(dry_run=True)

        assert count == 1
        assert storage.exists(path)
        assert Showcase.objects.filter(pk=obj.pk).exists()

    def test_shared_path_within_the_same_purge_batch_is_still_deleted(self):
        """Two rows expiring in the *same* purge that happen to reference the
        same storage path must not skip each other forever — the shared-file
        skip only protects a path a row *outside* this batch still needs.
        """
        from django.core.files.base import ContentFile
        from demo.apps.shop.models import Showcase

        first = self._showcase()
        first.file_field.save("shared.txt", ContentFile(b"hello"), save=True)
        storage, path = first.file_field.storage, first.file_field.name
        second = self._showcase(file_field=path)
        self._age(first, 31)
        self._age(second, 31)

        p1, p2, p3 = self._retention(["file_field"])
        with p1, p2, p3:
            count = Showcase.purge_expired()

        assert count == 2
        assert not storage.exists(path)

    def test_path_still_referenced_by_a_live_row_is_skipped(self):
        """A row outside this purge's window still points at the same path —
        the file must survive even though the expiring row's own copy of the
        row is deleted.
        """
        from django.core.files.base import ContentFile
        from demo.apps.shop.models import Showcase

        old = self._showcase()
        old.file_field.save("shared_live.txt", ContentFile(b"hello"), save=True)
        storage, path = old.file_field.storage, old.file_field.name
        live = self._showcase(file_field=path)  # recent — not in this purge's window
        self._age(old, 31)

        p1, p2, p3 = self._retention(["file_field"])
        with p1, p2, p3:
            count = Showcase.purge_expired()

        assert count == 1
        assert not Showcase.objects.filter(pk=old.pk).exists()
        assert Showcase.objects.filter(pk=live.pk).exists()
        assert storage.exists(path)  # kept — "live" still references it
