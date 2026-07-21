"""
tests/test_es_db_fallback.py  –  opt out of the silent ES→DB fallback

By default the structured ES query methods (`es_filter`, `es_aggregate`,
`es_count`, `es_scan`) fall back to the database when Elasticsearch is disabled
or a query errors. On a large, DB-unindexable table that silent fallback can be
*worse* than a clear failure (a full-table `GROUP BY`, an unbounded
`.iterator()`), so a caller can opt out:

    Model.es_filter(..., db_fallback=False)   # raise instead of a DB scan

With `db_fallback=False` a DUAL model raises `SnapEsUnavailable` when the ES path
can't answer, rather than running the ORM equivalent. The project-wide default is
`SNAPADMIN_ES_DB_FALLBACK` (default True → today's behaviour); a per-call value
overrides it. ES_ONLY models are unaffected (there is no DB to fall back to), and
DB_ONLY models never raise (the database is their primary store, not a fallback).
"""

import pytest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from demo.apps.shop.models import Product, SearchLog
from snapadmin.models import SnapEsUnavailable


def _es_raising():
    """A mock ES client whose every query call raises."""
    es = MagicMock()
    boom = RuntimeError("es down")
    es.search.side_effect = boom
    es.count.side_effect = boom
    return es, boom


# ── the exception is importable & public ─────────────────────────────────────

def test_exception_is_a_plain_exception_subclass():
    assert issubclass(SnapEsUnavailable, Exception)


# ── db_fallback=False raises when ES is DISABLED (DUAL model) ─────────────────

@pytest.mark.django_db
class TestFailFastWhenEsDisabled:
    def test_es_filter_raises(self):
        Product.objects.create(name="A", price=1, available=True)
        # ELASTICSEARCH_ENABLED defaults False in the test settings.
        with pytest.raises(SnapEsUnavailable, match="es_filter"):
            Product.es_filter(available=True, db_fallback=False)

    def test_es_aggregate_raises(self):
        with pytest.raises(SnapEsUnavailable, match="es_aggregate"):
            Product.es_aggregate("available", db_fallback=False)

    def test_es_count_raises(self):
        with pytest.raises(SnapEsUnavailable, match="es_count"):
            Product.es_count(available=True, db_fallback=False)

    def test_es_scan_raises_on_iteration(self):
        # es_scan is lazy — the raise surfaces when the generator is consumed.
        gen = Product.es_scan(available=True, db_fallback=False)
        with pytest.raises(SnapEsUnavailable, match="es_scan"):
            list(gen)


# ── db_fallback=False raises when the ES query ERRORS (DUAL model) ────────────

@pytest.mark.django_db
class TestFailFastWhenEsErrors:
    def test_es_filter_raises_chained_from_es_error(self):
        es, boom = _es_raising()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            with pytest.raises(SnapEsUnavailable) as exc_info:
                Product.es_filter(available=True, db_fallback=False)
        assert exc_info.value.__cause__ is boom

    def test_es_aggregate_raises_chained_from_es_error(self):
        es, boom = _es_raising()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            with pytest.raises(SnapEsUnavailable) as exc_info:
                Product.es_aggregate("available", db_fallback=False)
        assert exc_info.value.__cause__ is boom

    def test_es_count_raises_chained_from_es_error(self):
        es, boom = _es_raising()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            with pytest.raises(SnapEsUnavailable) as exc_info:
                Product.es_count(available=True, db_fallback=False)
        assert exc_info.value.__cause__ is boom

    def test_es_scan_raises_when_es_errors_before_streaming(self):
        es, _ = _es_raising()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            with pytest.raises(SnapEsUnavailable, match="es_scan"):
                list(Product.es_scan(available=True, db_fallback=False))


# ── explicit db_fallback=True keeps the DB fallback (overrides a fail-fast default) ──

