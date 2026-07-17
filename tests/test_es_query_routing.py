"""
tests/test_es_query_routing.py  –  smart ES query routing for the REST API

DUAL-storage models mirror their data in Elasticsearch, so full-text `?search=`
API requests are routed to ES (fuzzy, relevance-ranked) while plain listings
stay on the database. The X-Snap-Query-Backend response header exposes the
routing decision.
"""

import pytest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from demo.app.models import Customer, Product, SearchLog
from snapadmin.api.views import DynamicModelViewSet


def _es_client_returning(pks):
    es = MagicMock()
    es.search.return_value = {
        "hits": {"hits": [{"_source": {"id": pk}} for pk in pks]}
    }
    return es


# ── DUAL models: search goes to ES, listings stay on the DB ──────────────────

@pytest.mark.django_db
class TestDualModelRouting:
    def test_search_routed_to_es(self, auth_client, product):
        es = _es_client_returning([product.pk])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            r = auth_client.get("/api/models/demo/Product/?search=demo")
        assert r.status_code == 200
        assert r["X-Snap-Query-Backend"] == "elasticsearch"
        assert [p["id"] for p in r.json()["results"]] == [product.pk]
        es.search.assert_called_once()

    def test_es_relevance_order_preserved(self, auth_client):
        first = Product.objects.create(name="Alpha", price=1)
        second = Product.objects.create(name="Beta", price=2)
        # ES ranks `second` higher — the API must return it first.
        es = _es_client_returning([second.pk, first.pk])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            r = auth_client.get("/api/models/demo/Product/?search=widget")
        assert [p["id"] for p in r.json()["results"]] == [second.pk, first.pk]

    def test_plain_listing_stays_on_db(self, auth_client, product):
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            r = auth_client.get("/api/models/demo/Product/")
        assert r.status_code == 200
        assert r["X-Snap-Query-Backend"] == "database"
        es.search.assert_not_called()
        assert any(p["id"] == product.pk for p in r.json()["results"])

    def test_global_kill_switch_forces_db(self, auth_client, product):
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_ES_QUERY_ROUTING=False), \
                patch.object(Product, "get_es_client", return_value=es):
            r = auth_client.get(f"/api/models/demo/Product/?search={product.name}")
        assert r["X-Snap-Query-Backend"] == "database"
        es.search.assert_not_called()
        # DB icontains search still finds the product
        assert any(p["id"] == product.pk for p in r.json()["results"])

    def test_per_model_opt_out_forces_db(self, auth_client, product):
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "es_query_routing", False), \
                patch.object(Product, "get_es_client", return_value=es):
            r = auth_client.get(f"/api/models/demo/Product/?search={product.name}")
        assert r["X-Snap-Query-Backend"] == "database"
        es.search.assert_not_called()

    def test_es_disabled_falls_back_to_db_search(self, auth_client, product):
        # ELASTICSEARCH_ENABLED=False (test default) → routing unavailable.
        r = auth_client.get(f"/api/models/demo/Product/?search={product.name}")
        assert r["X-Snap-Query-Backend"] == "database"
        assert any(p["id"] == product.pk for p in r.json()["results"])

    def test_search_limit_setting_passed_to_es(self, auth_client, product):
        es = _es_client_returning([product.pk])
        with override_settings(ELASTICSEARCH_ENABLED=True, SNAPADMIN_ES_SEARCH_LIMIT=7), \
                patch.object(Product, "get_es_client", return_value=es):
            auth_client.get("/api/models/demo/Product/?search=demo")
        assert es.search.call_args.kwargs["body"]["size"] == 7


# ── DB path: `?search=` filters via searchable Snap fields ───────────────────

@pytest.mark.django_db
class TestDbSearch:
    def test_search_filters_by_searchable_fields(self, auth_client):
        Product.objects.create(name="UniqueWidget", price=1)
        Product.objects.create(name="Other", price=1)
        r = auth_client.get("/api/models/demo/Product/?search=UniqueWidget")
        names = [p["name"] for p in r.json()["results"]]
        assert names == ["UniqueWidget"]

    def test_search_matches_id_when_no_searchable_fields(self, auth_client, customer):
        # Customer defines no searchable=True fields, so only the implicit
        # "id" search field (same as the admin search box) applies.
        r = auth_client.get(f"/api/models/demo/Customer/?search={customer.pk}")
        assert r.status_code == 200
        assert any(c["id"] == customer.pk for c in r.json()["results"])

    def test_blank_search_param_is_plain_listing(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/?search=")
        assert r["X-Snap-Query-Backend"] == "database"
        assert r.json()["count"] >= 1

    def test_derived_search_fields_for_snap_model(self):
        assert "name" in DynamicModelViewSet._db_search_fields(Product)

    def test_derived_search_fields_fall_back_to_id(self):
        # Without searchable=True fields, only the implicit id column remains.
        assert DynamicModelViewSet._db_search_fields(Customer) == ("id",)

    def test_derived_search_fields_none_for_plain_class(self):
        class Plain:
            pass
        assert DynamicModelViewSet._db_search_fields(Plain) is None


# ── ES_ONLY models: the search term reaches the ES query ─────────────────────

@pytest.mark.django_db
class TestEsOnlyRouting:
    def test_search_term_passed_to_es_query(self, auth_client):
        es = MagicMock()
        es.search.return_value = {
            "hits": {"hits": [{"_source": {"id": 5, "query": "hello"}}]}
        }
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(SearchLog, "get_es_client", return_value=es):
            r = auth_client.get("/api/models/demo/SearchLog/?search=hello")
        assert r.status_code == 200
        assert r["X-Snap-Query-Backend"] == "elasticsearch"
        body = es.search.call_args.kwargs["body"]
        assert body["query"]["multi_match"]["query"] == "hello"

    def test_listing_without_search_uses_match_all(self, auth_client):
        r = auth_client.get("/api/models/demo/SearchLog/")
        assert r.status_code == 200
        assert r["X-Snap-Query-Backend"] == "elasticsearch"
