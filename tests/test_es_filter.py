"""
tests/test_es_filter.py  –  structured Elasticsearch term filters

`SnapModel.es_filter(**terms)` runs a structured term/terms query in ES filter
context (no scoring, cacheable), the structured counterpart to the fulltext
`es_search()`. Term keys resolve through the model's effective ES mapping:

* exact types (keyword/boolean/numeric/date/ip) filter directly;
* a `text` field auto-targets its keyword sub-field (`name` → `name.raw`);
* `__` walks `object` `properties` for JSON-mapped key paths;
* an unknown or analysed-text-only field raises `ValueError`.

Scalars build a `term` clause, lists/tuples/sets a `terms` clause. Results
mirror `es_search()` — a pk-ordered DB queryset for DUAL models, an EsQuerySet
for ES_ONLY. When ES is disabled or errors, DUAL models fall back to an
equivalent DB ORM filter (fail-closed to `.none()` on a non-column field);
ES_ONLY models return empty.
"""

import pytest
from unittest.mock import MagicMock, patch

from django.core.exceptions import FieldError
from django.test import override_settings

from demo.app.models import Product, SearchLog


def _es_returning(sources):
    """A mock ES client whose `.search()` returns the given `_source` dicts."""
    es = MagicMock()
    es.search.return_value = {"hits": {"hits": [{"_source": s} for s in sources]}}
    return es


def _body(es):
    return es.search.call_args.kwargs["body"]


# ── query construction (assert the ES request body) ──────────────────────────

