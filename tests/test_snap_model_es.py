
import pytest
from unittest.mock import MagicMock, patch
from demo.models import Product
from demo.models import SearchLog
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
