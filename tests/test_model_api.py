"""
tests/test_model_api.py  –  Dynamic model CRUD API + schema endpoint tests
"""

from decimal import Decimal
import pytest
from rest_framework.test import APIClient


# ── DynamicModelViewSet – Product ─────────────────────────────────────────────

@pytest.mark.django_db
class TestProductCRUD:
    def test_list_returns_200(self, auth_client, product):
        assert auth_client.get("/api/models/demo/Product/").status_code == 200

    def test_list_contains_product(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/")
        names = [item["name"] for item in r.json()["results"]]
        assert product.name in names

    def test_list_pagination_fields(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/")
        assert "count" in r.json()
        assert "results" in r.json()

    def test_list_pagination_second_page(self, auth_client, many_products):
        r1 = auth_client.get("/api/models/demo/Product/?page=1")
        r2 = auth_client.get("/api/models/demo/Product/?page=2")
        assert r1.status_code == 200
        assert r2.status_code == 200
        ids1 = {p["id"] for p in r1.json()["results"]}
        ids2 = {p["id"] for p in r2.json()["results"]}
        assert ids1.isdisjoint(ids2)  # no overlap between pages

    def test_retrieve_product(self, auth_client, product):
        r = auth_client.get(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 200
        assert r.json()["id"] == product.pk
        assert r.json()["name"] == product.name

    def test_create_product_201(self, auth_client):
        r = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "New Item", "price": "29.99", "available": True},
            format="json",
        )
        assert r.status_code == 201
        assert r.json()["name"] == "New Item"

    def test_create_product_persisted(self, auth_client):
        from demo.models import Product
        auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Persisted Item", "price": "5.00", "available": False},
            format="json",
        )
        assert Product.objects.filter(name="Persisted Item").exists()

    def test_update_product_patch(self, auth_client, product):
        r = auth_client.patch(
            f"/api/models/demo/Product/{product.pk}/",
            {"available": False},
            format="json",
        )
        assert r.status_code == 200
        assert r.json()["available"] is False

    def test_update_persisted(self, auth_client, product):
        from demo.models import Product
        auth_client.patch(
            f"/api/models/demo/Product/{product.pk}/",
            {"name": "Updated Name"},
            format="json",
        )
        product.refresh_from_db()
        assert product.name == "Updated Name"

    def test_delete_product_204(self, auth_client, product):
        assert auth_client.delete(f"/api/models/demo/Product/{product.pk}/").status_code == 204

    def test_delete_removes_from_db(self, auth_client, product):
        from demo.models import Product
        auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert not Product.objects.filter(pk=product.pk).exists()

    def test_retrieve_nonexistent_returns_404(self, auth_client):
        assert auth_client.get("/api/models/demo/Product/999999/").status_code == 404

    def test_unknown_model_returns_404(self, auth_client):
        assert auth_client.get("/api/models/demo/GhostModel/").status_code == 404

    def test_unknown_app_returns_404(self, auth_client):
        assert auth_client.get("/api/models/ghost_app/Product/").status_code == 404


# ── DynamicModelViewSet – Customer ───────────────────────────────────────────

@pytest.mark.django_db
class TestCustomerCRUD:
    def test_list_customers(self, auth_client, customer):
        r = auth_client.get("/api/models/demo/Customer/")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_create_customer(self, auth_client):
        r = auth_client.post(
            "/api/models/demo/Customer/",
            {"first_name": "Eve", "last_name": "Turner", "origin": "status_c", "active": True},
            format="json",
        )
        assert r.status_code == 201
        assert r.json()["first_name"] == "Eve"

    def test_retrieve_customer(self, auth_client, customer):
        r = auth_client.get(f"/api/models/demo/Customer/{customer.pk}/")
        assert r.status_code == 200
        assert r.json()["first_name"] == "Alice"

    def test_update_customer_active_status(self, auth_client, customer_inactive):
        r = auth_client.patch(
            f"/api/models/demo/Customer/{customer_inactive.pk}/",
            {"active": True},
            format="json",
        )
        assert r.status_code == 200
        assert r.json()["active"] is True


