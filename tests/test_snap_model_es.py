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
