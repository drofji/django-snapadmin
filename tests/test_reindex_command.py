"""
Tests for the `snapadmin_reindex` management command (v0.1.0a6).
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command


@pytest.fixture
def stub_reindex(monkeypatch):
    """Record es_reindex_all calls without touching a real ES cluster."""
    calls = []

    def make(result):
        def _reindex(cls, *, chunk_size=500):
            calls.append((cls.__name__, chunk_size))
            return result
        return classmethod(_reindex)

    from demo.models import Product, SearchLog

    monkeypatch.setattr(Product, "es_reindex_all", make({"indexed": 3}))
    monkeypatch.setattr(SearchLog, "es_reindex_all", make({"indexed": 1}))
    return calls


@pytest.mark.django_db
class TestReindexCommand:
    def test_reindex_all_es_models(self, stub_reindex):
        out = StringIO()
        call_command("snapadmin_reindex", stdout=out)
        output = out.getvalue()
        # DUAL (Product) and ES_ONLY (SearchLog) are reindexed; DB_ONLY skipped
        names = {name for name, _ in stub_reindex}
        assert "Product" in names and "SearchLog" in names
        assert "Category" not in names
        assert "indexed" in output

    def test_reindex_single_model(self, stub_reindex):
        out = StringIO()
        call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert stub_reindex == [("Product", 500)]
        assert "demo.Product: 3 indexed" in out.getvalue()

    def test_custom_chunk_size(self, stub_reindex):
        call_command("snapadmin_reindex", "--model", "demo.Product", "--chunk-size", "1000")
        assert stub_reindex == [("Product", 1000)]

    def test_unknown_model_errors(self):
        with pytest.raises(CommandError, match="Unknown model"):
            call_command("snapadmin_reindex", "--model", "demo.Nope")

    def test_non_snapmodel_errors(self):
        with pytest.raises(CommandError, match="not a SnapModel"):
            call_command("snapadmin_reindex", "--model", "auth.User")

    def test_skipped_reported(self, monkeypatch):
        from demo.models import Product

        monkeypatch.setattr(
            Product, "es_reindex_all",
            classmethod(lambda cls, *, chunk_size=500: {"skipped": True, "reason": "Elasticsearch not available"}),
        )
        out = StringIO()
        call_command("snapadmin_reindex", "--model", "demo.Product", stdout=out)
        assert "skipped" in out.getvalue()

    def test_no_es_models_reports_nothing(self, monkeypatch):
        import snapadmin.management.commands.snapadmin_reindex as cmd

        monkeypatch.setattr(cmd, "reindexable_snapmodels", lambda: [])
        out = StringIO()
        call_command("snapadmin_reindex", stdout=out)
        assert "No ES-enabled SnapModels" in out.getvalue()

    def test_errors_raise_commanderror(self, monkeypatch):
        from demo.models import Product

        monkeypatch.setattr(
            Product, "es_reindex_all",
            classmethod(lambda cls, *, chunk_size=500: {"indexed": 1, "errors": ["boom"]}),
        )
        with pytest.raises(CommandError, match="finished with errors"):
            call_command("snapadmin_reindex", "--model", "demo.Product")
