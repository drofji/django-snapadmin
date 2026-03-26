"""
tests/test_tasks.py

Tests for Celery tasks (api/tasks.py and demo/tasks.py).

All tasks are called synchronously via task.apply() so no broker is needed.
External dependencies (Elasticsearch) are mocked.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# api.tasks.purge_expired_tokens
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeExpiredTokens:
    def test_deletes_expired_tokens(self, db, admin_user):
        from snapadmin.models import APIToken
        from snapadmin.api.tasks import purge_expired_tokens

        APIToken.objects.create(
            user=admin_user,
            token_name="Expired 1",
            expiration_date=timezone.now() - timedelta(days=1),
        )
        APIToken.objects.create(
            user=admin_user,
            token_name="Expired 2",
            expiration_date=timezone.now() - timedelta(hours=2),
        )
        result = purge_expired_tokens()
        assert result["deleted"] >= 2

    def test_keeps_non_expired_tokens(self, api_token):
        from snapadmin.api.tasks import purge_expired_tokens

        before = __import__("snapadmin.models", fromlist=["APIToken"]).APIToken.objects.count()
        purge_expired_tokens()
        after = __import__("snapadmin.models", fromlist=["APIToken"]).APIToken.objects.count()
        assert after == before  # active, non-expired token must survive

    def test_keeps_inactive_tokens(self, inactive_token):
        """Inactive (but non-expired) tokens are NOT deleted – only expired ones are."""
        from snapadmin.models import APIToken
        from snapadmin.api.tasks import purge_expired_tokens

        pk = inactive_token.pk
        purge_expired_tokens()
        assert APIToken.objects.filter(pk=pk).exists()

    def test_returns_deleted_count(self, db, admin_user):
        from snapadmin.models import APIToken
        from snapadmin.api.tasks import purge_expired_tokens

        APIToken.objects.create(
            user=admin_user,
            token_name="Old",
            expiration_date=timezone.now() - timedelta(seconds=1),
        )
        result = purge_expired_tokens()
        assert isinstance(result["deleted"], int)
        assert result["deleted"] >= 1

    def test_returns_cutoff_timestamp(self, db):
        from snapadmin.api.tasks import purge_expired_tokens
        result = purge_expired_tokens()
        assert "cutoff" in result

    def test_zero_deleted_when_none_expired(self, api_token):
        from snapadmin.api.tasks import purge_expired_tokens
        result = purge_expired_tokens()
        # api_token never expires – nothing should be deleted
        assert result["deleted"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# demo.tasks.generate_daily_stats
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGenerateDailyStats:
    def test_returns_dict(self, product, customer, order):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert isinstance(result, dict)

    def test_has_required_keys(self, product, customer, order):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        for key in ("date", "total_products", "active_products",
                    "total_customers", "active_customers",
                    "total_orders", "total_revenue", "avg_order_value"):
            assert key in result, f"Missing key: {key}"

    def test_counts_products(self, product, product_unavailable):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_products"] >= 2

    def test_counts_only_available_products(self, product, product_unavailable):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["active_products"] >= 1
        assert result["active_products"] < result["total_products"]

    def test_counts_customers(self, customer, customer_inactive):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_customers"] >= 2

    def test_counts_only_active_customers(self, customer, customer_inactive):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["active_customers"] < result["total_customers"]

    def test_revenue_is_float(self, order):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert isinstance(result["total_revenue"], float)

    def test_revenue_sums_orders(self, order):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_revenue"] >= float(order.total)

    def test_avg_order_value_is_float(self, order):
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert isinstance(result["avg_order_value"], float)

    def test_date_is_today(self):
        from datetime import date
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["date"] == date.today().isoformat()

    def test_works_with_empty_db(self, db):
        """Task should not crash on an empty database."""
        from demo.tasks import generate_daily_stats
        result = generate_daily_stats()
        assert result["total_products"] == 0
        assert result["total_revenue"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# demo.tasks.reindex_products_to_elasticsearch
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReindexProductsToElasticsearch:
    def test_skips_when_es_unavailable(self, product, settings):
        settings.ELASTICSEARCH_ENABLED = False
        from demo.tasks import reindex_products_to_elasticsearch
        result = reindex_products_to_elasticsearch()
        assert result["skipped"] is True

    def test_skip_reason_in_result(self, product, settings):
        settings.ELASTICSEARCH_ENABLED = False
        from demo.tasks import reindex_products_to_elasticsearch
        result = reindex_products_to_elasticsearch()
        assert "reason" in result

    def test_indexes_products_when_es_available(self, product, settings):
        settings.ELASTICSEARCH_ENABLED = True
        from unittest.mock import MagicMock
        mock_es = MagicMock()
        with patch("demo.models.Product.get_es_client", return_value=mock_es):
            from demo.tasks import reindex_products_to_elasticsearch
            result = reindex_products_to_elasticsearch()
        assert result["indexed"] >= 1
        mock_es.index.assert_called()

    def test_returns_indexed_count(self, many_products, settings):
        settings.ELASTICSEARCH_ENABLED = True
        from unittest.mock import MagicMock
        mock_es = MagicMock()
        with patch("demo.models.Product.get_es_client", return_value=mock_es):
            from demo.tasks import reindex_products_to_elasticsearch
            result = reindex_products_to_elasticsearch()
        assert result["indexed"] == 30  # many_products creates 30
