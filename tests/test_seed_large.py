"""
tests/test_seed_large.py

Tests for the seed_large and benchmark_list_view management commands
(roadmap task #S). Small --count values keep the suite fast while still
exercising batching, FK linkage, flush behaviour, and the benchmark output.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db(transaction=True)
class TestSeedLargeCommand:
    def _call(self, *args, **kwargs):
        out = StringIO()
        kwargs.setdefault("stdout", out)
        call_command("seed_large", *args, **kwargs)
        return out.getvalue()

    def test_creates_requested_customer_count(self):
        from demo.app.models import Customer
        self._call(count=50, batch_size=10)
        assert Customer.objects.count() == 50

    def test_creates_requested_order_count(self):
        from demo.app.models import Order
        self._call(count=50, batch_size=10)
        assert Order.objects.count() == 50

    def test_batch_size_smaller_than_count(self):
        # Forces multiple bulk_create batches.
        from demo.app.models import Customer
        self._call(count=25, batch_size=7)
        assert Customer.objects.count() == 25

    def test_batch_size_larger_than_count(self):
        from demo.app.models import Customer
        self._call(count=5, batch_size=100)
        assert Customer.objects.count() == 5

    def test_orders_linked_to_real_customers(self):
        from demo.app.models import Order
        self._call(count=20, batch_size=5)
        customer_ids = set()
        for o in Order.objects.all():
            assert o.customer_id is not None
            customer_ids.add(o.customer_id)
        # Round-robin assignment should spread across customers.
        assert len(customer_ids) > 1

    def test_flush_resets_to_exact_count(self):
        from demo.app.models import Customer, Order
        self._call(count=15, batch_size=5)
        self._call(count=10, batch_size=5, flush=True)
        assert Customer.objects.count() == 10
        assert Order.objects.count() == 10

    def test_no_index_flag_accepted(self):
        # --no-index is a parity no-op; just verify it doesn't error.
        from demo.app.models import Customer
        self._call(count=5, batch_size=5, no_index=True)
        assert Customer.objects.count() == 5

    def test_zero_count_raises(self):
        with pytest.raises(CommandError):
            self._call(count=0)

    def test_negative_batch_size_raises(self):
        with pytest.raises(CommandError):
            self._call(count=5, batch_size=-1)

    def test_output_reports_completion(self):
        out = self._call(count=5, batch_size=5)
        assert "Large seed complete" in out or "✅" in out


@pytest.mark.django_db(transaction=True)
class TestBenchmarkListViewCommand:
    def test_runs_and_reports_query_counts(self):
        from demo.app.models import Customer, Order
        call_command("seed_large", count=20, batch_size=10, stdout=StringIO())
        out = StringIO()
        call_command("benchmark_list_view", model="order", stdout=out)
        text = out.getvalue()
        assert "Benchmark" in text
        assert "queries" in text
        assert "WITHOUT" in text and "WITH" in text

    def test_respects_limit(self):
        call_command("seed_large", count=30, batch_size=10, stdout=StringIO())
        out = StringIO()
        call_command("benchmark_list_view", model="order", limit=5, stdout=out)
        assert "Result" in out.getvalue()

    def test_unknown_model_raises(self):
        with pytest.raises(CommandError):
            call_command("benchmark_list_view", model="nonexistent", stdout=StringIO())

    def test_model_without_select_related_warns(self):
        # Customer has no FK columns → list_select_related is False.
        call_command("seed_large", count=10, batch_size=5, stdout=StringIO())
        out = StringIO()
        call_command("benchmark_list_view", model="customer", stdout=out)
        assert "no list_select_related" in out.getvalue()
