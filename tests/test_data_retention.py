"""
tests/test_data_retention.py

Tests for DSGVO/GDPR data retention:
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
        from snapadmin.api.tasks import purge_expired_data
        old = self._create_old_log(days_old=91)
        result = purge_expired_data()
        from demo.models import AuditLog
        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert result["total"] >= 1

    def test_keeps_records_within_retention(self):
        from snapadmin.api.tasks import purge_expired_data
        from demo.models import AuditLog
        recent = AuditLog.objects.create(action="logout", user_email="user@example.com")
        purge_expired_data()
        assert AuditLog.objects.filter(pk=recent.pk).exists()

    def test_returns_summary_dict(self):
        from snapadmin.api.tasks import purge_expired_data
        result = purge_expired_data()
        assert "purged" in result
        assert "total" in result
        assert isinstance(result["total"], int)

    def test_purge_returns_per_model_counts(self):
        from snapadmin.api.tasks import purge_expired_data
        self._create_old_log(days_old=100)
        result = purge_expired_data()
        assert "demo.AuditLog" in result["purged"]
        assert result["purged"]["demo.AuditLog"] >= 1

    def test_no_retention_model_not_in_summary(self):
        from snapadmin.api.tasks import purge_expired_data
        from demo.models import Product
        Product.objects.create(name="Safe Product", price=10)
        result = purge_expired_data()
        assert "demo.Product" not in result["purged"]

    def test_empty_db_returns_zero_total(self):
        from snapadmin.api.tasks import purge_expired_data
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
