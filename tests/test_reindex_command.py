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
                         "--parallel", "3", "--tune", "--chunk-size", "250")
        kwargs = run.call_args.kwargs
        assert kwargs["parallel"] == 3
        assert kwargs["tune"] is True
        assert kwargs["chunk_size"] == 250

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
class TestEsDisabled:
    def test_skips_when_es_disabled(self, products):
        out = StringIO()
        with override_settings(ELASTICSEARCH_ENABLED=False):
            call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert "skipped" in out.getvalue().lower()
