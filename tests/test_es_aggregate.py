"""
tests/test_es_aggregate.py  –  Elasticsearch `terms` aggregations (facets)

`SnapModel.es_aggregate(*fields, **terms)` runs one ES `terms` aggregation per
requested field and returns a plain bucket dict::

    {"status": [{"key": "paid", "count": 12}, {"key": "open", "count": 3}], ...}

It is the faceting counterpart to `es_filter()`: aggregation fields resolve
through the same `_resolve_es_term_field` machinery (a `text` field targets its
keyword sub-field, a `__` path walks an `object`/JSON mapping, an analysed-only
or unknown field raises `ValueError`), and optional `**terms` narrow the
document set in ES *filter* context before the buckets are counted.

Results fail safe: when ES is disabled or errors, a DUAL model recomputes the
same facets over the database with `values(field).annotate(Count)`; an ES_ONLY
model (no table) returns empty buckets for every requested field.
"""

import pytest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from demo.app.models import Product, SearchLog


def _es_returning(aggregations):
    """A mock ES client whose `.search()` returns the given `aggregations` dict."""
    es = MagicMock()
    es.search.return_value = {"aggregations": aggregations}
    return es


def _body(es):
    return es.search.call_args.kwargs["body"]


# ── query construction (assert the ES request body) ──────────────────────────

