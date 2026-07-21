"""
tests/test_es_count.py  –  true match count of a structured Elasticsearch query

`SnapModel.es_count(*, query_string=None, **terms) -> int` is the counting
counterpart to `es_filter()`: the same term resolution (`_resolve_es_term_field`)
and the same `_build_es_term_query` body, but it hits the Elasticsearch `_count`
API instead of `_search`, so it returns the *true* number of matches regardless
of `SNAPADMIN_ES_SEARCH_LIMIT` / `index.max_result_window` (which cap what
`es_filter`/`es_search` can ever return).

Results fail safe exactly like its siblings: when ES is disabled or errors, a
DUAL model falls back to the equivalent database `count()` (failing closed to
`0` for a term field with no backing column); an ES_ONLY model (no table)
returns `0`.
"""

import pytest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from demo.apps.shop.models import Product, SearchLog


def _es_counting(count):
    """A mock ES client whose `.count()` returns the given match ``count``."""
    es = MagicMock()
    es.count.return_value = {"count": count}
    return es


def _body(es):
    return es.count.call_args.kwargs["body"]


# ── query construction (assert the ES request body) ──────────────────────────

@pytest.mark.django_db
class TestEsCountQueryBody:
    def test_scalar_term_builds_term_clause(self):
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count(available=True)
        assert _body(es)["query"]["bool"]["filter"] == [{"term": {"available": True}}]

    def test_list_term_builds_terms_clause(self):
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count(price=[999, 1299])
        assert _body(es)["query"]["bool"]["filter"] == [{"terms": {"price": [999, 1299]}}]

    def test_no_terms_uses_match_all_query(self):
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count()
        assert _body(es)["query"] == {"match_all": {}}

    def test_query_string_added_as_scored_must(self):
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count(query_string="laptop", available=True)
        bool_q = _body(es)["query"]["bool"]
        assert bool_q["filter"] == [{"term": {"available": True}}]
        assert bool_q["must"][0]["multi_match"]["query"] == "laptop"

    def test_count_body_carries_no_size(self):
        # The `_count` API takes only a query — never a hit `size`.
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count(available=True)
        assert "size" not in _body(es)

    def test_uses_count_api_not_search(self):
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count(available=True)
        es.count.assert_called_once()
        es.search.assert_not_called()

    def test_text_field_counts_on_keyword_subfield(self):
        # SearchLog.query is auto-mapped text with a `.raw` keyword sub-field.
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            SearchLog.es_count(query="laptop")
        assert _body(es)["query"]["bool"]["filter"] == [{"term": {"query.raw": "laptop"}}]

    def test_json_path_counts_on_dotted_field(self):
        mapping = {"payload": {"type": "object",
                               "properties": {"status": {"type": "keyword"}}}}
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_mapping", return_value=mapping), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count(payload__status="paid")
        assert _body(es)["query"]["bool"]["filter"] == [{"term": {"payload.status": "paid"}}]

    def test_targets_the_model_index(self):
        es = _es_counting(0)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.es_count(available=True)
        assert es.count.call_args.kwargs["index"] == Product.get_es_index_name()


# ── result ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsCountResult:
    def test_returns_the_es_match_count(self):
        es = _es_counting(4217)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            assert Product.es_count(available=True) == 4217

    def test_counts_past_the_search_limit(self):
        # es_filter caps at SNAPADMIN_ES_SEARCH_LIMIT; es_count sees the true
        # total even when it dwarfs the limit.
        es = _es_counting(1_000_000)
        with override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_ES_SEARCH_LIMIT=1000), \
                patch.object(Product, "get_es_client", return_value=es):
            assert Product.es_count() == 1_000_000

    def test_missing_count_key_defaults_to_zero(self):
        es = MagicMock()
        es.count.return_value = {}
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            assert Product.es_count(available=True) == 0

    def test_result_is_an_int(self):
        es = _es_counting(7)
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            result = Product.es_count(available=True)
        assert isinstance(result, int)


# ── validation ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsCountValidation:
    def test_unknown_field_raises(self):
        with pytest.raises(ValueError, match="nonexistent"):
            Product.es_count(nonexistent=1)

    def test_analysed_text_field_raises(self):
        # Product.name is mapped as analysed text with no keyword sub-field.
        with pytest.raises(ValueError, match="name"):
            Product.es_count(name="laptop")


# ── fallback behaviour ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsCountFallback:
    def test_dual_falls_back_to_db_count_when_es_disabled(self):
        Product.objects.create(name="A", price=1, available=True)
        Product.objects.create(name="B", price=2, available=True)
        Product.objects.create(name="C", price=3, available=False)
        # ELASTICSEARCH_ENABLED defaults False in the test settings.
        assert Product.es_count(available=True) == 2

    def test_dual_db_fallback_counts_all_with_no_terms(self):
        for n in range(3):
            Product.objects.create(name=f"p{n}", price=1, available=True)
        assert Product.es_count() == 3

    def test_dual_db_fallback_honours_list_term(self):
        Product.objects.create(name="a", price=1, available=True)
        Product.objects.create(name="b", price=2, available=True)
        Product.objects.create(name="c", price=3, available=True)
        assert Product.es_count(price=[1, 3]) == 2

    def test_dual_falls_back_to_db_count_on_es_error(self):
        Product.objects.create(name="Kept", price=1, available=True)
        es = MagicMock()
        es.count.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            assert Product.es_count(available=True) == 1

    def test_db_fallback_fails_closed_on_non_column_field(self):
        # A mapped field with no backing DB column can't be counted in the DB
        # fallback — it must fail closed to 0, not raise.
        Product.objects.create(name="X", price=1, available=True)
        mapping = dict(Product.get_es_mapping())
        mapping["ghost"] = {"type": "keyword"}
        with patch.object(Product, "get_es_mapping", return_value=mapping):
            assert Product.es_count(ghost="anything") == 0

    def test_es_only_returns_zero_when_es_disabled(self):
        assert SearchLog.es_count(results_count=0) == 0

    def test_es_only_returns_zero_on_es_error(self):
        es = MagicMock()
        es.count.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            assert SearchLog.es_count(results_count=0) == 0
