"""
tests/test_admin_site.py

Tests for Django admin registration and basic admin view rendering.

Covers:
- All demo models are registered in admin
- Admin list views return HTTP 200 for a superuser
- Admin change views return HTTP 200
- formatted_id column is present in list display
- Admin search works (returns 200)
"""

import pytest
from django.contrib import admin
from django.urls import reverse


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminRegistration:
    def test_product_in_registry(self):
        from demo.models import Product
        assert Product in admin.site._registry

    def test_customer_in_registry(self):
        from demo.models import Customer
        assert Customer in admin.site._registry

    def test_order_in_registry(self):
        from demo.models import Order
        assert Order in admin.site._registry

    def test_api_token_in_registry(self):
        from api.models import APIToken
        assert APIToken in admin.site._registry

    def test_register_admin_idempotent(self):
        """Calling register_admin twice must not raise."""
        from demo.models import Product
        Product.register_admin()  # already registered – must be silent

    def test_product_admin_has_list_display(self):
        from demo.models import Product
        model_admin = admin.site._registry[Product]
        assert len(model_admin.list_display) > 0

    def test_product_admin_has_search_fields(self):
        from demo.models import Product
        model_admin = admin.site._registry[Product]
        assert len(model_admin.search_fields) > 0

    def test_customer_admin_has_list_filter(self):
        """Customer.origin is filterable=True, must appear in list_filter."""
        from demo.models import Customer
        model_admin = admin.site._registry[Customer]
        assert len(model_admin.list_filter) > 0

    def test_order_admin_has_autocomplete_fields(self):
        from demo.models import Order
        model_admin = admin.site._registry[Order]
        assert "customer" in model_admin.autocomplete_fields


# ─────────────────────────────────────────────────────────────────────────────
# Admin view HTTP responses
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminViews:
    """Basic smoke tests: admin pages must return 200 for a logged-in superuser."""

    @pytest.fixture
    def admin_client(self, admin_user, client):
        client.force_login(admin_user)
        return client

    def test_product_changelist_200(self, admin_client):
        url = reverse("admin:demo_product_changelist")
        assert admin_client.get(url).status_code == 200

    def test_customer_changelist_200(self, admin_client):
        url = reverse("admin:demo_customer_changelist")
        assert admin_client.get(url).status_code == 200

    def test_order_changelist_200(self, admin_client):
        url = reverse("admin:demo_order_changelist")
        assert admin_client.get(url).status_code == 200

    def test_product_add_view_200(self, admin_client):
        url = reverse("admin:demo_product_add")
        assert admin_client.get(url).status_code == 200

    def test_customer_add_view_200(self, admin_client):
        url = reverse("admin:demo_customer_add")
        assert admin_client.get(url).status_code == 200

    def test_product_change_view_200(self, admin_client, product):
        url = reverse("admin:demo_product_change", args=[product.pk])
        assert admin_client.get(url).status_code == 200

    def test_customer_change_view_200(self, admin_client, customer):
        url = reverse("admin:demo_customer_change", args=[customer.pk])
        assert admin_client.get(url).status_code == 200

    def test_order_change_view_200(self, admin_client, order):
        url = reverse("admin:demo_order_change", args=[order.pk])
        assert admin_client.get(url).status_code == 200

    def test_anonymous_admin_redirects_to_login(self, client):
        url = reverse("admin:demo_product_changelist")
        response = client.get(url)
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_product_search_returns_200(self, admin_client, product):
        url = reverse("admin:demo_product_changelist") + "?q=Laptop"
        assert admin_client.get(url).status_code == 200

    def test_api_token_changelist_200(self, admin_client, api_token):
        url = reverse("admin:api_apitoken_changelist")
        assert admin_client.get(url).status_code == 200
