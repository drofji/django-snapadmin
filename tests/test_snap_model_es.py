"""
tests/test_snap_model_es.py

``SnapModel``'s Elasticsearch surface: the index name a model resolves to, the
document it builds for the mirror, and what happens when the cluster is not
there to answer.
"""

from decimal import Decimal

import pytest
from unittest.mock import MagicMock, patch
from demo.apps.shop.models import Product
from demo.apps.shop.models import SearchLog
from django.conf import settings
from django.test import override_settings

@pytest.mark.django_db
class TestSnapModelES:
    def test_es_index_name(self):
        assert Product.get_es_index_name() == "snap_demo_product"

    def test_es_document(self):
        product = Product.objects.create(name="Test Product", price=10.0, available=True)
        doc = product.get_es_document()
        assert doc["id"] == product.pk
        assert doc["name"] == "Test Product"
        assert doc["price"] == 10.0
        assert doc["available"] == True

    def test_snap_search_fallback(self):
        # Clear existing products if any
        Product.objects.all().delete()
        Product.objects.create(name="SearchMe", price=5.0)
        Product.objects.create(name="Other", price=5.0)

        # ES is likely not running in test env, so it should fallback to DB
        results = Product.snap_search("SearchMe")
        assert results.count() == 1
        assert results[0].name == "SearchMe"


class TestEsOnlyPkGeneration:
    """ES_ONLY models mint their own ids; that id must be collision-resistant."""

    def test_pk_in_bigint_range_without_es(self):
        # ES disabled → single draw from the 63-bit space, no existence check.
        pk = SearchLog._generate_es_only_pk()
        assert 1 <= pk <= 9223372036854775807

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_pk_rerolls_on_existing_id(self):
        # First candidate "exists", second does not → loop must re-roll and return.
        es = MagicMock()
        es.exists.side_effect = [True, False]
        with patch.object(SearchLog, "get_es_client", return_value=es):
            pk = SearchLog._generate_es_only_pk()
        assert 1 <= pk <= 9223372036854775807
        assert es.exists.call_count == 2

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_pk_falls_back_when_es_errors(self):
        # ES unreachable mid-check → swallow and return the current candidate.
        with patch.object(SearchLog, "get_es_client", side_effect=Exception("es down")):
            pk = SearchLog._generate_es_only_pk()
        assert 1 <= pk <= 9223372036854775807


@pytest.mark.django_db
class TestElasticsearchFailuresAreSwallowed:
    """An unreachable cluster must never break a save, a delete or a migration.

    The Elasticsearch mirror is a secondary index: if the client cannot be
    built, indexing a row, deleting one, or creating the mapping has to fail
    quietly and leave the database operation intact. (Re-homed here in #QA1b
    from a suite that called all three and asserted nothing at all — "it did not
    raise" was the whole test.)
    """

    def _client_that_cannot_connect(self):
        return patch.object(Product, "get_es_client", side_effect=Exception("es down"))

    def test_ensuring_the_mapping_reports_failure_rather_than_raising(self):
        with override_settings(ELASTICSEARCH_ENABLED=True), self._client_that_cannot_connect():
            assert Product._ensure_es_index_and_mapping() is None

    def test_indexing_a_row_reports_failure_rather_than_raising(self):
        product = Product(name="ES Err", price=Decimal("1.00"))
        product.pk = 1234

        with override_settings(ELASTICSEARCH_ENABLED=True), self._client_that_cannot_connect():
            assert product.index_in_es() is None

    def test_deleting_a_row_reports_failure_rather_than_raising(self):
        product = Product(name="ES Err", price=Decimal("1.00"))
        product.pk = 1234

        with override_settings(ELASTICSEARCH_ENABLED=True), self._client_that_cannot_connect():
            assert product.delete_from_es() is None

    def test_the_row_itself_is_still_saved_when_the_mirror_is_down(self):
        with override_settings(ELASTICSEARCH_ENABLED=True), self._client_that_cannot_connect():
            product = Product.objects.create(name="Saved anyway", price=Decimal("2.00"))

        assert Product.objects.filter(pk=product.pk).exists()


