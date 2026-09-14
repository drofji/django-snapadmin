"""
tests/test_elasticsearch_live.py

The Elasticsearch surface against a **real cluster**, not a mock.

Every other Elasticsearch test in this suite patches ``get_es_client``, which is
the right trade for the everyday run — but a ``MagicMock`` accepts any call with
any arguments and returns whatever it was told to. It cannot reject a malformed
query, so the one thing these methods exist to build — the query DSL — is
exactly the thing mocks cannot check. A ``terms`` clause with the wrong nesting,
an aggregation missing its field, a ``search_after`` cursor in the wrong shape:
all of them pass against a mock and answer ``400 Bad Request`` against
Elasticsearch.

These tests are **deselected by default** (``-m "not real_es"`` in
``pytest.ini``): the everyday run must stay at its forty seconds and must not
need a cluster. CI runs them in the same job that runs the suite against
PostgreSQL. To run them locally::

    docker run --rm -d -p 9200:9200 -e discovery.type=single-node \\
        -e xpack.security.enabled=false elasticsearch:8.19.3
    SNAPADMIN_TEST_ES_URL=http://localhost:9200 pytest -m real_es

Each test owns its index and deletes it afterwards, so a failed run leaves
nothing behind for the next one to trip over (#QA1f).
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from django.test import override_settings

pytestmark = pytest.mark.real_es

ES_URL = os.environ.get("SNAPADMIN_TEST_ES_URL", "http://localhost:9200")


@pytest.fixture
def live_es():
    """A real client, with the demo's index removed before and after."""
    from demo.apps.shop.models import Product, SearchLog

    with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
        client = Product.get_es_client()
        for model in (Product, SearchLog):
            client.indices.delete(index=model.get_es_index_name(), ignore_unavailable=True)
        try:
            yield client
        finally:
            for model in (Product, SearchLog):
                client.indices.delete(index=model.get_es_index_name(), ignore_unavailable=True)


def _refresh(client, model):
    """Make just-written documents visible to search."""
    client.indices.refresh(index=model.get_es_index_name())


class TestTheClientReachesTheCluster:
    def test_the_configured_url_produces_a_client_that_answers(self, live_es):
        assert live_es.ping() is True
        assert live_es.info()["version"]["number"].startswith("8.")


class TestIndexAndMappingAreAccepted:
    def test_the_declared_mapping_is_created_as_written(self, live_es):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            Product._ensure_es_index_and_mapping()

        index = Product.get_es_index_name()
        assert live_es.indices.exists(index=index)
        properties = live_es.indices.get_mapping(index=index)[index]["mappings"]["properties"]
        # Elasticsearch accepted every declared type — a mock would have accepted
        # a type that does not exist just as happily.
        assert properties["name"]["type"] == "text"
        assert properties["price"]["type"] == "float"
        assert properties["available"]["type"] == "boolean"
        assert properties["id"]["type"] == "integer"

    def test_the_declared_index_settings_are_applied(self, live_es):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            Product._ensure_es_index_and_mapping()

        index = Product.get_es_index_name()
        settings_applied = live_es.indices.get_settings(index=index)[index]["settings"]["index"]
        assert settings_applied["number_of_shards"] == "1"


@pytest.mark.django_db
class TestDocumentsRoundTripThroughTheCluster:
    def _indexed_product(self, live_es, **kwargs):
        from demo.apps.shop.models import Product

        defaults = {"name": "Live Widget", "price": Decimal("19.99"), "available": True}
        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            product = Product.objects.create(**{**defaults, **kwargs})
            _refresh(live_es, Product)
        return product

    def test_saving_a_row_puts_a_readable_document_in_the_index(self, live_es):
        from demo.apps.shop.models import Product

        product = self._indexed_product(live_es)

        stored = live_es.get(index=Product.get_es_index_name(), id=product.pk)["_source"]
        assert stored["name"] == "Live Widget"
        assert stored["available"] is True

    def test_deleting_a_row_removes_its_document(self, live_es):
        from elasticsearch import NotFoundError

        from demo.apps.shop.models import Product

        product = self._indexed_product(live_es)
        index, pk = Product.get_es_index_name(), product.pk

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            product.delete()
        _refresh(live_es, Product)

        with pytest.raises(NotFoundError):
            live_es.get(index=index, id=pk)


@pytest.mark.django_db
class TestTheQueryDslIsValid:
    """The half a mock can never check: does Elasticsearch accept the query?"""

    @pytest.fixture(autouse=True)
    def catalogue(self, live_es):
        from demo.apps.shop.models import Product

        Product.objects.all().delete()
        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            self.cheap = Product.objects.create(
                name="Cheap Widget", price=Decimal("9.99"), available=True
            )
            self.dear = Product.objects.create(
                name="Dear Widget", price=Decimal("99.99"), available=True
            )
            self.gone = Product.objects.create(
                name="Gone Widget", price=Decimal("19.99"), available=False
            )
            _refresh(live_es, Product)

    def test_a_full_text_search_returns_the_matching_rows(self):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            found = list(Product.es_search(query_string="Cheap"))

        assert [p.pk for p in found] == [self.cheap.pk]

    def test_a_scalar_term_filter_is_accepted_and_selective(self):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            found = list(Product.es_filter(available=False, db_fallback=False))

        assert [p.pk for p in found] == [self.gone.pk]

    def test_a_list_builds_a_terms_clause_the_cluster_accepts(self):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            found = Product.es_filter(price=[9.99, 99.99], db_fallback=False)

        assert {p.pk for p in found} == {self.cheap.pk, self.dear.pk}

    def test_the_count_query_is_accepted_and_counts(self):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            assert Product.es_count(available=True, db_fallback=False) == 2

    def test_the_aggregation_query_is_accepted_and_buckets(self):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            buckets = Product.es_aggregate("available", db_fallback=False)

        assert buckets["available"] == {True: 2, False: 1}

    def test_the_deep_scan_cursor_is_accepted_and_yields_every_row(self):
        from demo.apps.shop.models import Product

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            # page_size=1 forces several search_after round trips, which is the
            # part whose cursor shape a mock cannot validate.
            scanned = [p.pk for p in Product.es_scan(page_size=1, db_fallback=False)]

        assert sorted(scanned) == sorted([self.cheap.pk, self.dear.pk, self.gone.pk])


class TestAnEsOnlyModelLivesEntirelyInTheCluster:
    def test_a_row_is_written_to_and_read_back_from_the_index(self, live_es):
        from demo.apps.shop.models import SearchLog

        with override_settings(ELASTICSEARCH_ENABLED=True, ELASTICSEARCH_URL=ES_URL):
            SearchLog._ensure_es_index_and_mapping()
            log = SearchLog(query="live query", results_count=7)
            log.pk = 4242
            log.index_in_es()
            _refresh(live_es, SearchLog)

            found = list(SearchLog.es_filter(results_count=7, db_fallback=False))

        assert [row.query for row in found] == ["live query"]
