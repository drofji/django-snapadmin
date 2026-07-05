"""
Tests for large-dataset / performance tuning (roadmap task #0).

SnapModel exposes Django admin's list-view performance knobs as class
attributes (``list_per_page``, ``list_max_show_all``, ``show_full_result_count``)
and — more importantly — auto-derives ``list_select_related`` from the
ForeignKey columns shown in the list view so related columns never trigger the
classic admin N+1 query storm. These tests verify the wiring, the
auto-derivation, the per-model overrides, and the actual query-count win.
"""

from decimal import Decimal

import pytest
from django.contrib import admin
from django.db import connection
from django.test.utils import CaptureQueriesContext


def _admin_for(model):
    return admin.site._registry[model]


class TestListViewKnobDefaults:
    def test_defaults_flow_onto_registered_admin(self):
        from demo.models import Product

        model_admin = _admin_for(Product)
        assert model_admin.list_per_page == 100
        assert model_admin.list_max_show_all == 200
        assert model_admin.show_full_result_count is True

    def test_snapmodel_default_attributes(self):
        from snapadmin.models import SnapModel

        assert SnapModel.list_per_page == 100
        assert SnapModel.list_max_show_all == 200
        assert SnapModel.show_full_result_count is True


class TestListSelectRelatedAutoDerivation:
    def test_fk_column_is_joined(self):
        # Order shows its `customer` FK in the list view.
        from demo.models import Order

        assert _admin_for(Order).list_select_related == ["customer"]

    def test_multiple_fk_columns_are_joined(self):
        from demo.models import OrderItem

        sr = _admin_for(OrderItem).list_select_related
        assert set(sr) == {"order", "product"}

    def test_single_fk_column_is_joined(self):
        from demo.models import Product

        assert _admin_for(Product).list_select_related == ["category"]

    def test_model_without_fk_columns_disables_select_related(self):
        # No FK columns → False, never an empty list (Django treats [] as "all").
        from demo.models import Category, Customer

        assert _admin_for(Category).list_select_related is False
        assert _admin_for(Customer).list_select_related is False


class TestPerModelOverride:
    def test_class_attribute_overrides_flow_through(self):
        """Re-registering a model with custom knobs propagates them to the admin."""
        from demo.models import Category

        original = (
            Category.list_per_page,
            Category.list_max_show_all,
            Category.show_full_result_count,
        )
        try:
            Category.list_per_page = 25
            Category.list_max_show_all = 50
            Category.show_full_result_count = False

            admin.site.unregister(Category)
            Category.register_admin()

            model_admin = _admin_for(Category)
            assert model_admin.list_per_page == 25
            assert model_admin.list_max_show_all == 50
            assert model_admin.show_full_result_count is False
        finally:
            (
                Category.list_per_page,
                Category.list_max_show_all,
                Category.show_full_result_count,
            ) = original
            admin.site.unregister(Category)
            Category.register_admin()


class TestSelectRelatedEliminatesNPlusOne:
    def test_fk_access_is_a_single_query(self, db):
        from demo.models import Customer, Order

        for i in range(10):
            cust = Customer.objects.create(
                first_name=f"C{i}", last_name="Test",
                email=f"c{i}@example.com", origin="status_a", active=True,
            )
            Order.objects.create(customer=cust, total=Decimal("9.99"))

        model_admin = _admin_for(Order)
        qs = Order.objects.select_related(*model_admin.list_select_related)

        with CaptureQueriesContext(connection) as ctx:
            for order in qs:
                str(order.customer)  # would be one query each without the join

        assert len(ctx) == 1

    def test_without_select_related_triggers_n_plus_1(self, db):
        # Contrast case: proves the optimization is what avoids the extra queries.
        from demo.models import Customer, Order

        for i in range(10):
            cust = Customer.objects.create(
                first_name=f"D{i}", last_name="Test",
                email=f"d{i}@example.com", origin="status_a", active=True,
            )
            Order.objects.create(customer=cust, total=Decimal("9.99"))

        with CaptureQueriesContext(connection) as ctx:
            for order in Order.objects.all():
                str(order.customer)

        # 1 query for the orders + 1 per customer access.
        assert len(ctx) > 1
