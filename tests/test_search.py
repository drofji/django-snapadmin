"""
tests/test_search.py

Tests for demo/search.py — Elasticsearch integration + graceful degradation.

Strategy:
- is_es_available() is mocked in most tests to avoid needing a real ES cluster.
- Tests verify fallback paths work correctly when ES is unavailable.
- Tests verify correct behaviour when ES IS available (via mock).
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# is_es_available
# ─────────────────────────────────────────────────────────────────────────────

class TestIsEsAvailable:
    def test_returns_false_when_disabled_in_settings(self, settings):
        settings.ELASTICSEARCH_ENABLED = False
        from demo import search
        assert search.is_es_available() is False

    def test_returns_false_when_ping_fails(self, settings):
        settings.ELASTICSEARCH_ENABLED = True
        with patch("demo.search.get_es_client") as mock_client:
            mock_client.return_value.ping.return_value = False
            from demo import search
            assert search.is_es_available() is False

    def test_returns_true_when_ping_succeeds(self, settings):
        settings.ELASTICSEARCH_ENABLED = True
        with patch("demo.search.get_es_client") as mock_client:
            mock_client.return_value.ping.return_value = True
            from demo import search
            assert search.is_es_available() is True

    def test_returns_false_on_connection_error(self, settings):
        settings.ELASTICSEARCH_ENABLED = True
        with patch("demo.search.get_es_client") as mock_client:
            mock_client.return_value.ping.side_effect = ConnectionError("refused")
            from demo import search
            assert search.is_es_available() is False

    def test_never_raises(self, settings):
        settings.ELASTICSEARCH_ENABLED = True
        with patch("demo.search.get_es_client") as mock_client:
            mock_client.side_effect = Exception("unexpected!")
            from demo import search
            # Must not raise
            result = search.is_es_available()
            assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# search_products – DB fallback path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchProductsDbFallback:
    """When ES is unavailable, search_products falls back to ORM queries."""

    def test_fallback_finds_matching_product(self, product):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import search_products
            results = search_products("Laptop")
        names = [r["name"] for r in results]
        assert product.name in names

    def test_fallback_case_insensitive(self, product):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import search_products
            results = search_products("laptop")  # lowercase
        names = [r["name"] for r in results]
        assert product.name in names

    def test_fallback_no_match_returns_empty(self, product):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import search_products
            results = search_products("xyzzynonexistent")
        assert results == []

    def test_fallback_result_has_required_keys(self, product):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import search_products
            results = search_products("Laptop")
        assert len(results) > 0
        r = results[0]
        assert "id" in r
        assert "name" in r
        assert "price" in r
        assert "available" in r

    def test_fallback_respects_limit(self, many_products):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import search_products
            results = search_products("Product", limit=5)
        assert len(results) <= 5

    def test_fallback_price_is_float(self, product):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import search_products
            results = search_products("Laptop")
        assert isinstance(results[0]["price"], float)

    def test_fallback_available_is_bool(self, product):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import search_products
            results = search_products("Laptop")
        assert isinstance(results[0]["available"], bool)


# ─────────────────────────────────────────────────────────────────────────────
# search_products – ES path (mocked)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchProductsEsPath:
    def test_uses_es_when_available(self, product):
        mock_es = MagicMock()
        mock_es.search.return_value = {
            "hits": {
                "hits": [
                    {"_source": {"id": product.pk, "name": product.name, "price": 49.99, "available": True}}
                ]
            }
        }
        with patch("demo.search.is_es_available", return_value=True), \
             patch("demo.search.get_es_client", return_value=mock_es):
            from demo.search import search_products
            results = search_products("Laptop")
        assert len(results) == 1
        assert results[0]["name"] == product.name

    def test_falls_back_to_db_when_es_search_raises(self, product):
        mock_es = MagicMock()
        mock_es.search.side_effect = Exception("ES timeout")
        with patch("demo.search.is_es_available", return_value=True), \
             patch("demo.search.get_es_client", return_value=mock_es):
            from demo.search import search_products
            results = search_products("Laptop")
        # Should have fallen back to DB without raising
        assert isinstance(results, list)


# ─────────────────────────────────────────────────────────────────────────────
# index_product
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestIndexProduct:
    def test_skips_when_es_unavailable(self, product):
        """index_product must be a no-op when ES is down."""
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import index_product
            index_product(product)  # must not raise

    def test_calls_es_index_when_available(self, product):
        mock_es = MagicMock()
        mock_es.indices.exists.return_value = True
        with patch("demo.search.is_es_available", return_value=True), \
             patch("demo.search.get_es_client", return_value=mock_es):
            from demo.search import index_product
            index_product(product)
        mock_es.index.assert_called_once()
        call_kwargs = mock_es.index.call_args.kwargs
        assert call_kwargs["id"] == product.pk

    def test_swallows_es_errors(self, product):
        mock_es = MagicMock()
        mock_es.indices.exists.return_value = True
        mock_es.index.side_effect = Exception("ES write failed")
        with patch("demo.search.is_es_available", return_value=True), \
             patch("demo.search.get_es_client", return_value=mock_es):
            from demo.search import index_product
            index_product(product)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# delete_product
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDeleteProduct:
    def test_skips_when_es_unavailable(self):
        with patch("demo.search.is_es_available", return_value=False):
            from demo.search import delete_product
            delete_product(99999)  # must not raise

    def test_calls_es_delete_when_available(self):
        mock_es = MagicMock()
        with patch("demo.search.is_es_available", return_value=True), \
             patch("demo.search.get_es_client", return_value=mock_es):
            from demo.search import delete_product
            delete_product(42)
        mock_es.delete.assert_called_once()

    def test_swallows_es_errors(self):
        mock_es = MagicMock()
        mock_es.delete.side_effect = Exception("ES delete failed")
        with patch("demo.search.is_es_available", return_value=True), \
             patch("demo.search.get_es_client", return_value=mock_es):
            from demo.search import delete_product
            delete_product(99)  # must not raise
