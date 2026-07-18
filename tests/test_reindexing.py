"""
Tests for the resumable, progress-tracking ES reindex job (#FEAT3).

``snapadmin.reindexing.run_reindex_job`` mirrors the async-export job pattern:
a :class:`~snapadmin.models.SnapReindexJob` row tracks progress, resumes from a
``pk__gt`` cursor after a crash, is cancellable, and can relax index settings
(``--tune``) or fan out with ``helpers.parallel_bulk`` (``--parallel``).

ES itself is always mocked here — a MagicMock client plus a patched
``elasticsearch.helpers.bulk`` so no real cluster is touched.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone


@pytest.fixture
def products(db):
    from demo.app.models import Product
    Product.objects.all().delete()
    from decimal import Decimal
    return [Product.objects.create(name=f"P{i}", price=Decimal("1.00")) for i in range(5)]


@pytest.fixture
def es_client():
    """A MagicMock ES client whose index settings round-trip through get/put."""
    es = MagicMock()
    es.indices.get_settings.return_value = {
        "snap_demo_product": {"settings": {"index": {"refresh_interval": "1s", "number_of_replicas": "1"}}}
    }
    return es


def _bulk_ok(es, actions, **kwargs):
    acted = list(actions)
    return (len(acted), [])


# ── model ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSnapReindexJobModel:
    def _job(self, **kw):
        from snapadmin.models import SnapReindexJob
        return SnapReindexJob.objects.create(app_label="demo", model="Product", **kw)

    def test_target_model_resolves(self):
        from demo.app.models import Product
        assert self._job().target_model() is Product

    def test_progress_percent_zero_total(self):
        job = self._job(total_rows=0)
        assert job.progress_percent == 0

    def test_progress_percent_completed_zero_total(self):
        from snapadmin.models import SnapReindexJob
        job = self._job(total_rows=0, status=SnapReindexJob.Status.COMPLETED)
        assert job.progress_percent == 100

    def test_progress_percent_partial(self):
        job = self._job(total_rows=200, processed_rows=50)
        assert job.progress_percent == 25

    def test_is_finished(self):
        from snapadmin.models import SnapReindexJob
        assert not self._job(status=SnapReindexJob.Status.PROCESSING).is_finished
        assert self._job(status=SnapReindexJob.Status.COMPLETED).is_finished
        assert self._job(status=SnapReindexJob.Status.FAILED).is_finished
        assert self._job(status=SnapReindexJob.Status.CANCELLED).is_finished

    def test_eta_none_until_processing(self):
        job = self._job(total_rows=100)
        assert job.eta_seconds is None

    def test_eta_zero_when_completed(self):
        from snapadmin.models import SnapReindexJob
        job = self._job(total_rows=100, status=SnapReindexJob.Status.COMPLETED)
        assert job.eta_seconds == 0

    def test_eta_computed_when_processing(self):
        from snapadmin.models import SnapReindexJob
        job = self._job(
            total_rows=100, processed_rows=50, status=SnapReindexJob.Status.PROCESSING,
            started_at=timezone.now() - timezone.timedelta(seconds=10),
        )
        eta = job.eta_seconds
        assert eta is not None and eta >= 0

    def test_str(self):
        job = self._job(total_rows=10, processed_rows=3)
        assert "demo.Product" in str(job) and "3/10" in str(job)

    def test_eta_none_when_rate_not_positive(self):
        from snapadmin.models import SnapReindexJob
        # started_at in the future → negative elapsed → rate falls to 0 → None.
        job = self._job(
            total_rows=100, processed_rows=5, status=SnapReindexJob.Status.PROCESSING,
            started_at=timezone.now() + timezone.timedelta(seconds=30),
        )
        assert job.eta_seconds is None


# ── index tuner ──────────────────────────────────────────────────────────────

class TestIndexTuner:
    def test_relax_failure_leaves_restore_a_noop(self):
        from snapadmin.reindexing import _IndexTuner
        es = MagicMock()
        es.indices.get_settings.side_effect = Exception("no perms")
        tuner = _IndexTuner(es, "idx")
        tuner.relax()                       # swallowed; nothing captured
        tuner.restore()                     # saved is None → early return
        assert es.indices.put_settings.call_count == 0

    def test_restore_failure_is_swallowed(self):
        from snapadmin.reindexing import _IndexTuner
        es = MagicMock()
        es.indices.get_settings.return_value = {
            "idx": {"settings": {"index": {"refresh_interval": "1s", "number_of_replicas": "1"}}}
        }
        es.indices.put_settings.side_effect = [None, Exception("boom")]
        tuner = _IndexTuner(es, "idx")
        tuner.relax()
        tuner.restore()                     # put_settings raises, must not propagate
        assert es.indices.put_settings.call_count == 2


# ── runner ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRunReindexJob:
    @pytest.fixture(autouse=True)
    def _enable_es(self, settings):
        settings.ELASTICSEARCH_ENABLED = True

    def _make_job(self, **kw):
        from snapadmin.models import SnapReindexJob
        return SnapReindexJob.objects.create(app_label="demo", model="Product", **kw)

    def test_indexes_all_rows(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok) as bulk:
            summary = run_reindex_job(job)
        job.refresh_from_db()
        assert job.status == "completed"
        assert job.processed_rows == 5
        assert job.total_rows == 5
        assert summary["indexed"] == 5
        assert bulk.called

    def test_resume_from_cursor(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        third_pk = str(products[2].pk)
        # Simulate a crash after 3 rows: cursor at row 3, 3 processed.
        job = self._make_job(cursor_pk=third_pk, processed_rows=3, status="failed")
        seen = []

        def rec(es, actions, **kw):
            acted = list(actions)
            seen.extend(a["_id"] for a in acted)
            return (len(acted), [])

        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=rec):
            run_reindex_job(job)
        job.refresh_from_db()
        # Only rows after the cursor are re-indexed; none before it are repeated.
        assert set(seen) == {products[3].pk, products[4].pk}
        assert job.processed_rows == 5
        assert job.status == "completed"

    def test_cancellation_stops_between_chunks(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.models import SnapReindexJob
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()

        def cancel_after_first(es, actions, **kw):
            acted = list(actions)
            SnapReindexJob.objects.filter(pk=job.pk).update(status="cancelled")
            return (len(acted), [])

        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=cancel_after_first):
            summary = run_reindex_job(job, chunk_size=2)
        job.refresh_from_db()
        assert job.status == "cancelled"
        assert summary.get("cancelled") is True
        assert job.processed_rows == 2  # only the first chunk was written

    def test_tune_relaxes_and_restores(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            run_reindex_job(job, tune=True)
        # relaxed once, restored once
        put = es_client.indices.put_settings
        assert put.call_count >= 2
        first = put.call_args_list[0].kwargs["body"]["index"]
        assert first["refresh_interval"] == "-1"
        assert first["number_of_replicas"] == 0
        last = put.call_args_list[-1].kwargs["body"]["index"]
        assert last["refresh_interval"] == "1s"          # restored to the saved value
        assert last["number_of_replicas"] == "1"

    def test_parallel_uses_parallel_bulk(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()

        def fake_parallel(es, actions, **kw):
            for a in actions:
                yield (True, {"index": {"_id": a["_id"]}})

        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.parallel_bulk", side_effect=fake_parallel) as pb, \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok) as b:
            run_reindex_job(job, parallel=4)
        job.refresh_from_db()
        assert pb.called and not b.called
        assert job.processed_rows == 5
        assert pb.call_args.kwargs["thread_count"] == 4

    def test_bulk_errors_recorded_but_job_completes(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()

        def with_errors(es, actions, **kw):
            acted = list(actions)
            return (len(acted) - 1, [{"index": {"error": "boom"}}])

        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=with_errors):
            summary = run_reindex_job(job)
        job.refresh_from_db()
        assert job.status == "completed"
        assert summary["errors"] >= 1
        assert "rejected" in job.error.lower()

    def test_exception_marks_job_failed(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=RuntimeError("es exploded")):
            summary = run_reindex_job(job)
        job.refresh_from_db()
        assert job.status == "failed"
        assert "es exploded" in job.error
        assert summary["errors"]

    def test_progress_callback_invoked(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()
        seen = []
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            run_reindex_job(job, chunk_size=2, on_progress=lambda j: seen.append(j.processed_rows))
        assert seen and seen[-1] == 5

    def test_already_processing_is_skipped(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job(status="processing")
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok) as bulk:
            summary = run_reindex_job(job)
        assert summary.get("skipped") is True
        assert not bulk.called

    def test_es_only_model_single_pass(self, db, es_client):
        from demo.app.models import SearchLog
        from snapadmin.models import SnapReindexJob
        from snapadmin.reindexing import run_reindex_job
        job = SnapReindexJob.objects.create(app_label="demo", model="SearchLog")
        hit = MagicMock()
        hit.pk = 123
        hit.get_es_document.return_value = {"id": 123}
        with patch.object(SearchLog, "objects") as mgr, \
             patch.object(SearchLog, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            from snapadmin.models import EsQuerySet
            eqs = EsQuerySet(SearchLog, hits=[hit])
            mgr.all.return_value = eqs
            summary = run_reindex_job(job, tune=True)  # tune is a no-op for ES_ONLY
        job.refresh_from_db()
        assert job.status == "completed"
        assert job.processed_rows == 1

    def test_parallel_bulk_counts_errors(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        job = self._make_job()

        def fake_parallel(es, actions, **kw):
            acted = list(actions)
            # First document rejected, the rest succeed.
            yield (False, {"index": {"error": "rejected"}})
            for a in acted[1:]:
                yield (True, {"index": {"_id": a["_id"]}})

        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.parallel_bulk", side_effect=fake_parallel):
            summary = run_reindex_job(job, parallel=2)
        assert summary["errors"] == 1

    def test_restart_clean_when_processed_but_no_cursor(self, products, es_client):
        from demo.app.models import Product
        from snapadmin.reindexing import run_reindex_job
        # A stale counter (processed_rows set) with no cursor to resume from must
        # reset to 0 so the fresh pass doesn't double-count.
        job = self._make_job(processed_rows=99, cursor_pk="", status="failed")
        with patch.object(Product, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            run_reindex_job(job)
        job.refresh_from_db()
        assert job.processed_rows == 5   # not 99 + 5

    def _es_only_job(self):
        from snapadmin.models import SnapReindexJob
        return SnapReindexJob.objects.create(app_label="demo", model="SearchLog")

    def _es_only_hits(self, n):
        hits = []
        for i in range(n):
            hit = MagicMock()
            hit.pk = i + 1
            hit.get_es_document.return_value = {"id": i + 1}
            hits.append(hit)
        return hits

    def test_es_only_multi_chunk_with_progress(self, db, es_client):
        from demo.app.models import SearchLog
        from snapadmin.models import EsQuerySet
        from snapadmin.reindexing import run_reindex_job
        job = self._es_only_job()
        seen = []
        with patch.object(SearchLog, "objects") as mgr, \
             patch.object(SearchLog, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            mgr.all.return_value = EsQuerySet(SearchLog, hits=self._es_only_hits(3))
            run_reindex_job(job, chunk_size=1, on_progress=lambda j: seen.append(j.processed_rows))
        job.refresh_from_db()
        assert job.processed_rows == 3
        assert seen[-1] == 3               # progress fired per chunk + on completion

    def test_es_only_rerun_resets_counter(self, db, es_client):
        from demo.app.models import SearchLog
        from snapadmin.models import EsQuerySet, SnapReindexJob
        from snapadmin.reindexing import run_reindex_job
        # A failed ES_ONLY job carrying a stale processed_rows must restart clean
        # (single pass, no resume) — not add the fresh pass on top of the old count.
        job = SnapReindexJob.objects.create(
            app_label="demo", model="SearchLog", status="failed", processed_rows=99,
        )
        with patch.object(SearchLog, "objects") as mgr, \
             patch.object(SearchLog, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=_bulk_ok):
            mgr.all.return_value = EsQuerySet(SearchLog, hits=self._es_only_hits(2))
            run_reindex_job(job)
        job.refresh_from_db()
        assert job.processed_rows == 2   # not 99 + 2

    def test_es_only_cancellation(self, db, es_client):
        from demo.app.models import SearchLog
        from snapadmin.models import EsQuerySet, SnapReindexJob
        from snapadmin.reindexing import run_reindex_job
        job = self._es_only_job()

        def cancel_after_first(es, actions, **kw):
            acted = list(actions)
            SnapReindexJob.objects.filter(pk=job.pk).update(status="cancelled")
            return (len(acted), [])

        with patch.object(SearchLog, "objects") as mgr, \
             patch.object(SearchLog, "get_es_client", return_value=es_client), \
             patch("elasticsearch.helpers.bulk", side_effect=cancel_after_first):
            mgr.all.return_value = EsQuerySet(SearchLog, hits=self._es_only_hits(3))
            summary = run_reindex_job(job, chunk_size=1)
        job.refresh_from_db()
        assert summary.get("cancelled") is True
        assert job.status == "cancelled"
        assert job.processed_rows == 1
