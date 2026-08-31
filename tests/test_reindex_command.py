"""
Tests for the `snapadmin_reindex` management command.

The command drives the resumable, progress-tracking reindex job in
:mod:`snapadmin.reindexing` (see ``tests/test_reindexing.py`` for the runner
itself). Here we cover model selection, flag forwarding, progress/summary
output, resume, and the skip/error paths — Elasticsearch is always mocked.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings


@pytest.fixture
def es_client():
    es = MagicMock()
    es.indices.get_settings.return_value = {
        "snap_demo_product": {"settings": {"index": {"refresh_interval": "1s", "number_of_replicas": "1"}}}
    }
    return es


def _bulk_ok(es, actions, **kwargs):
    acted = list(actions)
    return (len(acted), [])


@pytest.fixture
def products(db):
    from decimal import Decimal
    from demo.apps.shop.models import Product
    Product.objects.all().delete()
    return [Product.objects.create(name=f"P{i}", price=Decimal("1.00")) for i in range(5)]


@pytest.mark.django_db
class TestModelSelection:
    def test_reindexes_dual_and_es_only_skips_db_only(self):
        # Patch the runner so no model actually touches ES — we only assert which
        # models were selected for reindexing.
        selected = []

        def fake_run(job, **kwargs):
            selected.append(job.model)
            return {"indexed": 0, "errors": 0}

        with override_settings(ELASTICSEARCH_ENABLED=True), \
             patch("snapadmin.management.commands.snapadmin_reindex.run_reindex_job", side_effect=fake_run):
            call_command("snapadmin_reindex", stdout=StringIO())
        assert "Product" in selected      # DUAL
        assert "SearchLog" in selected    # ES_ONLY
        assert "Category" not in selected  # DB_ONLY

    def test_unknown_model_errors(self):
        with pytest.raises(CommandError, match="Unknown model"):
            call_command("snapadmin_reindex", "--model", "demo.Nope")

    def test_non_snapmodel_errors(self):
        with pytest.raises(CommandError, match="not a SnapModel"):
            call_command("snapadmin_reindex", "--model", "auth.User")

    def test_no_es_models_reports_nothing(self, monkeypatch):
        import snapadmin.management.commands.snapadmin_reindex as cmd
        monkeypatch.setattr(cmd, "reindexable_snapmodels", lambda: [])
        out = StringIO()
        with override_settings(ELASTICSEARCH_ENABLED=True):
            call_command("snapadmin_reindex", stdout=out)
        assert "No ES-enabled SnapModels" in out.getvalue()


@pytest.mark.django_db
class TestRealRun:
    @pytest.fixture(autouse=True)
    def _enable_es(self, settings):
        settings.ELASTICSEARCH_ENABLED = True

    def test_single_model_reports_progress_and_count(self, products, es_client):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        text = out.getvalue()
        assert "demo.Product" in text
        assert "5 indexed" in text

    def test_creates_completed_job_row(self, products, es_client):
        from demo.apps.shop.models import Product
        from snapadmin.models import SnapReindexJob
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            call_command("snapadmin_reindex", "--model", "demo.Product")
        job = SnapReindexJob.objects.filter(app_label="demo", model="Product").latest("created_at")
        assert job.status == "completed" and job.processed_rows == 5

    def test_resume_reuses_failed_job(self, products, es_client):
        from demo.apps.shop.models import Product
        from snapadmin.models import SnapReindexJob
        stale = SnapReindexJob.objects.create(
            app_label="demo", model="Product", status="failed",
            cursor_pk=str(products[1].pk), processed_rows=2,
        )
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            call_command("snapadmin_reindex", "--model", "demo.Product", "--resume")
        stale.refresh_from_db()
        assert stale.status == "completed"
        # The failed job was resumed, not replaced.
        assert SnapReindexJob.objects.filter(app_label="demo", model="Product").count() == 1

    def test_flags_forwarded_to_runner(self, products, es_client):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("snapadmin.management.commands.snapadmin_reindex.run_reindex_job") as run:
            run.return_value = {"indexed": 5, "errors": 0}
            call_command("snapadmin_reindex", "--model", "demo.Product",
                         "--parallel", "3", "--tune", "--chunk-size", "250", "--limit", "10")
        kwargs = run.call_args.kwargs
        assert kwargs["parallel"] == 3
        assert kwargs["tune"] is True
        assert kwargs["chunk_size"] == 250
        assert kwargs["limit"] == 10

    def test_limit_bounds_a_real_run(self, products, es_client):
        from demo.apps.shop.models import Product
        from snapadmin.models import SnapReindexJob
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product", "--limit", "2", stdout=out)
        assert "2 indexed" in out.getvalue()
        job = SnapReindexJob.objects.filter(app_label="demo", model="Product").latest("created_at")
        assert job.processed_rows == 2

    def test_non_positive_limit_errors(self, products):
        with pytest.raises(CommandError, match="limit"):
            call_command("snapadmin_reindex", "--model", "demo.Product", "--limit", "0")

    def test_rejected_documents_reported(self, products, es_client):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("snapadmin.management.commands.snapadmin_reindex.run_reindex_job",
                   return_value={"indexed": 5, "errors": 2}):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert "2 rejected" in out.getvalue()

    def test_cancelled_reported_without_error(self, products, es_client):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("snapadmin.management.commands.snapadmin_reindex.run_reindex_job",
                   return_value={"cancelled": True, "indexed": 2}):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert "cancelled" in out.getvalue().lower()

    def test_skipped_job_reported(self, products, es_client):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("snapadmin.management.commands.snapadmin_reindex.run_reindex_job",
                   return_value={"skipped": True, "reason": "already processing or finished"}):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert "skipped" in out.getvalue().lower()

    def test_failure_raises_commanderror(self, products, es_client):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("snapadmin.management.commands.snapadmin_reindex.run_reindex_job",
                   return_value={"errors": ["boom"], "indexed": 0}):
            with pytest.raises(CommandError, match="finished with errors"):
                call_command("snapadmin_reindex", "--model", "demo.Product")


@pytest.mark.django_db
class TestTuneDefault:
    """`--tune` defaults to SNAPADMIN_REINDEX_TUNE_DEFAULT; --tune/--no-tune override."""

    def _run_and_capture_tune(self, es_client, *args):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("snapadmin.management.commands.snapadmin_reindex.run_reindex_job") as run:
            run.return_value = {"indexed": 5, "errors": 0}
            call_command("snapadmin_reindex", "--model", "demo.Product", *args, stdout=StringIO())
        return run.call_args.kwargs["tune"]

    @override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_REINDEX_TUNE_DEFAULT=True)
    def test_setting_true_makes_tune_default_on(self, products, es_client):
        assert self._run_and_capture_tune(es_client) is True

    @override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_REINDEX_TUNE_DEFAULT=False)
    def test_setting_false_keeps_tune_off_by_default(self, products, es_client):
        assert self._run_and_capture_tune(es_client) is False

    @override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_REINDEX_TUNE_DEFAULT=True)
    def test_no_tune_flag_overrides_a_true_setting(self, products, es_client):
        assert self._run_and_capture_tune(es_client, "--no-tune") is False

    @override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_REINDEX_TUNE_DEFAULT=False)
    def test_tune_flag_overrides_a_false_setting(self, products, es_client):
        assert self._run_and_capture_tune(es_client, "--tune") is True

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_unset_setting_defaults_tune_off(self, products, es_client):
        # No SNAPADMIN_REINDEX_TUNE_DEFAULT configured → today's behaviour (off).
        assert self._run_and_capture_tune(es_client) is False


@pytest.mark.django_db
class TestEsDisabled:
    def test_skips_when_es_disabled(self, products):
        out = StringIO()
        with override_settings(ELASTICSEARCH_ENABLED=False):
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert "skipped" in out.getvalue().lower()


@pytest.mark.django_db
class TestVerifyFlag:
    @pytest.fixture(autouse=True)
    def _enable_es(self, settings):
        settings.ELASTICSEARCH_ENABLED = True

    def test_matching_verify_reports_success(self, products, es_client):
        from demo.apps.shop.models import Product
        es_client.count.return_value = {"count": 5}
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product", "--verify", stdout=out)
        assert "verified" in out.getvalue().lower()

    def test_mismatch_raises_commanderror(self, products, es_client):
        from demo.apps.shop.models import Product
        es_client.count.return_value = {"count": 2}
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            with pytest.raises(CommandError, match="finished with errors"):
                call_command("snapadmin_reindex", "--model", "demo.Product", "--verify", stdout=StringIO())

    def test_mismatch_output_names_the_counts(self, products, es_client):
        from demo.apps.shop.models import Product
        es_client.count.return_value = {"count": 2}
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            out = StringIO()
            with pytest.raises(CommandError):
                call_command("snapadmin_reindex", "--model", "demo.Product", "--verify", stdout=out)
        text = out.getvalue()
        assert "MISMATCH" in text and "2" in text and "5" in text

    def test_es_only_model_reports_skipped_not_applicable(self, db, es_client):
        from demo.apps.shop.models import SearchLog
        from snapadmin.models import EsQuerySet
        hit = MagicMock()
        hit.pk = 1
        hit.get_es_document.return_value = {"id": 1}
        with patch.object(SearchLog, "objects") as mgr, \
             patch.object(SearchLog, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            mgr.all.return_value = EsQuerySet(SearchLog, hits=[hit])
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.SearchLog", "--verify", stdout=out)
        assert "verify skipped" in out.getvalue().lower()

    def test_without_the_flag_no_verification_runs(self, products, es_client):
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert es_client.count.call_count == 0
        assert "verified" not in out.getvalue().lower()

    def test_verify_count_failure_raises_commanderror(self, products, es_client):
        from demo.apps.shop.models import Product
        es_client.count.side_effect = Exception("cluster unreachable")
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            out = StringIO()
            with pytest.raises(CommandError):
                call_command("snapadmin_reindex", "--model", "demo.Product", "--verify", stdout=out)
        assert "cluster unreachable" in out.getvalue()


class TestThrottledProgress:
    """Unit tests for the command's progress-line rate limiter, with a fake clock."""

    def _job(self, *, finished=False):
        job = MagicMock()
        job.is_finished = finished
        return job

    def test_first_call_always_emits(self):
        from snapadmin.management.commands.snapadmin_reindex import _ThrottledProgress
        seen = []
        throttled = _ThrottledProgress(seen.append, interval=10, clock=lambda: 0.0)
        throttled(self._job())
        assert len(seen) == 1

    def test_calls_within_the_interval_are_dropped(self):
        from snapadmin.management.commands.snapadmin_reindex import _ThrottledProgress
        seen = []
        clock = iter([0.0, 1.0, 2.0, 3.0]).__next__
        throttled = _ThrottledProgress(seen.append, interval=10, clock=clock)
        for _ in range(4):
            throttled(self._job())
        assert len(seen) == 1  # only the first call, at t=0

    def test_calls_past_the_interval_emit_again(self):
        from snapadmin.management.commands.snapadmin_reindex import _ThrottledProgress
        seen = []
        clock = iter([0.0, 4.0, 11.0, 12.0, 25.0]).__next__
        throttled = _ThrottledProgress(seen.append, interval=10, clock=clock)
        for _ in range(5):
            throttled(self._job())
        # t=0 (first), t=4 (dropped), t=11 (>=10 since last emit at 0), t=12 (dropped), t=25 (>=10 since 11)
        assert len(seen) == 3

    def test_finished_job_always_emits_even_within_the_interval(self):
        from snapadmin.management.commands.snapadmin_reindex import _ThrottledProgress
        seen = []
        clock = iter([0.0, 1.0]).__next__
        throttled = _ThrottledProgress(seen.append, interval=1000, clock=clock)
        throttled(self._job(finished=False))
        throttled(self._job(finished=True))
        assert len(seen) == 2  # the huge interval would otherwise have dropped the second call