# ── DynamicModelViewSet – Order ──────────────────────────────────────────────

@pytest.mark.django_db
class TestOrderCRUD:
    def test_list_orders(self, auth_client, order):
        r = auth_client.get("/api/models/demo/Order/")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_retrieve_order(self, auth_client, order):
        r = auth_client.get(f"/api/models/demo/Order/{order.pk}/")
        assert r.status_code == 200
        assert r.json()["total"] == "99.99"

    def test_order_has_customer_field(self, auth_client, order, customer):
        r = auth_client.get(f"/api/models/demo/Order/{order.pk}/")
        assert r.json()["customer"] == customer.pk


# ── Token scope enforcement ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenScopeEnforcement:
    def test_restricted_token_can_access_product(self, db, restricted_token):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {restricted_token.token_key}")
        r = c.get("/api/models/demo/Product/")
        assert r.status_code == 200

    def test_restricted_token_blocked_from_customer(self, db, restricted_token):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {restricted_token.token_key}")
        r = c.get("/api/models/demo/Customer/")
        assert r.status_code in (403, 404)

    def test_unauthenticated_denied(self, anon_client):
        assert anon_client.get("/api/models/demo/Product/").status_code in (401, 403)


# ── ModelSchemaView ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestModelSchemaView:
    def test_schema_returns_200(self, auth_client):
        assert auth_client.get("/api/models/schema/").status_code == 200

    def test_schema_lists_snap_models(self, auth_client):
        r = auth_client.get("/api/models/schema/")
        model_names = [m["model_name"] for m in r.json()["models"]]
        assert "Product" in model_names
        assert "Customer" in model_names
        assert "Order" in model_names

    def test_schema_has_count_field(self, auth_client):
        r = auth_client.get("/api/models/schema/")
        assert "count" in r.json()

    def test_schema_entries_have_endpoint_url(self, auth_client):
        r = auth_client.get("/api/models/schema/")
        for m in r.json()["models"]:
            assert "endpoint" in m
            assert "/api/models/" in m["endpoint"]

    def test_schema_entries_have_fields_list(self, auth_client):
        r = auth_client.get("/api/models/schema/")
        product_entry = next(m for m in r.json()["models"] if m["model_name"] == "Product")
        assert "fields" in product_entry
        field_names = [f["name"] for f in product_entry["fields"]]
        assert "name" in field_names
        assert "price" in field_names

    def test_schema_respects_token_restriction(self, db, restricted_token):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {restricted_token.token_key}")
        r = c.get("/api/models/schema/")
        model_names = [m["model_name"] for m in r.json()["models"]]
        assert "Product" in model_names
        assert "Customer" not in model_names
        assert "Order" not in model_names

    def test_schema_unauthenticated_denied(self, anon_client):
        assert anon_client.get("/api/models/schema/").status_code in (401, 403)

    def test_schema_verbose_names_present(self, auth_client):
        r = auth_client.get("/api/models/schema/")
        product_entry = next(m for m in r.json()["models"] if m["model_name"] == "Product")
        assert "verbose_name" in product_entry
        assert "verbose_name_plural" in product_entry


# ── Serializer factory ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSerializerFactory:
    def test_get_serializer_for_product(self):
        from api.serializers import get_serializer_for_model
        cls = get_serializer_for_model("demo", "Product")
        assert cls is not None

    def test_serializer_cached(self):
        from api.serializers import get_serializer_for_model
        cls1 = get_serializer_for_model("demo", "Customer")
        cls2 = get_serializer_for_model("demo", "Customer")
        assert cls1 is cls2  # same object due to caching

    def test_unknown_model_raises(self):
        from api.serializers import get_serializer_for_model
        with pytest.raises(LookupError):
            get_serializer_for_model("demo", "DoesNotExist")
