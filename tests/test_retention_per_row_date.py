"""
tests/test_retention_per_row_date.py

Per-row retention: ``data_retention_date_field`` (#EXT1o).

``data_retention_days`` is a model-level constant measured off one timestamp
column, which can express "delete 90 days after this row was created" and
nothing else. A row that carries its own expiry — a ``delete_at`` an upstream
supplier sets per record — had no way in at all: the closest approximation was
to point ``data_retention_field`` at the expiry column and accept a whole-day
offset of at least one day (``data_retention_days`` is disabled at ``0``),
which also reads the column as an age rather than a deadline.

``data_retention_date_field`` names that column. The rule pinned here:

* a row whose date is set and in the past is purged;
* a row whose date is set and in the future is kept — **even when it is older
  than ``data_retention_days``**, because an explicit per-row instruction is
  the more specific one;
* a row whose date is ``NULL`` falls back to ``data_retention_days`` measured
  on ``data_retention_field``, and is never purged when that is unset.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone

from snapadmin.registry import get_model_meta


def _aged(obj, model, **columns):
    """Rewrite auto-managed columns on a saved row, bypassing ``auto_now_add``."""
    model.objects.filter(pk=obj.pk).update(**columns)
    obj.refresh_from_db()
    return obj


class TestTheOptionItself:
    def test_default_is_none(self):
        from snapadmin.models import SnapModel
        assert SnapModel.data_retention_date_field is None

    def test_it_is_read_through_get_model_meta(self):
        """One accessor, four tiers — never a hand-rolled getattr (#RFC1b)."""
        from demo.apps.shop.models import AuditLog
        assert get_model_meta(AuditLog, "data_retention_date_field", None) == "delete_at"

    def test_a_registry_entry_overrides_the_class_attribute(self):
        from demo.apps.shop.models import AuditLog
        from snapadmin import registry
        original = registry.meta_for(AuditLog)
        try:
            registry.register(AuditLog, data_retention_date_field="other_at")
            assert get_model_meta(AuditLog, "data_retention_date_field", None) == "other_at"
        finally:
            registry._REGISTRY[AuditLog] = original

    def test_it_is_tracked_as_an_unexposed_snapmodel_attribute(self):
        """Retention needs a shared purge attachment, not just a keyword (#RFC1g)."""
        from snapadmin.models import _SNAP_MODEL_UNEXPOSED_ATTRIBUTES
        assert "data_retention_date_field" in _SNAP_MODEL_UNEXPOSED_ATTRIBUTES


@pytest.mark.django_db
class TestPurgeWithAPerRowDate:
    def _log(self, *, created_days_ago: int, delete_at):
        from demo.apps.shop.models import AuditLog
        obj = AuditLog.objects.create(action="login", user_email="t@example.com")
        return _aged(
            obj,
            AuditLog,
            created_at=timezone.now() - timedelta(days=created_days_ago),
            delete_at=delete_at,
        )

    def test_a_row_past_its_own_date_is_purged(self):
        from demo.apps.shop.models import AuditLog
        doomed = self._log(created_days_ago=1, delete_at=timezone.now() - timedelta(minutes=1))
        assert AuditLog.purge_expired() == 1
        assert not AuditLog.objects.filter(pk=doomed.pk).exists()

    def test_a_row_with_a_future_date_survives_the_model_wide_window(self):
        """The explicit per-row instruction is the more specific rule and wins."""
        from demo.apps.shop.models import AuditLog
        kept = self._log(created_days_ago=500, delete_at=timezone.now() + timedelta(days=1))
        assert AuditLog.purge_expired() == 0
        assert AuditLog.objects.filter(pk=kept.pk).exists()

    def test_a_null_date_falls_back_to_the_day_window(self):
        from demo.apps.shop.models import AuditLog
        old = self._log(created_days_ago=91, delete_at=None)
        fresh = self._log(created_days_ago=1, delete_at=None)
        assert AuditLog.purge_expired() == 1
        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert AuditLog.objects.filter(pk=fresh.pk).exists()

    def test_with_no_day_window_a_null_date_is_never_purged(self):
        from demo.apps.shop.models import AuditLog
        ancient = self._log(created_days_ago=9999, delete_at=None)
        with patch.object(AuditLog, "data_retention_days", None):
            assert AuditLog.purge_expired() == 0
        assert AuditLog.objects.filter(pk=ancient.pk).exists()

    def test_the_date_field_alone_enables_the_purge(self):
        """No ``data_retention_days`` at all is still a configured model."""
        from demo.apps.shop.models import AuditLog
        doomed = self._log(created_days_ago=0, delete_at=timezone.now() - timedelta(minutes=1))
        with patch.object(AuditLog, "data_retention_days", None):
            assert AuditLog.purge_expired() == 1
        assert not AuditLog.objects.filter(pk=doomed.pk).exists()

    def test_neither_configured_returns_zero_and_deletes_nothing(self):
        from demo.apps.shop.models import AuditLog
        kept = self._log(created_days_ago=9999, delete_at=timezone.now() - timedelta(days=1))
        with patch.object(AuditLog, "data_retention_days", None), \
             patch.object(AuditLog, "data_retention_date_field", None):
            assert AuditLog.purge_expired() == 0
        assert AuditLog.objects.filter(pk=kept.pk).exists()

    def test_dry_run_counts_both_rules_without_deleting(self):
        from demo.apps.shop.models import AuditLog
        self._log(created_days_ago=1, delete_at=timezone.now() - timedelta(minutes=1))
        self._log(created_days_ago=91, delete_at=None)
        self._log(created_days_ago=1, delete_at=timezone.now() + timedelta(days=1))
        assert AuditLog.purge_expired(dry_run=True) == 2
        assert AuditLog.objects.count() == 3

    def test_now_is_honoured(self):
        """``now`` moves the per-row deadline too, not only the day window."""
        from demo.apps.shop.models import AuditLog
        later = timezone.now() + timedelta(days=2)
        self._log(created_days_ago=1, delete_at=timezone.now() + timedelta(days=1))
        assert AuditLog.purge_expired(now=later) == 1


@pytest.mark.django_db
class TestTheSchedulersSeeIt:
    """A model configured only with a per-row date must not be skipped."""

    def _doomed(self):
        from demo.apps.shop.models import AuditLog
        obj = AuditLog.objects.create(action="login", user_email="t@example.com")
        return _aged(AuditLog.objects.get(pk=obj.pk), AuditLog,
                     delete_at=timezone.now() - timedelta(minutes=1))

    def test_the_celery_task_purges_it(self):
        from demo.apps.shop.models import AuditLog
        from snapadmin.tasks import purge_expired_data
        doomed = self._doomed()
        with patch.object(AuditLog, "data_retention_days", None):
            result = purge_expired_data()
        assert result["purged"]["demo.AuditLog"] == 1
        assert not AuditLog.objects.filter(pk=doomed.pk).exists()

    def test_the_management_command_purges_it(self):
        from io import StringIO

        from django.core.management import call_command

        from demo.apps.shop.models import AuditLog
        doomed = self._doomed()
        out = StringIO()
        with patch.object(AuditLog, "data_retention_days", None):
            call_command("snapadmin_purge_expired_data", stdout=out)
        assert "demo.AuditLog" in out.getvalue()
        assert not AuditLog.objects.filter(pk=doomed.pk).exists()

    def test_the_command_dry_run_reports_it_without_deleting(self):
        from io import StringIO

        from django.core.management import call_command

        from demo.apps.shop.models import AuditLog
        doomed = self._doomed()
        out = StringIO()
        with patch.object(AuditLog, "data_retention_days", None):
            call_command("snapadmin_purge_expired_data", "--dry-run", stdout=out)
        assert "DRY RUN demo.AuditLog" in out.getvalue()
        assert AuditLog.objects.filter(pk=doomed.pk).exists()


class TestTheStartupCheckCountsIt:
    """``snapadmin.W012`` — retention configured but nothing schedules the purge."""

    def test_a_date_field_only_model_counts_as_configured(self):
        from demo.apps.shop.models import AuditLog, Showcase
        from snapadmin import checks
        from snapadmin.models import SnapadminAuditLog

        with patch.object(AuditLog, "data_retention_days", None), \
             patch.object(Showcase, "data_retention_days", None), \
             patch.object(SnapadminAuditLog, "data_retention_days", staticmethod(lambda: 0)), \
             override_settings(SNAPADMIN_EXPORT_RETENTION_DAYS=None, CELERY_BEAT_SCHEDULE={}):
            found = checks.check_retention_purge_scheduled(None)
        assert [w.id for w in found] == ["snapadmin.W012"]

    def test_nothing_configured_stays_quiet(self):
        from demo.apps.shop.models import AuditLog, Showcase
        from snapadmin import checks
        from snapadmin.models import SnapadminAuditLog

        with patch.object(AuditLog, "data_retention_days", None), \
             patch.object(AuditLog, "data_retention_date_field", None), \
             patch.object(Showcase, "data_retention_days", None), \
             patch.object(SnapadminAuditLog, "data_retention_days", staticmethod(lambda: 0)), \
             override_settings(SNAPADMIN_EXPORT_RETENTION_DAYS=None, CELERY_BEAT_SCHEDULE={}):
            assert checks.check_retention_purge_scheduled(None) == []


class TestEsOnlyPurge:
    """ES_ONLY has no table, so the same rule has to be expressed as a query."""

    def _purge(self, **attrs):
        from demo.apps.shop.models import SearchLog
        mock_es = MagicMock()
        mock_es.delete_by_query.return_value = {"deleted": 2}
        patches = [patch.object(SearchLog, key, value) for key, value in attrs.items()]
        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch.object(SearchLog, "get_es_client", return_value=mock_es):
            for entered in patches:
                entered.__enter__()
            try:
                assert SearchLog.purge_expired() == 2
            finally:
                for entered in reversed(patches):
                    entered.__exit__(None, None, None)
        return mock_es.delete_by_query.call_args.kwargs["body"]["query"]

    def test_a_date_field_alone_is_a_plain_range(self):
        query = self._purge(
            data_retention_days=None,
            data_retention_date_field="expires_at",
        )
        assert list(query["range"]) == ["expires_at"]

    def test_both_rules_become_a_should_with_a_must_not_exists(self):
        query = self._purge(
            data_retention_days=30,
            data_retention_field="timestamp",
            data_retention_date_field="expires_at",
        )
        should = query["bool"]["should"]
        assert query["bool"]["minimum_should_match"] == 1
        assert should[0] == {"range": {"expires_at": should[0]["range"]["expires_at"]}}
        fallback = should[1]["bool"]
        assert fallback["must_not"] == [{"exists": {"field": "expires_at"}}]
        assert list(fallback["filter"][0]["range"]) == ["timestamp"]

    def test_days_alone_is_unchanged(self):
        query = self._purge(
            data_retention_days=30,
            data_retention_field="timestamp",
            data_retention_date_field=None,
        )
        assert list(query["range"]) == ["timestamp"]


class TestTheDemoDogfoodsIt:
    def test_the_demo_audit_log_declares_a_per_row_expiry(self):
        from demo.apps.shop.models import AuditLog
        assert AuditLog.data_retention_date_field == "delete_at"
        assert AuditLog.data_retention_days == 90

    def test_the_demo_column_is_nullable_so_old_rows_keep_the_day_window(self):
        from demo.apps.shop.models import AuditLog
        field = AuditLog._meta.get_field("delete_at")
        assert field.null is True