@pytest.mark.django_db
class TestEsFilterQueryBody:
    def test_scalar_builds_term_clause(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(available=True)
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"term": {"available": True}} in filters

    def test_list_builds_terms_clause(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(price=[1, 2, 3])
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"terms": {"price": [1, 2, 3]}} in filters

    def test_set_and_tuple_build_terms_clause(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(price=(1, 2))
        filters = _body(es)["query"]["bool"]["filter"]
        assert filters[0]["terms"]["price"] == [1, 2]

    def test_text_field_targets_auto_keyword_subfield(self):
        # SearchLog.query is auto-mapped text with a `.raw` keyword sub-field.
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            SearchLog.es_filter(query="checkout")
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"term": {"query.raw": "checkout"}} in filters

    def test_numeric_and_date_exact_types_filter_directly(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            SearchLog.es_filter(results_count=0)
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"term": {"results_count": 0}} in filters

    def test_query_string_added_as_scored_must(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(query_string="laptop", available=True)
        bool_q = _body(es)["query"]["bool"]
        assert bool_q["filter"] == [{"term": {"available": True}}]
        assert bool_q["must"][0]["multi_match"]["query"] == "laptop"

    def test_no_terms_no_query_is_match_all(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter()
        assert _body(es)["query"] == {"match_all": {}}

    def test_limit_controls_size(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(available=True, limit=7)
        assert _body(es)["size"] == 7

    def test_default_size_from_es_search_limit_setting(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_ES_SEARCH_LIMIT=250), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(available=True)
        assert _body(es)["size"] == 250

    def test_unknown_field_raises_value_error(self):
        with pytest.raises(ValueError, match="nonexistent"):
            Product.es_filter(nonexistent=1)

    def test_analysed_text_without_keyword_subfield_raises(self):
        # Product.name is explicitly mapped {"type": "text", "analyzer": "standard"}
        # with no keyword sub-field — not term-filterable.
        with pytest.raises(ValueError, match="name"):
            Product.es_filter(name="Widget")


# ── JSON / object key-path resolution (the headline use case) ─────────────────

@pytest.mark.django_db
class TestEsFilterJsonPaths:
    """A `__` term key walks an `object` mapping's `properties`, so a JSON
    column mapped in ES can be filtered by nested key path — exactly the case a
    plain database column can't index."""

    _MAPPING = {
        "payload": {
            "type": "object",
            "properties": {
                "status": {"type": "keyword"},
                "meta": {
                    "type": "object",
                    "properties": {"region": {"type": "keyword"}},
                },
            },
        },
    }

    def test_nested_path_resolves_to_dotted_field(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_mapping", return_value=self._MAPPING), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(payload__status="paid")
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"term": {"payload.status": "paid"}} in filters

    def test_deep_nested_path_resolves(self):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_mapping", return_value=self._MAPPING), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_filter(payload__meta__region=["eu", "us"])
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"terms": {"payload.meta.region": ["eu", "us"]}} in filters

    def test_descending_into_container_without_properties_raises(self):
        with patch.object(Product, "get_es_mapping",
                          return_value={"payload": {"type": "object"}}):
            with pytest.raises(ValueError, match="sub-fields"):
                Product.es_filter(payload__status="paid")

    def test_filtering_a_container_directly_raises(self):
        with patch.object(Product, "get_es_mapping", return_value=self._MAPPING):
            with pytest.raises(ValueError, match="not term-filterable"):
                Product.es_filter(payload="whole-object")


# ── result shaping ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsFilterResults:
    def test_dual_returns_db_queryset_in_es_order(self):
        a = Product.objects.create(name="A", price=1, available=True)
        b = Product.objects.create(name="B", price=2, available=True)
        es = _es_returning([{"id": b.pk}, {"id": a.pk}])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            result = Product.es_filter(available=True)
        assert [p.pk for p in result] == [b.pk, a.pk]
        assert result._snap_search_backend == "elasticsearch"

    def test_es_only_returns_reconstructed_objects(self):
        es = _es_returning([{"id": 5, "query": "hi", "results_count": 3}])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            result = SearchLog.es_filter(results_count=3)
        objs = list(result)
        assert len(objs) == 1
        assert objs[0].pk == 5
        assert objs[0].query == "hi"


# ── fallback behaviour ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsFilterFallback:
    def test_dual_falls_back_to_db_when_es_disabled(self):
        Product.objects.create(name="On", price=1, available=True)
        Product.objects.create(name="Off", price=1, available=False)
        # ELASTICSEARCH_ENABLED defaults False in the test settings.
        result = Product.es_filter(available=True)
        names = sorted(p.name for p in result)
        assert names == ["On"]
        assert result._snap_search_backend == "database"

    def test_dual_list_fallback_uses_in_lookup(self):
        Product.objects.create(name="P1", price=1, available=True)
        Product.objects.create(name="P2", price=2, available=True)
        Product.objects.create(name="P3", price=9, available=True)
        result = Product.es_filter(price=[1, 2])
        assert sorted(p.name for p in result) == ["P1", "P2"]

    def test_dual_falls_back_to_db_on_es_error(self):
        Product.objects.create(name="Kept", price=1, available=True)
        es = MagicMock()
        es.search.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            result = Product.es_filter(available=True)
        assert [p.name for p in result] == ["Kept"]
        assert result._snap_search_backend == "database"

    def test_es_only_returns_empty_when_es_disabled(self):
        result = SearchLog.es_filter(results_count=1)
        assert list(result) == []
        assert result._snap_search_backend == "elasticsearch"

    def test_es_only_returns_empty_on_es_error(self):
        es = MagicMock()
        es.search.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            result = SearchLog.es_filter(results_count=1)
        assert list(result) == []

    def test_db_fallback_fails_closed_on_non_column_field(self):
        # A mapped field with no backing DB column can't be translated to an
        # ORM lookup — the fallback must fail closed, not raise or return all.
        Product.objects.create(name="X", price=1, available=True)
        mapping = dict(Product.get_es_mapping())
        mapping["ghost"] = {"type": "keyword"}
        with patch.object(Product, "get_es_mapping", return_value=mapping):
            result = Product.es_filter(ghost="anything")
        assert list(result) == []
        assert result._snap_search_backend == "database"
