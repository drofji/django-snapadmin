"""
tests/test_es_scan.py  –  `search_after` deep-scan iterator

`SnapModel.es_scan(**terms)` yields every matching document without deep-`from`
paging, so it can walk result sets larger than ES's `index.max_result_window`
(10k). It pages through the index with `search_after` over a stable `id` sort,
one `page_size`-sized request per round-trip, and reuses the same field
resolution / filter-context machinery as `es_filter()`.

Like the other ES query methods it fails safe: a DUAL model with ES disabled
(or ES unreachable before it streams anything) walks the equivalent DB filter
with `.iterator()` instead; an ES_ONLY model yields nothing when ES is down.
"""

import pytest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from demo.app.models import Product, SearchLog


def _es_pages(*pages):
    """A mock ES client whose successive `.search()` calls return `pages`.

    Each page is a list of `_source` dicts; the mock attaches a `sort` cursor
    (`[id]`) to every hit, mirroring what ES returns when a `sort` is requested.
    A trailing empty page is appended so the scan loop terminates.
    """
    es = MagicMock()
    responses = []
    for sources in list(pages) + [[]]:
        hits = [{"_source": s, "sort": [s["id"]]} for s in sources]
        responses.append({"hits": {"hits": hits}})
    es.search.side_effect = responses
    return es


def _bodies(es):
    return [c.kwargs["body"] for c in es.search.call_args_list]


# ── cursor paging (assert the search_after loop) ─────────────────────────────

@pytest.mark.django_db
class TestEsScanCursor:
    def test_sorts_by_id_for_a_stable_cursor(self):
        es = _es_pages([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            list(SearchLog.es_scan())
        assert _bodies(es)[0]["sort"] == [{"id": "asc"}]

    def test_first_request_has_no_search_after(self):
        es = _es_pages([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            list(SearchLog.es_scan())
        assert "search_after" not in _bodies(es)[0]

    def test_advances_cursor_from_last_hit_sort(self):
        es = _es_pages(
            [{"id": 1, "query": "a", "results_count": 0},
             {"id": 2, "query": "b", "results_count": 0}],
        )
        with override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_ES_SEARCH_LIMIT=2), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            list(SearchLog.es_scan())
        bodies = _bodies(es)
        # Second request resumes after the last hit's sort value (id=2).
        assert bodies[1]["search_after"] == [2]

    def test_walks_multiple_full_pages_until_short_page(self):
        es = _es_pages(
            [{"id": 1, "query": "a", "results_count": 0},
             {"id": 2, "query": "b", "results_count": 0}],
            [{"id": 3, "query": "c", "results_count": 0}],  # short page → stop
        )
        with override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_ES_SEARCH_LIMIT=2), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            ids = [o.pk for o in SearchLog.es_scan()]
        assert ids == [1, 2, 3]
        # A full page (==page_size) forces another request; the short one stops it.
        assert es.search.call_count == 2

    def test_page_size_controls_request_size(self):
        es = _es_pages([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            list(SearchLog.es_scan(page_size=5000))
        assert _bodies(es)[0]["size"] == 5000

    def test_terms_and_query_string_shape_the_query(self):
        es = _es_pages([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            list(Product.es_scan(query_string="laptop", available=True))
        bool_q = _bodies(es)[0]["query"]["bool"]
        assert bool_q["filter"] == [{"term": {"available": True}}]
        assert bool_q["must"][0]["multi_match"]["query"] == "laptop"


# ── validation ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsScanValidation:
    def test_unknown_field_raises_eagerly(self):
        # Validation must happen when es_scan() is called, not deferred to the
        # first `next()` — a generator that hides a bad field is a footgun.
        with pytest.raises(ValueError, match="nonexistent"):
            Product.es_scan(nonexistent=1)

    def test_non_positive_page_size_raises(self):
        with pytest.raises(ValueError, match="page_size"):
            Product.es_scan(page_size=0)


# ── result shaping ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsScanResults:
    def test_es_only_reconstructs_objects(self):
        es = _es_pages([{"id": 7, "query": "hi", "results_count": 3}])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            objs = list(SearchLog.es_scan())
        assert [o.pk for o in objs] == [7]
        assert objs[0].query == "hi"

    def test_dual_yields_db_objects_in_cursor_order(self):
        a = Product.objects.create(name="A", price=1, available=True)
        b = Product.objects.create(name="B", price=2, available=True)
        es = _es_pages([{"id": a.pk}, {"id": b.pk}])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            names = [p.name for p in Product.es_scan(available=True)]
        assert names == ["A", "B"]

    def test_dual_skips_pks_absent_from_the_db(self):
        a = Product.objects.create(name="A", price=1, available=True)
        es = _es_pages([{"id": a.pk}, {"id": 999999}])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            names = [p.name for p in Product.es_scan(available=True)]
        assert names == ["A"]


# ── fallback behaviour ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsScanFallback:
    def test_dual_falls_back_to_db_iterator_when_es_disabled(self):
        Product.objects.create(name="On", price=1, available=True)
        Product.objects.create(name="Off", price=1, available=False)
        # ELASTICSEARCH_ENABLED defaults False in the test settings.
        names = sorted(p.name for p in Product.es_scan(available=True))
        assert names == ["On"]

    def test_dual_db_fallback_uses_in_lookup_for_lists(self):
        Product.objects.create(name="P1", price=1, available=True)
        Product.objects.create(name="P2", price=2, available=True)
        Product.objects.create(name="P3", price=9, available=True)
        names = sorted(p.name for p in Product.es_scan(price=[1, 2]))
        assert names == ["P1", "P2"]

    def test_dual_falls_back_to_db_when_es_errors_before_streaming(self):
        Product.objects.create(name="Kept", price=1, available=True)
        es = MagicMock()
        es.search.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            names = [p.name for p in Product.es_scan(available=True)]
        assert names == ["Kept"]

    def test_dual_db_fallback_fails_closed_on_non_column_field(self):
        Product.objects.create(name="X", price=1, available=True)
        mapping = dict(Product.get_es_mapping())
        mapping["ghost"] = {"type": "keyword"}
        with patch.object(Product, "get_es_mapping", return_value=mapping):
            result = list(Product.es_scan(ghost="anything"))
        assert result == []

    def test_es_only_yields_nothing_when_es_disabled(self):
        assert list(SearchLog.es_scan()) == []

    def test_es_only_yields_nothing_on_es_error(self):
        es = MagicMock()
        es.search.side_effect = RuntimeError("es down")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            assert list(SearchLog.es_scan()) == []

    def test_dual_stops_without_restarting_if_es_fails_mid_stream(self):
        # If ES dies *after* a document was already yielded, the scan must not
        # silently restart on the DB — that would re-emit the yielded row and
        # stream ones the ES cursor had already passed. It stops where it was.
        a = Product.objects.create(name="A", price=1, available=True)
        Product.objects.create(name="B", price=2, available=True)
        es = MagicMock()
        es.search.side_effect = [
            # A full page (==page_size) → the loop asks for another page…
            {"hits": {"hits": [{"_source": {"id": a.pk}, "sort": [a.pk]}]}},
            RuntimeError("es down mid-scan"),  # …which fails.
        ]
        with override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_ES_SEARCH_LIMIT=1), \
                patch.object(Product, "get_es_client", return_value=es):
            names = [p.name for p in Product.es_scan(available=True)]
        # Only the one already-yielded row — no DB restart that would re-add "A"
        # or spuriously pull in "B".
        assert names == ["A"]