# ─────────────────────────────────────────────────────────────────────────────
# get_es_document — how field values are coerced for the index
# (re-homed in #QA1b from a suite named for the coverage metric)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsDocumentValueCoercion:
    """Values that Elasticsearch cannot store verbatim are converted, not dropped."""

    def test_a_related_object_is_stored_as_its_primary_key(self):
        from demo.apps.shop.models import Category

        category = Category.objects.create(name="Tech", slug="tech")
        product = Product.objects.create(
            name="Widget", price=Decimal("9.99"), category=category
        )

        original_mapping = Product.es_mapping
        Product.es_mapping = {"category": {"type": "integer"}}
        try:
            document = product.get_es_document()
        finally:
            Product.es_mapping = original_mapping

        assert document == {"id": product.pk, "category": category.pk}

    def test_a_timedelta_is_stored_as_its_string_form(self):
        from datetime import timedelta

        product = Product.objects.create(name="Duration Test", price=Decimal("1.00"))

        original_mapping = Product.es_mapping
        Product.es_mapping = {"name": {"type": "text"}}
        try:
            product.name = timedelta(hours=2)
            document = product.get_es_document()
        finally:
            Product.es_mapping = original_mapping

        assert document["name"] == "2:00:00"


@pytest.mark.django_db
class TestIndexAndMappingMaintenance:
    """What the mirror actually asks the cluster to do, and when it asks nothing."""

    def _reachable_client(self):
        client = MagicMock()
        client.ping.return_value = True
        client.indices.exists.return_value = False
        return client

    def test_a_missing_index_is_created_with_the_model_s_mapping(self):
        client = self._reachable_client()

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            Product, "get_es_client", return_value=client
        ):
            Product._ensure_es_index_and_mapping()

        client.indices.create.assert_called_once()
        kwargs = client.indices.create.call_args.kwargs
        assert kwargs["index"] == Product.get_es_index_name()
        assert kwargs["body"]["mappings"]["properties"]["id"] == {"type": "integer"}
        client.indices.put_mapping.assert_not_called()

    def test_an_existing_index_has_its_mapping_updated_instead(self):
        client = self._reachable_client()
        client.indices.exists.return_value = True

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            Product, "get_es_client", return_value=client
        ):
            Product._ensure_es_index_and_mapping()

        client.indices.put_mapping.assert_called_once()
        assert client.indices.put_mapping.call_args.kwargs["index"] == (
            Product.get_es_index_name()
        )
        client.indices.create.assert_not_called()

    def test_nothing_is_asked_of_the_cluster_while_elasticsearch_is_off(self):
        client = self._reachable_client()

        with override_settings(ELASTICSEARCH_ENABLED=False), patch.object(
            Product, "get_es_client", return_value=client
        ):
            Product._ensure_es_index_and_mapping()

        client.indices.create.assert_not_called()
        client.indices.put_mapping.assert_not_called()


@pytest.mark.django_db
class TestRowLevelMirroring:
    def _reachable_client(self):
        client = MagicMock()
        client.ping.return_value = True
        return client

    def test_saving_a_row_indexes_it_under_its_primary_key(self):
        product = Product.objects.create(name="ES Test", price=Decimal("5.00"))
        client = self._reachable_client()

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            Product, "get_es_client", return_value=client
        ), patch.object(Product, "_ensure_es_index_and_mapping"):
            product.index_in_es()

        kwargs = client.index.call_args.kwargs
        assert kwargs["index"] == Product.get_es_index_name()
        assert kwargs["id"] == product.pk
        assert kwargs["document"]["name"] == "ES Test"

    def test_deleting_a_row_removes_that_document(self):
        product = Product.objects.create(name="Delete Me", price=Decimal("5.00"))
        client = self._reachable_client()

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            Product, "get_es_client", return_value=client
        ):
            product.delete_from_es()

        kwargs = client.delete.call_args.kwargs
        assert kwargs["index"] == Product.get_es_index_name()
        assert kwargs["id"] == product.pk

    def test_neither_touches_the_cluster_while_elasticsearch_is_off(self):
        product = Product.objects.create(name="No ES", price=Decimal("5.00"))
        client = self._reachable_client()

        with override_settings(ELASTICSEARCH_ENABLED=False), patch.object(
            Product, "get_es_client", return_value=client
        ):
            product.index_in_es()
            product.delete_from_es()

        client.index.assert_not_called()
        client.delete.assert_not_called()

    def test_deleting_an_es_only_row_clears_the_mirror(self):
        log = SearchLog(query="test", results_count=5)
        log.pk = 777

        with patch.object(SearchLog, "delete_from_es") as delete_from_es:
            log.delete()

        delete_from_es.assert_called_once()