@pytest.mark.django_db
class TestExplicitFallbackTrue:
    def test_es_filter_falls_back_to_db(self):
        Product.objects.create(name="A", price=1, available=True)
        result = Product.es_filter(available=True, db_fallback=True)
        assert [p.name for p in result] == ["A"]

    def test_es_count_falls_back_to_db(self):
        Product.objects.create(name="A", price=1, available=True)
        assert Product.es_count(available=True, db_fallback=True) == 1


# ── default behaviour is UNCHANGED (no arg → silent DB fallback) ──────────────

@pytest.mark.django_db
class TestDefaultUnchanged:
    def test_es_filter_default_still_falls_back(self):
        Product.objects.create(name="A", price=1, available=True)
        result = Product.es_filter(available=True)
        assert [p.name for p in result] == ["A"]

    def test_es_aggregate_default_still_falls_back(self):
        Product.objects.create(name="A", price=1, available=True)
        result = Product.es_aggregate("available")
        assert result["available"] == [{"key": True, "count": 1}]

    def test_es_count_default_still_falls_back(self):
        Product.objects.create(name="A", price=1, available=True)
        assert Product.es_count(available=True) == 1

    def test_es_scan_default_still_falls_back(self):
        Product.objects.create(name="A", price=1, available=True)
        assert [p.name for p in Product.es_scan(available=True)] == ["A"]


# ── SNAPADMIN_ES_DB_FALLBACK sets the project-wide default ────────────────────

@pytest.mark.django_db
class TestProjectWideDefault:
    def test_setting_false_makes_es_filter_fail_fast(self):
        with override_settings(SNAPADMIN_ES_DB_FALLBACK=False):
            with pytest.raises(SnapEsUnavailable):
                Product.es_filter(available=True)

    def test_setting_false_makes_es_count_fail_fast(self):
        with override_settings(SNAPADMIN_ES_DB_FALLBACK=False):
            with pytest.raises(SnapEsUnavailable):
                Product.es_count(available=True)

    def test_per_call_true_overrides_setting_false(self):
        Product.objects.create(name="A", price=1, available=True)
        with override_settings(SNAPADMIN_ES_DB_FALLBACK=False):
            # An explicit db_fallback=True re-enables the DB fallback for this call.
            assert Product.es_count(available=True, db_fallback=True) == 1

    def test_setting_true_is_the_default(self):
        Product.objects.create(name="A", price=1, available=True)
        with override_settings(SNAPADMIN_ES_DB_FALLBACK=True):
            assert Product.es_count(available=True) == 1


# ── ES_ONLY models are UNAFFECTED (no DB to fall back to) ─────────────────────

@pytest.mark.django_db
class TestEsOnlyUnaffected:
    def test_es_only_returns_empty_not_raise_when_disabled(self):
        # No table → nothing to fall back to → still an empty result, never a raise.
        assert SearchLog.es_count(results_count=0, db_fallback=False) == 0

    def test_es_only_filter_returns_empty_not_raise_on_error(self):
        es, _ = _es_raising()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            result = SearchLog.es_filter(results_count=0, db_fallback=False)
        assert list(result) == []

    def test_es_only_scan_yields_nothing_not_raise(self):
        with override_settings(SNAPADMIN_ES_DB_FALLBACK=False):
            assert list(SearchLog.es_scan(results_count=0)) == []


# ── es_scan mid-stream failure still stops (does not raise) ───────────────────

@pytest.mark.django_db
class TestScanMidStreamStop:
    def test_mid_stream_es_failure_stops_without_raising(self):
        # Once a document has been streamed the search_after cursor is gone, so a
        # mid-stream failure stops where it was — db_fallback=False does not turn
        # that into a raise (there is no DB scan being suppressed there).
        Product.objects.create(name="A", price=1, available=True)
        es = MagicMock()
        page = {"hits": {"hits": [{"_source": {"id": 1}, "sort": [1]}]}}
        es.search.side_effect = [page, RuntimeError("es died mid-scan")]
        collected = []
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            for obj in Product.es_scan(available=True, db_fallback=False, page_size=1):
                collected.append(obj.pk)
        assert collected == [1]  # streamed the first page, then stopped — no raise
