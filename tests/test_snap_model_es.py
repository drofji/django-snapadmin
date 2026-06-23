
import pytest
from demo.models import Product
from django.conf import settings

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