@pytest.mark.django_db
class TestBulkReindex:
    def test_every_row_is_streamed_to_the_bulk_helper(self):
        Product.objects.all().delete()
        Product.objects.create(name="Reindex Test", price=Decimal("1.00"))
        client = MagicMock()
        client.ping.return_value = True

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            Product, "get_es_client", return_value=client
        ), patch.object(Product, "_ensure_es_index_and_mapping"), patch(
            "elasticsearch.helpers.bulk", return_value=(1, [])
        ) as bulk:
            result = Product.es_reindex_all()

        assert result == {"indexed": 1}
        actions = list(bulk.call_args.args[1])
        assert len(actions) == 1
        assert actions[0]["_source"]["name"] == "Reindex Test"
        assert actions[0]["_index"] == Product.get_es_index_name()

    def test_a_reindex_with_elasticsearch_off_reports_that_it_skipped(self):
        with override_settings(ELASTICSEARCH_ENABLED=False):
            # The reason travels with the answer, so a caller can report *why*
            # nothing was reindexed rather than just that nothing was.
            assert Product.es_reindex_all() == {
                "skipped": True,
                "reason": "Elasticsearch not available",
            }


@pytest.mark.django_db
class TestEsSearchRouting:
    """``es_search`` answers from the index, then from the database if it must."""

    def test_a_dual_model_returns_database_rows_for_the_ids_the_index_matched(self):
        Product.objects.all().delete()
        product = Product.objects.create(name="Found", price=Decimal("1.00"))
        client = MagicMock()
        client.search.return_value = {"hits": {"hits": [{"_source": {"id": product.pk}}]}}

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            Product, "get_es_client", return_value=client
        ):
            results = Product.es_search(query_string="test")

        assert list(results) == [product]

    def test_a_dual_model_falls_back_to_the_database_when_the_query_fails(self):
        Product.objects.all().delete()
        product = Product.objects.create(name="Found", price=Decimal("1.00"))
        client = MagicMock()
        client.search.side_effect = Exception("ES offline")

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            Product, "get_es_client", return_value=client
        ):
            results = Product.es_search(query_string="Found")

        # The database answered the same search, so the caller never sees the outage.
        assert list(results) == [product]

    def test_an_es_only_model_answers_from_the_index(self):
        from snapadmin.models import EsQuerySet

        client = MagicMock()
        client.search.return_value = {
            "hits": {"hits": [{"_source": {"id": 10, "query": "test", "results_count": 5}}]}
        }

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            SearchLog, "get_es_client", return_value=client
        ):
            results = SearchLog.es_search(query_string="test")

        assert isinstance(results, EsQuerySet)
        assert [row.query for row in results] == ["test"]

    def test_an_es_only_model_has_nothing_to_fall_back_to_and_returns_empty(self):
        from snapadmin.models import EsQuerySet

        client = MagicMock()
        client.search.side_effect = Exception("ES offline")

        with override_settings(ELASTICSEARCH_ENABLED=True), patch.object(
            SearchLog, "get_es_client", return_value=client
        ):
            results = SearchLog.es_search(query_string="test")

        assert isinstance(results, EsQuerySet)
        assert list(results) == []

    def test_an_empty_query_returns_every_row(self):
        Product.objects.all().delete()
        alpha = Product.objects.create(name="Alpha", price=Decimal("1.00"))
        beta = Product.objects.create(name="Beta", price=Decimal("2.00"))

        assert set(Product.es_search(query_string="")) == {alpha, beta}

    def test_a_model_with_no_searchable_fields_also_returns_every_row(self):
        """``Order`` declares no searchable field, so there is nothing to match
        on and an empty query still has to hand back the whole table.

        It is also tenant-scoped, so the read happens inside a bound tenant —
        outside one, default-deny would hide the row and the test would be
        measuring tenancy rather than search.
        """
        from demo.apps.shop.models import Customer, Order
        from snapadmin import tenancy

        Order.objects.all().delete()
        Customer.objects.all().delete()
        customer = Customer.objects.create(
            first_name="X", last_name="Y", email="x@y.com", origin="status_a"
        )
        order = Order.objects.create(
            customer=customer, total=Decimal("10.00"), tenant_id="example.com"
        )

        with tenancy.use_tenant("example.com"):
            assert list(Order.es_search(query_string="")) == [order]