@pytest.mark.django_db
class TestEsAggregateQueryBody:
    def test_single_field_builds_terms_aggregation(self):
        es = _es_returning({"available": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("available")
        body = _body(es)
        assert body["aggs"] == {"available": {"terms": {"field": "available", "size": 10}}}

    def test_size_zero_hits_requested(self):
        # An aggregation-only query never needs the hit documents themselves.
        es = _es_returning({"available": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("available")
        assert _body(es)["size"] == 0

    def test_multiple_fields_build_multiple_aggregations(self):
        es = _es_returning({"available": {"buckets": []}, "price": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("available", "price")
        aggs = _body(es)["aggs"]
        assert set(aggs) == {"available", "price"}

    def test_size_controls_bucket_count(self):
        es = _es_returning({"available": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("available", size=50)
        assert _body(es)["aggs"]["available"]["terms"]["size"] == 50

    def test_no_terms_uses_match_all_query(self):
        es = _es_returning({"available": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("available")
        assert _body(es)["query"] == {"match_all": {}}

    def test_terms_narrow_the_aggregation_in_filter_context(self):
        es = _es_returning({"price": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("price", available=True)
        query = _body(es)["query"]
        assert query["bool"]["filter"] == [{"term": {"available": True}}]

    def test_query_string_added_as_scored_must(self):
        es = _es_returning({"price": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("price", query_string="laptop", available=True)
        bool_q = _body(es)["query"]["bool"]
        assert bool_q["filter"] == [{"term": {"available": True}}]
        assert bool_q["must"][0]["multi_match"]["query"] == "laptop"

    def test_text_field_aggregates_on_keyword_subfield(self):
        # SearchLog.query is auto-mapped text with a `.raw` keyword sub-field.
        es = _es_returning({"query": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            SearchLog.es_aggregate("query")
        assert _body(es)["aggs"]["query"]["terms"]["field"] == "query.raw"

    def test_json_path_aggregates_on_dotted_field(self):
        mapping = {"payload": {"type": "object",
                               "properties": {"status": {"type": "keyword"}}}}
        es = _es_returning({"payload__status": {"buckets": []}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_mapping", return_value=mapping), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_aggregate("payload__status")
        assert _body(es)["aggs"]["payload__status"]["terms"]["field"] == "payload.status"


# ── validation ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsAggregateValidation:
    def test_no_fields_raises(self):
        with pytest.raises(ValueError, match="at least one field"):
            Product.es_aggregate()

    def test_unknown_field_raises(self):
        with pytest.raises(ValueError, match="nonexistent"):
            Product.es_aggregate("nonexistent")

    def test_analysed_text_field_raises(self):
        # Product.name is mapped as analysed text with no keyword sub-field.
        with pytest.raises(ValueError, match="name"):
            Product.es_aggregate("name")

    def test_unknown_filter_term_raises(self):
        with pytest.raises(ValueError, match="ghost"):
            Product.es_aggregate("available", ghost=1)

    def test_non_positive_size_raises(self):
        with pytest.raises(ValueError, match="size"):
            Product.es_aggregate("available", size=0)


# ── result shaping ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsAggregateResults:
    def test_buckets_map_to_key_count_dicts(self):
        es = _es_returning({"available": {"buckets": [
            {"key": 1, "key_as_string": "true", "doc_count": 8},
            {"key": 0, "key_as_string": "false", "doc_count": 2},
        ]}})
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            result = Product.es_aggregate("available")
        assert result == {"available": [
            {"key": 1, "count": 8},
            {"key": 0, "count": 2},
        ]}

    def test_result_has_an_entry_per_requested_field(self):
        es = _es_returning({
            "available": {"buckets": [{"key": 1, "doc_count": 3}]},
            "price": {"buckets": []},
        })
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            result = Product.es_aggregate("available", "price")
        assert set(result) == {"available", "price"}
        assert result["price"] == []


# ── fallback behaviour ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsAggregateFallback:
    def test_dual_falls_back_to_db_facets_when_es_disabled(self):
        Product.objects.create(name="A", price=1, available=True)
        Product.objects.create(name="B", price=2, available=True)
        Product.objects.create(name="C", price=3, available=False)
        # ELASTICSEARCH_ENABLED defaults False in the test settings.
        result = Product.es_aggregate("available")
        buckets = {b["key"]: b["count"] for b in result["available"]}
        assert buckets == {True: 2, False: 1}

    def test_db_fallback_buckets_ordered_by_count_desc(self):
        for _ in range(3):
            Product.objects.create(name="on", price=1, available=True)
        Product.objects.create(name="off", price=1, available=False)
        result = Product.es_aggregate("available")
        counts = [b["count"] for b in result["available"]]
        assert counts == sorted(counts, reverse=True)

    def test_db_fallback_honours_filter_terms(self):
        Product.objects.create(name="cheap-on", price=1, available=True)
        Product.objects.create(name="dear-on", price=9, available=True)
        Product.objects.create(name="cheap-off", price=1, available=False)
        result = Product.es_aggregate("price", available=True)
        buckets = {float(b["key"]): b["count"] for b in result["price"]}
        assert buckets == {1.0: 1, 9.0: 1}

    def test_db_fallback_respects_size(self):
        for price in (1, 2, 3, 4):
            Product.objects.create(name=f"p{price}", price=price, available=True)
        result = Product.es_aggregate("price", size=2)
        assert len(result["price"]) == 2

    def test_dual_falls_back_to_db_on_es_error(self):
        Product.objects.create(name="Kept", price=1, available=True)
        es = MagicMock()
        es.search.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            result = Product.es_aggregate("available")
        assert result["available"] == [{"key": True, "count": 1}]

    def test_db_fallback_fails_closed_on_non_column_field(self):
        # A mapped field with no backing DB column can't be aggregated in the
        # DB fallback — it must fail closed to empty, not raise.
        Product.objects.create(name="X", price=1, available=True)
        mapping = dict(Product.get_es_mapping())
        mapping["ghost"] = {"type": "keyword"}
        with patch.object(Product, "get_es_mapping", return_value=mapping):
            result = Product.es_aggregate("ghost")
        assert result == {"ghost": []}

    def test_db_fallback_fails_closed_when_filter_term_has_no_column(self):
        Product.objects.create(name="X", price=1, available=True)
        mapping = dict(Product.get_es_mapping())
        mapping["ghost"] = {"type": "keyword"}
        with patch.object(Product, "get_es_mapping", return_value=mapping):
            result = Product.es_aggregate("available", ghost="anything")
        assert result == {"available": []}

    def test_es_only_returns_empty_buckets_when_es_disabled(self):
        result = SearchLog.es_aggregate("results_count")
        assert result == {"results_count": []}

    def test_es_only_returns_empty_buckets_on_es_error(self):
        es = MagicMock()
        es.search.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            result = SearchLog.es_aggregate("results_count")
        assert result == {"results_count": []}
