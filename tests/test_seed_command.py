"""
tests/test_seed_command.py

Tests for the seed_demo management command.

All Elasticsearch calls are patched so no ES cluster is needed.
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command


# ─────────────────────────────────────────────────────────────────────────────
# Basic seeding
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestSeedDemoCommand:
    def _call(self, *args, **kwargs):
        out = StringIO()
        kwargs.setdefault("stdout", out)
        kwargs.setdefault("no_index", True)  # always skip ES in tests
        call_command("seed_demo", *args, **kwargs)
        return out.getvalue()

    def test_creates_products(self):
        from demo.models import Product
        self._call(count=5)
        assert Product.objects.count() >= 5

    def test_creates_customers(self):
        from demo.models import Customer
        self._call(count=5)
        assert Customer.objects.count() >= 5

    def test_creates_orders(self):
        from demo.models import Order
        self._call(count=5)
        assert Order.objects.count() >= 5

    def test_creates_superuser_if_none_exists(self, db):
        from django.contrib.auth.models import User
        User.objects.filter(is_superuser=True).delete()
        self._call(count=3)
        assert User.objects.filter(is_superuser=True).exists()

    def test_does_not_duplicate_superuser(self, db):
        from django.contrib.auth.models import User
        # Create admin first
        self._call(count=3)
        count_after_first = User.objects.filter(is_superuser=True).count()
        # Run again – should not create a second superuser
        self._call(count=3)
        assert User.objects.filter(is_superuser=True).count() == count_after_first

    def test_creates_api_token(self):
        from snapadmin.models import APIToken
        self._call(count=3)
        assert APIToken.objects.filter(token_name="Demo Token").exists()

    def test_output_contains_success_message(self):
        output = self._call(count=3)
        assert "Seeding complete" in output or "✅" in output

    def test_output_prints_token_key(self):
        output = self._call(count=3)
        # Token key is 40 alphanumeric chars – check something key-like is in output
        from snapadmin.models import APIToken
        token = APIToken.objects.filter(token_name="Demo Token").first()
        assert token.token_key in output

    def test_custom_count_respected(self):
        from demo.models import Product
        self._call(count=10)
        assert Product.objects.count() >= 10

    def test_flush_clears_existing_data(self):
        from demo.models import Customer, Product
        self._call(count=5)
        count_before = Product.objects.count()
        self._call(count=5, flush=True)
        # After flush + re-seed there should be exactly 5 products
        assert Product.objects.count() == 5

    def test_products_have_valid_prices(self):
        from decimal import Decimal
        from demo.models import Product
        self._call(count=10)
        for p in Product.objects.all():
            assert p.price > Decimal("0")

    def test_customers_have_valid_origins(self):
        from demo.models import Customer
        valid_origins = {"status_a", "status_b", "status_c"}
        self._call(count=10)
        for c in Customer.objects.all():
            assert c.origin in valid_origins

    def test_orders_linked_to_customers(self):
        from demo.models import Order
        self._call(count=5)
        for o in Order.objects.select_related("customer"):
            assert o.customer_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# Elasticsearch indexing path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestSeedDemoEsIndexing:
    def test_skips_index_when_es_unavailable(self):
        """--no-index flag means no ES calls are made."""
        out = StringIO()
        with patch("demo.search.is_es_available", return_value=False):
            call_command("seed_demo", count=3, no_index=True, stdout=out)
        # No exception means graceful skip
        assert True

    def test_indexes_when_es_available(self):
        """With ES available and no --no-index, index_product should be called."""
        from unittest.mock import MagicMock, call
        mock_index = MagicMock()
        out = StringIO()
        with patch("demo.search.is_es_available", return_value=True), \
             patch("demo.search.index_product", mock_index):
            call_command("seed_demo", count=3, no_index=False, stdout=out)
        assert mock_index.call_count >= 3
