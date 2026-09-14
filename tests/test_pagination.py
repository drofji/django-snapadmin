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


def _running_on_postgres() -> bool:
    """Whether this run's default database is PostgreSQL.

    The estimate is a PostgreSQL feature, so half the behaviour in this file
    only exists on one backend. The suite runs on SQLite by default and on
    PostgreSQL in its own CI job, and each half says which one it needs rather
    than assuming (#QA1f).
    """
    from django.conf import settings

    return "postgresql" in settings.DATABASES["default"]["ENGINE"]


_ON_POSTGRES = _running_on_postgres()


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

    @pytest.mark.skipif(_ON_POSTGRES, reason="this is the non-PostgreSQL branch")
    def test_a_non_postgres_backend_returns_none(self, products):
        """Anything but PostgreSQL has no ``reltuples`` to read, so: exact count."""
        from django.db import connections

        assert connections[products.db].vendor != "postgresql"
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


# ── pg_estimated_count() against a real PostgreSQL ───────────────────────────


@pytest.mark.skipif(not _ON_POSTGRES, reason="needs a real PostgreSQL")
@pytest.mark.django_db
class TestPgEstimateAgainstTheRealPlanner:
    """The query itself, run by PostgreSQL rather than described to a mock.

    Every other test in this file patches ``connections`` and hands the code a
    fetched row, so ``SELECT reltuples::bigint FROM pg_class WHERE relname = %s``
    had never actually executed: a wrong catalogue column, a wrong cast or a
    table name that does not match ``relname`` would have passed all of them.
    These run only in the job that has a PostgreSQL service (#QA1f).
    """

    def test_an_analysed_table_reports_the_planner_s_row_estimate(self, products):
        from django.db import connections

        from demo.apps.shop.models import Product

        # reltuples is populated by ANALYZE; a table that has never been
        # analysed carries -1 ("unknown") and is deliberately ignored below.
        with connections[products.db].cursor() as cursor:
            cursor.execute(f'ANALYZE "{Product._meta.db_table}"')

        estimate = pg_estimated_count(Product.objects.all())

        assert estimate == Product.objects.count()

    def test_a_negative_estimate_is_treated_as_no_estimate(self):
        """``-1`` is PostgreSQL for "never analysed" — fall back to exact.

        Reading it as a row count would report a negative total. (A table
        created but not yet analysed reads **0** rather than -1 on this server,
        which the threshold check below already keeps harmless; -1 has to be
        forced here to reach the guard.)
        """
        from django.db import connections

        from demo.apps.shop.models import Showcase

        with connections["default"].cursor() as cursor:
            cursor.execute(
                "UPDATE pg_class SET reltuples = -1 WHERE relname = %s",
                [Showcase._meta.db_table],
            )

        assert pg_estimated_count(Showcase.objects.all()) is None

    def test_a_filtered_queryset_still_takes_the_exact_count(self, products):
        """``reltuples`` is whole-table, so a WHERE clause makes it wrong."""
        from django.db import connections

        from demo.apps.shop.models import Product

        with connections["default"].cursor() as cursor:
            cursor.execute(f'ANALYZE "{Product._meta.db_table}"')

        assert pg_estimated_count(Product.objects.filter(name="P1")) is None

    def test_the_paginator_uses_the_estimate_once_it_clears_the_threshold(self, products):
        from django.db import connections

        from demo.apps.shop.models import Product

        with connections["default"].cursor() as cursor:
            cursor.execute(f'ANALYZE "{Product._meta.db_table}"')

        with override_settings(SNAPADMIN_ESTIMATED_COUNT_THRESHOLD=1):
            paginator = EstimatedCountPaginator(Product.objects.all(), per_page=2)
            assert paginator.count == Product.objects.count()


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
