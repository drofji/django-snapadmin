"""
tests/test_pagination.py — fast approximate-count pagination (issue #5)

EstimatedCountPaginator swaps the changelist COUNT(*) for PostgreSQL's
reltuples estimate on unfiltered, large tables, and falls back to an exact
count everywhere it isn't safe (other DBs, filtered queries, small tables,
kill-switch off).
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin import site
from django.test import override_settings

from snapadmin.pagination import (
    EstimatedCountPaginator,
    estimated_count_enabled,
    pg_estimated_count,
)


@pytest.fixture
def products(db):
    from demo.apps.shop.models import Product
    from decimal import Decimal
    for i in range(5):
        Product.objects.create(name=f"P{i}", price=Decimal(i))
    # Ordered like the admin changelist hands it to the paginator (the base
    # manager no longer injects a default order), so pagination is deterministic.
    return Product.objects.order_by("-pk")


# ── config ───────────────────────────────────────────────────────────────────

class TestConfig:
    def test_enabled_default(self):
        assert estimated_count_enabled() is True

    @override_settings(SNAPADMIN_ESTIMATED_COUNT=False)
    def test_disabled(self):
        assert estimated_count_enabled() is False


# ── pg_estimated_count() ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPgEstimate:
    def test_non_queryset_returns_none(self):
        assert pg_estimated_count([1, 2, 3]) is None

    def test_sqlite_returns_none(self, products):
        # The test DB is SQLite (vendor != postgresql) → no estimate.
        assert pg_estimated_count(products) is None

    def test_filtered_queryset_returns_none(self, products):
        # Even mocked as PostgreSQL, a WHERE clause forces exact count.
        conn = MagicMock()
        conn.vendor = "postgresql"
        with patch("snapadmin.pagination.connections", {products.db: conn}):
            assert pg_estimated_count(products.filter(name="P1")) is None
        conn.cursor.assert_not_called()

    def test_postgres_estimate_used(self, products):
        conn = MagicMock()
        conn.vendor = "postgresql"
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = [5_000_000]
        with patch("snapadmin.pagination.connections", {products.db: conn}):
            assert pg_estimated_count(products) == 5_000_000

    def test_postgres_no_estimate_row(self, products):
        conn = MagicMock()
        conn.vendor = "postgresql"
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        with patch("snapadmin.pagination.connections", {products.db: conn}):
            assert pg_estimated_count(products) is None

    def test_postgres_negative_estimate_ignored(self, products):
        conn = MagicMock()
        conn.vendor = "postgresql"
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = [-1]
        with patch("snapadmin.pagination.connections", {products.db: conn}):
            assert pg_estimated_count(products) is None


# ── EstimatedCountPaginator ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestPaginator:
    def test_exact_count_on_sqlite(self, products):
        pag = EstimatedCountPaginator(products, per_page=2)
        assert pag.count == 5  # real count, not an estimate

    def test_uses_estimate_above_threshold(self, products):
        with patch("snapadmin.pagination.pg_estimated_count", return_value=5_000_000):
            pag = EstimatedCountPaginator(products, per_page=2)
            assert pag.count == 5_000_000

    def test_estimate_below_threshold_falls_back_to_exact(self, products):
        # Estimate (10) is under the 100k threshold → exact count (5) wins.
        with patch("snapadmin.pagination.pg_estimated_count", return_value=10):
            pag = EstimatedCountPaginator(products, per_page=2)
            assert pag.count == 5

    @override_settings(SNAPADMIN_ESTIMATED_COUNT=False)
    def test_kill_switch_forces_exact(self, products):
        with patch("snapadmin.pagination.pg_estimated_count", return_value=5_000_000) as m:
            pag = EstimatedCountPaginator(products, per_page=2)
            assert pag.count == 5
            m.assert_not_called()

    @override_settings(SNAPADMIN_ESTIMATED_COUNT_THRESHOLD=3)
    def test_custom_threshold(self, products):
        with patch("snapadmin.pagination.pg_estimated_count", return_value=4):
            pag = EstimatedCountPaginator(products, per_page=2)
            assert pag.count == 4  # 4 >= threshold 3


# ── wired into generated admin ───────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminWiring:
    def test_admin_uses_estimated_paginator(self):
        from demo.apps.shop.models import Product
        assert site._registry[Product].paginator is EstimatedCountPaginator

    def test_changelist_still_renders(self, admin_user, client, products):
        client.force_login(admin_user)
        from django.urls import reverse
        r = client.get(reverse("admin:demo_product_changelist"))
        assert r.status_code == 200