@pytest.mark.django_db
class TestProgressIntervalFlag:
    @pytest.fixture(autouse=True)
    def _enable_es(self, settings):
        settings.ELASTICSEARCH_ENABLED = True

    def test_flag_is_forwarded_to_the_throttle(self, products, es_client):
        from demo.apps.shop.models import Product
        import snapadmin.management.commands.snapadmin_reindex as cmd
        captured = {}
        orig_cls = cmd._ThrottledProgress

        def spy(emit, *, interval, clock=None):
            captured["interval"] = interval
            return orig_cls(emit, interval=interval)

        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok), \
             patch.object(cmd, "_ThrottledProgress", side_effect=spy):
            call_command("snapadmin_reindex", "--model", "demo.Product",
                         "--progress-interval", "42", stdout=StringIO())
        assert captured["interval"] == 42.0

    def test_default_interval_is_the_module_default(self, products, es_client):
        from demo.apps.shop.models import Product
        import snapadmin.management.commands.snapadmin_reindex as cmd
        captured = {}
        orig_cls = cmd._ThrottledProgress

        def spy(emit, *, interval, clock=None):
            captured["interval"] = interval
            return orig_cls(emit, interval=interval)

        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok), \
             patch.object(cmd, "_ThrottledProgress", side_effect=spy):
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=StringIO())
        assert captured["interval"] == cmd.DEFAULT_PROGRESS_INTERVAL

    def test_final_progress_line_survives_a_huge_interval(self, products, es_client):
        # A huge --progress-interval must still show the run finished — it must
        # never look like a hang because a live-progress line never printed.
        from demo.apps.shop.models import Product
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            out = StringIO()
            call_command("snapadmin_reindex", "--model", "demo.Product",
                         "--chunk-size", "1", "--progress-interval", "99999", stdout=out)
        text = out.getvalue()
        assert "5/5" in text and "100%" in text
