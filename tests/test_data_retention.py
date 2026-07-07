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
        from demo.models import AuditLog
        assert AuditLog.data_retention_days == 90

    def test_audit_log_retention_field(self):
        from demo.models import AuditLog
        assert AuditLog.data_retention_field == "created_at"


# ─────────────────────────────────────────────────────────────────────────────
# purge_expired_data task
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredDataTask:
    def _create_old_log(self, days_old: int):
        from demo.models import AuditLog
        obj = AuditLog.objects.create(action="login", user_email="test@example.com")
        stale_ts = timezone.now() - timedelta(days=days_old)
        AuditLog.objects.filter(pk=obj.pk).update(created_at=stale_ts)
        return obj

    def test_deletes_records_older_than_retention(self):
        from snapadmin.tasks import purge_expired_data
        old = self._create_old_log(days_old=91)
        result = purge_expired_data()
        from demo.models import AuditLog
        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert result["total"] >= 1

    def test_keeps_records_within_retention(self):
        from snapadmin.tasks import purge_expired_data
        from demo.models import AuditLog
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
        from demo.models import Product
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
        from demo.models import AuditLog
        obj = AuditLog.objects.create(action="cmd_test", user_email="cmd@example.com")
        stale_ts = timezone.now() - timedelta(days=days_old)
        AuditLog.objects.filter(pk=obj.pk).update(created_at=stale_ts)
        return obj

    def _call_command(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("purge_expired_data", *args, stdout=out)
        return out.getvalue()

    def test_command_runs_without_error(self):
        output = self._call_command()
        assert "Total deleted" in output

    def test_dry_run_does_not_delete(self):
        from demo.models import AuditLog
        old = self._create_old_log(days_old=91)
        self._call_command("--dry-run")
        assert AuditLog.objects.filter(pk=old.pk).exists()

    def test_dry_run_output_mentions_dry_run(self):
        self._create_old_log(days_old=91)
        output = self._call_command("--dry-run")
        assert "DRY RUN" in output or "dry run" in output.lower()

    def test_live_run_deletes_old_records(self):
        from demo.models import AuditLog
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
        from demo.models import AuditLog
        obj = AuditLog.objects.create(action="x", user_email="a@b.c")
        AuditLog.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return obj

    def test_deletes_expired(self):
        from demo.models import AuditLog
        old = self._old_log(91)
        assert AuditLog.purge_expired() == 1
        assert not AuditLog.objects.filter(pk=old.pk).exists()

    def test_keeps_recent(self):
        from demo.models import AuditLog
        recent = AuditLog.objects.create(action="y", user_email="r@b.c")
        AuditLog.purge_expired()
        assert AuditLog.objects.filter(pk=recent.pk).exists()

    def test_dry_run_counts_without_deleting(self):
        from demo.models import AuditLog
        old = self._old_log(91)
        assert AuditLog.purge_expired(dry_run=True) == 1
        assert AuditLog.objects.filter(pk=old.pk).exists()

    def test_no_retention_returns_zero(self):
        from demo.models import Category
        assert Category.purge_expired() == 0


@pytest.mark.django_db
class TestPurgeExpiredDual:
    """DUAL models must clear the ES mirror as well as the DB rows.

    Driven by patching AuditLog (which has both ``created_at`` and a retention
    window) into DUAL mode — there is no demo model that is DUAL + retention.
    """

    def _old_log(self, days_old: int):
        from demo.models import AuditLog
        obj = AuditLog.objects.create(action="x", user_email="a@b.c")
        AuditLog.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return obj

    def test_deletes_db_and_es(self):
        from demo.models import AuditLog
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
        mock_es.delete.assert_called_once_with(
            index=AuditLog.get_es_index_name(), id=old.pk, ignore=[404]
        )

    def test_dry_run_skips_db_and_es(self):
        from demo.models import AuditLog
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
        mock_es.delete.assert_not_called()


class TestDeletePksFromEs:
    """_delete_pks_from_es is best-effort and must never raise."""

    def test_no_pks_is_noop(self):
        from demo.models import Product
        from unittest.mock import patch
        with patch.object(Product, "get_es_client") as client:
            Product._delete_pks_from_es([])
            client.assert_not_called()

    def test_es_disabled_is_noop(self):
        from demo.models import Product
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=False), \
             patch.object(Product, "get_es_client") as client:
            Product._delete_pks_from_es([1, 2])
            client.assert_not_called()

    def test_deletes_each_pk(self):
        from demo.models import Product
        from unittest.mock import MagicMock, patch
        from django.test import override_settings
        mock_es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(Product, "get_es_client", return_value=mock_es):
            Product._delete_pks_from_es([7, 8])
        assert mock_es.delete.call_count == 2

    def test_swallows_exceptions(self):
        from demo.models import Product
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(Product, "get_es_client", side_effect=RuntimeError("boom")):
            Product._delete_pks_from_es([1])  # must not raise


class TestPurgeExpiredEsOnly:
    """ES_ONLY models purge via a range delete_by_query against the index."""

    def test_es_disabled_returns_zero(self):
        from demo.models import SearchLog
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=False), \
             patch.object(SearchLog, "data_retention_days", 30):
            assert SearchLog.purge_expired() == 0

    def test_delete_by_query(self):
        from demo.models import SearchLog
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
        from demo.models import SearchLog
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
        from demo.models import SearchLog
        from unittest.mock import patch
        from django.test import override_settings
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(SearchLog, "data_retention_days", 30), \
             patch.object(SearchLog, "get_es_client", side_effect=RuntimeError("boom")):
            assert SearchLog.purge_expired() == 0
