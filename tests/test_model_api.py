"""
tests/test_model_api.py  –  Dynamic model CRUD API + schema endpoint tests
"""

from decimal import Decimal
import pytest
from django.test import override_settings
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
        from demo.apps.shop.models import Product
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
        from demo.apps.shop.models import Product
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
        from demo.apps.shop.models import Product
        auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert not Product.objects.filter(pk=product.pk).exists()

    def test_retrieve_nonexistent_returns_404(self, auth_client):
        assert auth_client.get("/api/models/demo/Product/999999/").status_code == 404

    def test_unknown_model_returns_404(self, auth_client):
        assert auth_client.get("/api/models/demo/GhostModel/").status_code == 404

    def test_unknown_app_returns_404(self, auth_client):
        assert auth_client.get("/api/models/ghost_app/Product/").status_code == 404


# ── DynamicModelViewSet – non-SnapModel models are not exposed ──────────────

@pytest.mark.django_db
class TestNonSnapModelRejected:
    def test_list_non_snapmodel_returns_404(self, auth_client):
        assert auth_client.get("/api/models/auth/User/").status_code == 404

    def test_retrieve_non_snapmodel_returns_404(self, auth_client, admin_user):
        r = auth_client.get(f"/api/models/auth/User/{admin_user.pk}/")
        assert r.status_code == 404

    def test_create_non_snapmodel_returns_404(self, auth_client):
        r = auth_client.post(
            "/api/models/auth/User/",
            {"username": "hacker", "password": "whatever"},
            format="json",
        )
        assert r.status_code == 404

    def test_delete_non_snapmodel_returns_404(self, auth_client, admin_user):
        r = auth_client.delete(f"/api/models/auth/User/{admin_user.pk}/")
        assert r.status_code == 404

    def test_update_non_snapmodel_returns_404(self, auth_client, admin_user):
        r = auth_client.patch(
            f"/api/models/auth/User/{admin_user.pk}/",
            {"is_superuser": True},
            format="json",
        )
        assert r.status_code == 404

    def test_get_model_class_returns_none_for_non_snapmodel(self):
        from snapadmin.api.views import DynamicModelViewSet
        view = DynamicModelViewSet()
        view.kwargs = {"app_label": "auth", "model_name": "User"}
        assert view._get_model_class() is None

    def test_get_model_class_returns_model_for_real_snapmodel(self):
        from snapadmin.api.views import DynamicModelViewSet
        from demo.apps.shop.models import Product
        view = DynamicModelViewSet()
        view.kwargs = {"app_label": "demo", "model_name": "Product"}
        assert view._get_model_class() is Product


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
        from snapadmin.api.serializers import get_serializer_for_model
        cls = get_serializer_for_model("demo", "Product")
        assert cls is not None

    def test_serializer_cached(self):
        from snapadmin.api.serializers import get_serializer_for_model
        cls1 = get_serializer_for_model("demo", "Customer")
        cls2 = get_serializer_for_model("demo", "Customer")
        assert cls1 is cls2  # same object due to caching

    def test_unknown_model_raises(self):
        from snapadmin.api.serializers import get_serializer_for_model
        with pytest.raises(LookupError):
            get_serializer_for_model("demo", "DoesNotExist")


# ── DynamicModelViewSet – count + streaming export (no-Celery bulk helpers) ────

def _ndjson_rows(response):
    """Decode a StreamingHttpResponse NDJSON body into a list of dicts."""
    import json
    body = b"".join(response.streaming_content).decode()
    return [json.loads(line) for line in body.splitlines() if line]


@pytest.mark.django_db
class TestModelCount:
    def test_count_returns_total(self, auth_client, many_products):
        r = auth_client.get("/api/models/demo/Product/count/")
        assert r.status_code == 200
        assert r.json() == {"count": 30}

    def test_count_honours_filters(self, auth_client, product, product_unavailable):
        # Two products exist; only one is available.
        r = auth_client.get("/api/models/demo/Product/count/?available=true")
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_count_carries_backend_header(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/count/")
        assert r["X-Snap-Query-Backend"] == "database"

    def test_count_unknown_model_404(self, auth_client):
        assert auth_client.get("/api/models/demo/GhostModel/count/").status_code == 404

    def test_count_unauthenticated_denied(self, anon_client):
        assert anon_client.get("/api/models/demo/Product/count/").status_code in (401, 403)


@pytest.mark.django_db
class TestModelExport:
    def test_export_streams_all_rows(self, auth_client, many_products):
        r = auth_client.get("/api/models/demo/Product/export/")
        assert r.status_code == 200
        assert r["Content-Type"] == "application/x-ndjson"
        rows = _ndjson_rows(r)
        assert len(rows) == 30
        assert all("id" in row and "name" in row for row in rows)

    def test_export_content_disposition(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/export/")
        assert r["Content-Disposition"] == 'attachment; filename="demo_product.ndjson"'

    def test_export_respects_limit(self, auth_client, many_products):
        r = auth_client.get("/api/models/demo/Product/export/?limit=5")
        assert len(_ndjson_rows(r)) == 5

    def test_export_honours_filters(self, auth_client, product, product_unavailable):
        r = auth_client.get("/api/models/demo/Product/export/?available=true")
        rows = _ndjson_rows(r)
        assert len(rows) == 1
        assert rows[0]["name"] == product.name

    def test_export_garbled_limit_streams_all(self, auth_client, many_products):
        # Non-numeric, blank or absent caps must not silently truncate.
        for bad in ("abc", ""):
            rows = _ndjson_rows(
                auth_client.get(f"/api/models/demo/Product/export/?limit={bad}")
            )
            assert len(rows) == 30, bad

    def test_export_nonpositive_limit_rejected(self, auth_client, many_products):
        # An explicit zero/negative limit is a caller mistake, not a garbled
        # value: it is rejected outright rather than silently streaming
        # everything (see _parse_export_limit's docstring for the rationale).
        for bad in ("0", "-3"):
            r = auth_client.get(f"/api/models/demo/Product/export/?limit={bad}")
            assert r.status_code == 400, bad
            assert "detail" in r.json()

    def test_export_carries_backend_header(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/export/")
        assert r["X-Snap-Query-Backend"] == "database"

    def test_export_unknown_model_404(self, auth_client):
        assert auth_client.get("/api/models/demo/GhostModel/export/").status_code == 404

    def test_export_unauthenticated_denied(self, anon_client):
        assert anon_client.get("/api/models/demo/Product/export/").status_code in (401, 403)

    @override_settings(SNAPADMIN_EXPORT_MAX_ROWS=10)
    def test_export_row_ceiling_blocks_unbounded_export(self, auth_client, many_products):
        r = auth_client.get("/api/models/demo/Product/export/")
        assert r.status_code == 413
        body = r.json()
        assert body["count"] == 30
        assert body["max_rows"] == 10
        assert "/api/exports/" in body["async_export_endpoint"]
        assert "/api/exports/" in body["detail"]

    @override_settings(SNAPADMIN_EXPORT_MAX_ROWS=10)
    def test_export_row_ceiling_allows_explicit_limit(self, auth_client, many_products):
        # A valid, explicit ?limit= is the caller opting into a bounded
        # response themselves — it is honoured even though it's below the
        # ceiling's trigger threshold (the ceiling only guards the
        # "no limit passed" case).
        r = auth_client.get("/api/models/demo/Product/export/?limit=20")
        assert r.status_code == 200
        assert len(_ndjson_rows(r)) == 20

    @override_settings(SNAPADMIN_EXPORT_MAX_ROWS=10)
    def test_export_row_ceiling_allows_within_bound(self, auth_client, product, product_unavailable):
        # Only 2 rows match; well under the configured ceiling of 10.
        r = auth_client.get("/api/models/demo/Product/export/")
        assert r.status_code == 200
        assert len(_ndjson_rows(r)) == 2

    def test_export_row_ceiling_unset_is_unlimited(self, auth_client, many_products):
        # SNAPADMIN_EXPORT_MAX_ROWS defaults to 0 (unlimited) — no regression
        # for installs that never set it.
        r = auth_client.get("/api/models/demo/Product/export/")
        assert r.status_code == 200
        assert len(_ndjson_rows(r)) == 30

    @override_settings(SNAPADMIN_EXPORT_LIMIT_MAX=5)
    def test_export_limit_clamped_to_hard_maximum(self, auth_client, many_products):
        r = auth_client.get("/api/models/demo/Product/export/?limit=25")
        assert r.status_code == 200
        assert len(_ndjson_rows(r)) == 5

    def test_export_limit_unclamped_by_default(self, auth_client, many_products):
        # SNAPADMIN_EXPORT_LIMIT_MAX defaults to 0 (no clamp) — an explicit
        # large ?limit= is still honoured as-is.
        r = auth_client.get("/api/models/demo/Product/export/?limit=25")
        assert r.status_code == 200
        assert len(_ndjson_rows(r)) == 25


# ── DynamicModelViewSet – deletion-veto hook (#B12) ───────────────────────────

def _veto_all_deletes(request, obj):
    """Test guard: forbid every deletion."""
    return False


def _veto_unavailable_products(request, obj):
    """Test guard receiving (request, instance): only available rows deletable."""
    return getattr(obj, "available", True)


@pytest.mark.django_db
class TestDeletionVeto:
    def test_delete_allowed_by_default(self, auth_client, product):
        # No hook override, no guard setting → the default path deletes (204).
        from demo.apps.shop.models import Product
        r = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 204
        assert not Product.objects.filter(pk=product.pk).exists()

    def test_model_hook_veto_returns_403(self, auth_client, product, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_can_delete", lambda self, request: False)
        r = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 403
        assert Product.objects.filter(pk=product.pk).exists()  # not deleted

    def test_setting_guard_veto_returns_403(self, auth_client, product):
        from demo.apps.shop.models import Product
        with override_settings(
            SNAPADMIN_API_DELETE_GUARD="tests.test_model_api._veto_all_deletes"
        ):
            r = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 403
        assert Product.objects.filter(pk=product.pk).exists()

    def test_setting_guard_receives_object(self, auth_client, product, product_unavailable):
        from demo.apps.shop.models import Product
        guard = "tests.test_model_api._veto_unavailable_products"
        with override_settings(SNAPADMIN_API_DELETE_GUARD=guard):
            # Unavailable product is vetoed by the object-aware guard …
            blocked = auth_client.delete(f"/api/models/demo/Product/{product_unavailable.pk}/")
            # … while an available one still deletes.
            allowed = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert blocked.status_code == 403
        assert allowed.status_code == 204
        assert Product.objects.filter(pk=product_unavailable.pk).exists()
        assert not Product.objects.filter(pk=product.pk).exists()

    def test_setting_guard_accepts_direct_callable(self, auth_client, product):
        from demo.apps.shop.models import Product
        with override_settings(SNAPADMIN_API_DELETE_GUARD=_veto_all_deletes):
            r = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 403
        assert Product.objects.filter(pk=product.pk).exists()

    def test_destroy_unknown_model_404(self, auth_client):
        assert auth_client.delete("/api/models/demo/GhostModel/1/").status_code == 404


# ── api_write_fields allowlist ────────────────────────────────────────────────

@pytest.fixture
def restricted_product_serializer(monkeypatch):
    """Product.api_write_fields = ["available"] for the duration of the test.

    Bypasses (and restores) the module-level serializer cache in
    ``snapadmin.api.serializers`` so the HTTP-level tests observe the patched
    attribute instead of an already-built, unrestricted serializer class.
    """
    from snapadmin.api import serializers as serializers_module
    from demo.apps.shop.models import Product

    monkeypatch.setattr(Product, "api_write_fields", ["available"], raising=False)
    serializers_module._serializer_cache.pop("demo.Product", None)
    yield
    serializers_module._serializer_cache.pop("demo.Product", None)


@pytest.mark.django_db
class TestApiWriteFieldsAllowlist:
    def test_default_unset_leaves_every_field_writable(self):
        from snapadmin.api.serializers import build_model_serializer
        from demo.apps.shop.models import Product
        fields = build_model_serializer(Product)().fields
        assert fields["name"].read_only is False
        assert fields["price"].read_only is False
        assert fields["available"].read_only is False

    def test_explicit_list_forces_other_fields_read_only(self, monkeypatch):
        from snapadmin.api.serializers import build_model_serializer
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["available"], raising=False)
        fields = build_model_serializer(Product)().fields
        assert fields["available"].read_only is False
        assert fields["name"].read_only is True
        assert fields["price"].read_only is True

    def test_non_writable_field_still_readable(self, monkeypatch):
        # Restricting writes must not narrow what api_exclude_fields controls.
        from snapadmin.api.serializers import build_model_serializer
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["available"], raising=False)
        fields = build_model_serializer(Product)().fields
        assert "name" in fields
        assert "price" in fields

    def test_pk_stays_read_only_even_if_listed(self, monkeypatch):
        from snapadmin.api.serializers import build_model_serializer
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["id"], raising=False)
        fields = build_model_serializer(Product)().fields
        assert fields["id"].read_only is True

    def test_update_ignores_non_allowlisted_field(
        self, auth_client, product, restricted_product_serializer
    ):
        original_name = product.name
        r = auth_client.patch(
            f"/api/models/demo/Product/{product.pk}/",
            {"name": "Hacked Name", "available": False},
            format="json",
        )
        assert r.status_code == 200
        assert r.json()["available"] is False
        product.refresh_from_db()
        assert product.name == original_name

    def test_update_still_persists_allowlisted_field(
        self, auth_client, product, restricted_product_serializer
    ):
        from demo.apps.shop.models import Product
        auth_client.patch(
            f"/api/models/demo/Product/{product.pk}/",
            {"available": False},
            format="json",
        )
        assert Product.objects.get(pk=product.pk).available is False

    def test_read_response_still_includes_non_allowlisted_field(
        self, auth_client, product, restricted_product_serializer
    ):
        r = auth_client.get(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 200
        assert r.json()["name"] == product.name


# ── auto-generated text-field filters — exact-by-default (#PERF1) ────────────

@pytest.fixture
def product_filterset_override(monkeypatch):
    """Product.api_filter_lookups = {"name": ["exact", "icontains"]} for the duration
    of the test.

    Bypasses (and restores) the module-level filterset cache in
    ``snapadmin.api.filters`` so the HTTP-level tests observe the patched attribute
    instead of an already-built, library-default FilterSet class.
    """
    from snapadmin.api import filters as filters_module
    from demo.apps.shop.models import Product

    monkeypatch.setattr(
        Product, "api_filter_lookups", {"name": ["exact", "icontains"]}, raising=False
    )
    filters_module._filterset_cache.pop("demo.product", None)
    yield
    filters_module._filterset_cache.pop("demo.product", None)


@pytest.mark.django_db
class TestAutoFilterTextFieldLookups:
    def test_bare_field_filter_is_exact_match(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop", price=Decimal("10.00"))
        Product.objects.create(name="Laptop Pro", price=Decimal("20.00"))

        r = auth_client.get("/api/models/demo/Product/?name=Laptop")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["results"]]
        assert names == ["Laptop"]

    def test_bare_field_filter_no_longer_matches_superstring(self, auth_client):
        # Old behaviour: icontains meant "?name=Laptop" also matched "Laptop Pro".
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop Pro", price=Decimal("20.00"))

        r = auth_client.get("/api/models/demo/Product/?name=Laptop")
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_icontains_suffix_performs_substring_match(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop", price=Decimal("10.00"))
        Product.objects.create(name="Laptop Pro", price=Decimal("20.00"))

        r = auth_client.get("/api/models/demo/Product/?name__icontains=Laptop")
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["results"]}
        assert names == {"Laptop", "Laptop Pro"}

    def test_startswith_suffix_available(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop", price=Decimal("10.00"))
        Product.objects.create(name="Desktop", price=Decimal("20.00"))

        r = auth_client.get("/api/models/demo/Product/?name__startswith=Lap")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["results"]]
        assert names == ["Laptop"]

    def test_in_suffix_accepts_comma_separated_values(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop", price=Decimal("10.00"))
        Product.objects.create(name="Desktop", price=Decimal("20.00"))
        Product.objects.create(name="Tablet", price=Decimal("30.00"))

        r = auth_client.get("/api/models/demo/Product/?name__in=Laptop,Tablet")
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["results"]}
        assert names == {"Laptop", "Tablet"}

    def test_model_override_applies_only_configured_lookups(
        self, product_filterset_override
    ):
        from snapadmin.api.filters import build_filterset_for_model
        from demo.apps.shop.models import Product

        filterset_cls = build_filterset_for_model(Product)
        name_keys = {
            k for k in filterset_cls.base_filters if k == "name" or k.startswith("name__")
        }
        assert name_keys == {"name", "name__icontains"}

        # A field with no per-model override still gets the library default set.
        description_keys = {
            k for k in filterset_cls.base_filters
            if k == "description" or k.startswith("description__")
        }
        assert description_keys == {
            "description", "description__icontains", "description__startswith", "description__in",
        }

    def test_model_without_override_uses_library_default_for_every_text_field(self):
        from snapadmin.api.filters import build_filterset_for_model
        from demo.apps.shop.models import Product

        filterset_cls = build_filterset_for_model(Product)
        name_keys = {
            k for k in filterset_cls.base_filters if k == "name" or k.startswith("name__")
        }
        assert name_keys == {"name", "name__icontains", "name__startswith", "name__in"}

    def test_cache_stays_independent_per_model_despite_override(
        self, product_filterset_override
    ):
        from snapadmin.api.filters import build_filterset_for_model
        from demo.apps.shop.models import Product, Customer

        product_fs = build_filterset_for_model(Product)
        customer_fs = build_filterset_for_model(Customer)

        product_name_keys = {
            k for k in product_fs.base_filters if k == "name" or k.startswith("name__")
        }
        customer_email_keys = {
            k for k in customer_fs.base_filters if k == "email" or k.startswith("email__")
        }
        assert product_name_keys == {"name", "name__icontains"}
        assert customer_email_keys == {
            "email", "email__icontains", "email__startswith", "email__in",
        }


# ── global / model-wide text-lookup defaults (#FEAT8a) ───────────────────────

@pytest.mark.django_db
class TestGlobalTextLookupDefaults:
    """Drop `icontains` once — per-model (api_default_text_lookups) or project-wide
    (SNAPADMIN_API_TEXT_LOOKUPS) — instead of enumerating every column, while a
    per-field api_filter_lookups override still wins."""

    @staticmethod
    def _fresh_filterset(model):
        # The FilterSet is cached per model; pop it so this build observes the
        # patched attribute / overridden setting, and leave the cache clean after.
        from snapadmin.api import filters as fm
        key = f"{model._meta.app_label}.{model._meta.model_name}"
        fm._filterset_cache.pop(key, None)
        try:
            return fm.build_filterset_for_model(model)
        finally:
            fm._filterset_cache.pop(key, None)

    @staticmethod
    def _text_keys(filterset_cls, field):
        return {
            k for k in filterset_cls.base_filters if k == field or k.startswith(f"{field}__")
        }

    def test_project_setting_narrows_every_text_field(self):
        from demo.apps.shop.models import Product
        with override_settings(SNAPADMIN_API_TEXT_LOOKUPS=["exact", "startswith"]):
            fs = self._fresh_filterset(Product)
        assert self._text_keys(fs, "name") == {"name", "name__startswith"}
        assert self._text_keys(fs, "description") == {"description", "description__startswith"}

    def test_model_attr_narrows_every_text_field(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_default_text_lookups", ["exact", "in"], raising=False)
        fs = self._fresh_filterset(Product)
        assert self._text_keys(fs, "name") == {"name", "name__in"}
        assert self._text_keys(fs, "description") == {"description", "description__in"}

    def test_model_attr_wins_over_project_setting(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_default_text_lookups", ["exact", "startswith"], raising=False)
        with override_settings(SNAPADMIN_API_TEXT_LOOKUPS=["exact", "in"]):
            fs = self._fresh_filterset(Product)
        assert self._text_keys(fs, "name") == {"name", "name__startswith"}

    def test_per_field_lookups_win_over_both_defaults(self, monkeypatch):
        from demo.apps.shop.models import Product
        monkeypatch.setattr(Product, "api_default_text_lookups", ["exact"], raising=False)
        monkeypatch.setattr(
            Product, "api_filter_lookups", {"name": ["exact", "icontains"]}, raising=False
        )
        with override_settings(SNAPADMIN_API_TEXT_LOOKUPS=["exact", "startswith"]):
            fs = self._fresh_filterset(Product)
        # A per-field entry wins for `name`…
        assert self._text_keys(fs, "name") == {"name", "name__icontains"}
        # …while a field with no per-field entry uses the model-wide default.
        assert self._text_keys(fs, "description") == {"description"}

    def test_unset_everywhere_falls_back_to_library_default(self):
        from demo.apps.shop.models import Product
        # No per-field, no model attr, and SNAPADMIN_API_TEXT_LOOKUPS is None in the
        # test settings → the library default set.
        fs = self._fresh_filterset(Product)
        assert self._text_keys(fs, "name") == {
            "name", "name__icontains", "name__startswith", "name__in",
        }

    def test_demo_showcase_ships_an_index_friendly_default(self):
        # The demo wires api_default_text_lookups on Showcase (drops icontains).
        from demo.apps.shop.models import Showcase
        fs = self._fresh_filterset(Showcase)
        assert self._text_keys(fs, "char_field") == {
            "char_field", "char_field__startswith", "char_field__in",
        }

    def test_project_setting_drops_icontains_at_the_http_layer(self, auth_client):
        from snapadmin.api import filters as fm
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop Pro", price=Decimal("20.00"))
        Product.objects.create(name="Desktop", price=Decimal("30.00"))
        fm._filterset_cache.pop("demo.product", None)
        try:
            with override_settings(SNAPADMIN_API_TEXT_LOOKUPS=["exact", "startswith", "in"]):
                r = auth_client.get("/api/models/demo/Product/?name__icontains=Laptop")
            assert r.status_code == 200
            # icontains dropped project-wide → the param is unknown and ignored, so
            # BOTH rows come back (an active icontains filter would return only one).
            assert len(r.json()["results"]) == 2
        finally:
            fm._filterset_cache.pop("demo.product", None)


# ── auto-generated isnull / __in filters (#FEAT8b) ───────────────────────────

@pytest.mark.django_db
class TestIsNullAndInFilters:
    """`?field__isnull=true/false` must return 200 on text/numeric/date/FK (was a
    500 for text `isnull`, and numeric/date/FK had no `isnull`/`__in` at all), and
    numeric/FK gain a comma-separated `__in`."""

    @staticmethod
    def _fresh_filterset(model):
        from snapadmin.api import filters as fm
        key = f"{model._meta.app_label}.{model._meta.model_name}"
        fm._filterset_cache.pop(key, None)
        try:
            return fm.build_filterset_for_model(model)
        finally:
            fm._filterset_cache.pop(key, None)

    # ---- filterset construction -------------------------------------------------

    def test_numeric_field_gets_in_and_isnull(self):
        import django_filters
        from snapadmin.api.filters import _NumberInFilter
        from demo.apps.shop.models import Product
        fs = self._fresh_filterset(Product)
        assert isinstance(fs.base_filters["price__in"], _NumberInFilter)
        assert isinstance(fs.base_filters["price__isnull"], django_filters.BooleanFilter)
        assert fs.base_filters["price__isnull"].lookup_expr == "isnull"

    def test_date_field_gets_isnull_but_no_in(self):
        import django_filters
        from demo.apps.shop.models import Order
        fs = self._fresh_filterset(Order)
        assert isinstance(fs.base_filters["created_at__isnull"], django_filters.BooleanFilter)
        assert "created_at__in" not in fs.base_filters

    def test_fk_field_gets_id_in_and_isnull(self):
        import django_filters
        from snapadmin.api.filters import _NumberInFilter
        from demo.apps.shop.models import Product
        fs = self._fresh_filterset(Product)
        assert isinstance(fs.base_filters["category_id__in"], _NumberInFilter)
        assert isinstance(fs.base_filters["category_id__isnull"], django_filters.BooleanFilter)
        assert fs.base_filters["category_id__isnull"].field_name == "category_id"

    def test_text_isnull_maps_to_boolean_filter(self, monkeypatch):
        import django_filters
        from demo.apps.shop.models import Product
        monkeypatch.setattr(
            Product, "api_filter_lookups", {"name": ["exact", "isnull"]}, raising=False
        )
        fs = self._fresh_filterset(Product)
        assert isinstance(fs.base_filters["name__isnull"], django_filters.BooleanFilter)
        assert fs.base_filters["name__isnull"].lookup_expr == "isnull"

    # ---- HTTP behaviour ---------------------------------------------------------

    def test_text_isnull_returns_200_not_500(self, auth_client, monkeypatch):
        # Regression: a text field with `isnull` in its lookup set used to build a
        # CharFilter → the string "false" hit Django's isnull lookup → ValueError →
        # HTTP 500. It must now be a BooleanFilter → 200.
        from snapadmin.api import filters as fm
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop", price=Decimal("10.00"))
        monkeypatch.setattr(
            Product, "api_filter_lookups", {"name": ["exact", "isnull"]}, raising=False
        )
        fm._filterset_cache.pop("demo.product", None)
        try:
            r = auth_client.get("/api/models/demo/Product/?name__isnull=false")
            assert r.status_code == 200
            names = [row["name"] for row in r.json()["results"]]
            assert names == ["Laptop"]
        finally:
            fm._filterset_cache.pop("demo.product", None)

    def test_fk_isnull_true_matches_rows_without_relation(self, auth_client):
        from demo.apps.shop.models import Category, Product
        cat = Category.objects.create(name="Peripherals", slug="peripherals")
        Product.objects.create(name="With category", price=Decimal("10.00"), category=cat)
        Product.objects.create(name="No category", price=Decimal("20.00"), category=None)

        r = auth_client.get("/api/models/demo/Product/?category_id__isnull=true")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["results"]]
        assert names == ["No category"]

    def test_fk_isnull_false_matches_rows_with_relation(self, auth_client):
        from demo.apps.shop.models import Category, Product
        cat = Category.objects.create(name="Peripherals", slug="peripherals")
        Product.objects.create(name="With category", price=Decimal("10.00"), category=cat)
        Product.objects.create(name="No category", price=Decimal("20.00"), category=None)

        r = auth_client.get("/api/models/demo/Product/?category_id__isnull=false")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["results"]]
        assert names == ["With category"]

    def test_numeric_isnull_false_returns_non_null_rows(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="Cheap", price=Decimal("10.00"))
        Product.objects.create(name="Pricey", price=Decimal("20.00"))

        r = auth_client.get("/api/models/demo/Product/?price__isnull=false")
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["results"]}
        assert names == {"Cheap", "Pricey"}

    def test_numeric_in_accepts_comma_separated_values(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="Ten", price=Decimal("10.00"))
        Product.objects.create(name="Twenty", price=Decimal("20.00"))
        Product.objects.create(name="Thirty", price=Decimal("30.00"))

        r = auth_client.get("/api/models/demo/Product/?price__in=10.00,30.00")
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["results"]}
        assert names == {"Ten", "Thirty"}

    def test_fk_id_in_accepts_comma_separated_values(self, auth_client, order, customer):
        r = auth_client.get(f"/api/models/demo/Order/?customer_id__in={customer.pk}")
        assert r.status_code == 200
        assert len(r.json()["results"]) == 1

    def test_date_isnull_false_returns_dated_rows(self, auth_client, order):
        r = auth_client.get("/api/models/demo/Order/?created_at__isnull=false")
        assert r.status_code == 200
        assert len(r.json()["results"]) == 1


# ── swappable filter backend — SNAPADMIN_API_FILTER_BACKEND (#FEAT8c) ─────────

from rest_framework.filters import BaseFilterBackend  # noqa: E402


class _MarkerFilterBackend(BaseFilterBackend):
    """Test backend that filters everything out, so its presence is observable."""

    def filter_queryset(self, request, queryset, view):
        return queryset.none()


@pytest.mark.django_db
class TestApiFilterBackendSetting:
    """SNAPADMIN_API_FILTER_BACKEND swaps DynamicModelViewSet.filter_backends
    without monkeypatching; unset keeps the current default list."""

    def test_default_when_unset(self):
        from snapadmin.api.filters import (
            get_api_filter_backends, SnapAdminFilterBackend,
        )
        from rest_framework.filters import OrderingFilter, SearchFilter
        assert get_api_filter_backends() == [
            SnapAdminFilterBackend, SearchFilter, OrderingFilter,
        ]

    def test_single_dotted_path_string_replaces_list(self):
        from snapadmin.api.filters import get_api_filter_backends
        from rest_framework.filters import OrderingFilter
        with override_settings(
            SNAPADMIN_API_FILTER_BACKEND="rest_framework.filters.OrderingFilter"
        ):
            assert get_api_filter_backends() == [OrderingFilter]

    def test_single_class_object_replaces_list(self):
        from snapadmin.api.filters import get_api_filter_backends
        from rest_framework.filters import OrderingFilter
        with override_settings(SNAPADMIN_API_FILTER_BACKEND=OrderingFilter):
            assert get_api_filter_backends() == [OrderingFilter]

    def test_list_of_dotted_paths(self):
        from snapadmin.api.filters import get_api_filter_backends, SnapAdminFilterBackend
        from rest_framework.filters import SearchFilter
        with override_settings(SNAPADMIN_API_FILTER_BACKEND=[
            "snapadmin.api.filters.SnapAdminFilterBackend",
            "rest_framework.filters.SearchFilter",
        ]):
            assert get_api_filter_backends() == [SnapAdminFilterBackend, SearchFilter]

    def test_viewset_property_default(self):
        from snapadmin.api.views import DynamicModelViewSet
        from snapadmin.api.filters import SnapAdminFilterBackend
        from rest_framework.filters import OrderingFilter, SearchFilter
        assert DynamicModelViewSet().filter_backends == [
            SnapAdminFilterBackend, SearchFilter, OrderingFilter,
        ]

    def test_viewset_property_reflects_setting(self):
        from snapadmin.api.views import DynamicModelViewSet
        with override_settings(
            SNAPADMIN_API_FILTER_BACKEND="tests.test_model_api._MarkerFilterBackend"
        ):
            assert DynamicModelViewSet().filter_backends == [_MarkerFilterBackend]

    def test_custom_backend_applied_at_http_layer(self, auth_client, product):
        # Default: the product is listed.
        assert auth_client.get("/api/models/demo/Product/").json()["count"] == 1
        # Swap in a backend that filters everything out → empty list, proving the
        # setting actually replaces the backend chain (no monkeypatch).
        with override_settings(
            SNAPADMIN_API_FILTER_BACKEND="tests.test_model_api._MarkerFilterBackend"
        ):
            r = auth_client.get("/api/models/demo/Product/")
        assert r.status_code == 200
        assert r.json()["count"] == 0


# ── DynamicModelViewSet – always-on pagination (#SEC4) ────────────────────────

@pytest.mark.django_db
class TestDynamicListPagination:
    def test_list_paginated_with_default_page_size(self, auth_client, many_products):
        # 30 rows, SNAPADMIN_API_PAGE_SIZE unset → default page size 25.
        r = auth_client.get("/api/models/demo/Product/")
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 25
        assert data["count"] == 30

    def test_list_respects_custom_default_page_size(self, auth_client, many_products):
        with override_settings(SNAPADMIN_API_PAGE_SIZE=5):
            r = auth_client.get("/api/models/demo/Product/")
        assert len(r.json()["results"]) == 5

    def test_list_client_page_size_honoured_below_max(self, auth_client, many_products):
        r = auth_client.get("/api/models/demo/Product/?page_size=3")
        assert r.status_code == 200
        assert len(r.json()["results"]) == 3

    def test_list_client_page_size_clamped_to_max(self, auth_client, many_products):
        with override_settings(SNAPADMIN_API_MAX_PAGE_SIZE=10):
            r = auth_client.get("/api/models/demo/Product/?page_size=1000")
        assert r.status_code == 200
        # 30 rows exist; an unclamped request would return all 30 on one page.
        assert len(r.json()["results"]) == 10

    def test_list_page_size_default_max_is_500(self, auth_client, many_products):
        # Default SNAPADMIN_API_MAX_PAGE_SIZE (500) comfortably fits all 30 rows.
        r = auth_client.get("/api/models/demo/Product/?page_size=1000")
        assert r.status_code == 200
        assert len(r.json()["results"]) == 30


# ── DynamicModelViewSet – snapadmin-native throttling (#SEC4) ────────────────

@pytest.mark.django_db
class TestDynamicViewSetThrottling:
    @pytest.fixture(autouse=True)
    def _clear_throttle_cache(self):
        from django.core.cache import cache
        cache.clear()
        yield
        cache.clear()

    def test_low_user_rate_returns_429_after_limit(self, auth_client, product):
        with override_settings(SNAPADMIN_THROTTLE_USER="2/min", SNAPADMIN_THROTTLE_ANON=None):
            first = auth_client.get("/api/models/demo/Product/")
            second = auth_client.get("/api/models/demo/Product/")
            third = auth_client.get("/api/models/demo/Product/")
        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429

    def test_anon_throttle_enforces_low_rate(self):
        # DynamicModelViewSet requires IsAuthenticated, and DRF checks
        # permissions before throttles — an anonymous HTTP request never
        # reaches the throttle at all (it 401/403s first). Exercise
        # SnapAnonRateThrottle directly instead, the same way DRF would.
        from django.contrib.auth.models import AnonymousUser
        from django.core.cache import cache
        from django.test import RequestFactory
        from snapadmin.api.views import SnapAnonRateThrottle

        cache.clear()
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        with override_settings(SNAPADMIN_THROTTLE_ANON="2/min"):
            assert SnapAnonRateThrottle().allow_request(request, view=None) is True
            assert SnapAnonRateThrottle().allow_request(request, view=None) is True
            assert SnapAnonRateThrottle().allow_request(request, view=None) is False
        cache.clear()

    def test_anon_throttle_none_setting_allows_unlimited_requests(self):
        from django.contrib.auth.models import AnonymousUser
        from django.core.cache import cache
        from django.test import RequestFactory
        from snapadmin.api.views import SnapAnonRateThrottle

        cache.clear()
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        with override_settings(SNAPADMIN_THROTTLE_ANON=None):
            for _ in range(10):
                assert SnapAnonRateThrottle().allow_request(request, view=None) is True
        cache.clear()

    def test_anon_and_user_throttle_default_rates(self, monkeypatch):
        # The test settings pin both to None (see demo/core/settings_test.py) so
        # the suite never throttles itself. Simulate a host project that never
        # set either setting at all by swapping in a bare namespace for the
        # module-level `settings` name get_rate() reads from.
        import types
        from snapadmin.api import views as views_module
        from snapadmin.api.views import SnapAnonRateThrottle, SnapUserRateThrottle

        monkeypatch.setattr(views_module, "settings", types.SimpleNamespace())

        assert SnapAnonRateThrottle().get_rate() == "60/min"
        assert SnapUserRateThrottle().get_rate() == "600/min"

    def test_none_rate_disables_user_throttling(self, auth_client, product):
        # SNAPADMIN_THROTTLE_USER is None in the test settings by default: a
        # burst well above any real-world rate must never trip a 429.
        for _ in range(20):
            r = auth_client.get("/api/models/demo/Product/")
            assert r.status_code == 200

    def test_none_setting_value_explicitly_disables_throttle(self, auth_client, product):
        with override_settings(SNAPADMIN_THROTTLE_USER=None, SNAPADMIN_THROTTLE_ANON=None):
            for _ in range(10):
                assert auth_client.get("/api/models/demo/Product/").status_code == 200


# ── DynamicModelViewSet – auto-generated JSON key-path filters (#FEAT1) ──────

@pytest.mark.django_db
class TestAutoFilterJsonFieldLookups:
    """
    demo.Showcase declares:
        api_json_filters = {"json_field": ["a.b", "tags"]}
    so the auto-generated filter set exposes ?json_field__a__b=<value> (nested
    scalar match) and ?json_field__tags=<value> (list-membership match, since
    "tags" is stored as a JSON list). SQLite — the test settings' backend — has
    no native JSON `contains` support, so the list-membership case specifically
    exercises the Python fallback branch end-to-end.
    """

    @pytest.fixture
    def showcase_records(self, db):
        from demo.apps.shop.models import Showcase

        red = Showcase.objects.create(
            char_field="red-tagged",
            json_field={"a": {"b": "match-me"}, "tags": ["red", "blue"]},
        )
        green = Showcase.objects.create(
            char_field="green-tagged",
            json_field={"a": {"b": "other"}, "tags": ["green"]},
        )
        scalar_only = Showcase.objects.create(
            char_field="scalar-only",
            json_field={"a": {"b": "match-me"}, "tags": "not-a-list"},
        )
        return {"red": red, "green": green, "scalar_only": scalar_only}

    def test_nested_scalar_key_path_filters_correctly(self, auth_client, showcase_records):
        r = auth_client.get("/api/models/demo/Showcase/?json_field__a__b=match-me")
        assert r.status_code == 200
        names = {item["char_field"] for item in r.json()["results"]}
        assert names == {
            showcase_records["red"].char_field,
            showcase_records["scalar_only"].char_field,
        }

    def test_list_membership_matches_records_containing_value(self, auth_client, showcase_records):
        r = auth_client.get("/api/models/demo/Showcase/?json_field__tags=red")
        assert r.status_code == 200
        names = {item["char_field"] for item in r.json()["results"]}
        assert names == {showcase_records["red"].char_field}

    def test_list_membership_excludes_records_without_value(self, auth_client, showcase_records):
        r = auth_client.get("/api/models/demo/Showcase/?json_field__tags=purple")
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_list_membership_runs_on_sqlite_via_python_fallback(self, showcase_records):
        # The whole point of #FEAT1: this must work on the default dev/test
        # backend, not just on a backend with native JSON `contains` support.
        from django.db import connection
        from demo.apps.shop.models import Showcase
        from snapadmin.api.filters import build_filterset_for_model

        assert connection.vendor == "sqlite"
        assert connection.features.supports_json_field_contains is False

        FS = build_filterset_for_model(Showcase)
        fs = FS({"json_field__tags": "green"}, queryset=Showcase.objects.all())
        assert list(fs.qs.values_list("char_field", flat=True)) == [
            showcase_records["green"].char_field
        ]

    def test_unknown_key_path_is_not_filterable(self, auth_client, showcase_records):
        # A key-path that isn't declared in api_json_filters has no
        # corresponding filter field at all — django-filter silently ignores
        # unrecognised query params, so the request behaves exactly as if the
        # param had not been sent (every row is returned, unfiltered).
        r = auth_client.get("/api/models/demo/Showcase/?json_field__unknown__path=anything")
        assert r.status_code == 200
        names = {item["char_field"] for item in r.json()["results"]}
        assert names == {
            showcase_records["red"].char_field,
            showcase_records["green"].char_field,
            showcase_records["scalar_only"].char_field,
        }

    def test_model_without_api_json_filters_exposes_no_json_filters(self, monkeypatch):
        # Matches today's behavior for a model that never opts in: the JSON
        # field is simply absent from the auto-generated filter set — no
        # crash, no accidental exposure of every key-path.
        from demo.apps.shop.models import Showcase
        from snapadmin.api.filters import _build_filters_for_model

        monkeypatch.setattr(Showcase, "api_json_filters", None, raising=False)
        filters = _build_filters_for_model(Showcase)
        assert not any(name.startswith("json_field__") for name in filters)

    def test_declared_json_filters_present_in_filter_set(self):
        from demo.apps.shop.models import Showcase
        from snapadmin.api.filters import _build_filters_for_model

        filters = _build_filters_for_model(Showcase)
        assert "json_field__a__b" in filters
        assert "json_field__tags" in filters

    def test_filterset_cache_reflects_current_model_declaration(self):
        # api_json_filters is a plain class attribute (like api_write_fields),
        # derived per model — the cache is keyed per model, not per attribute
        # value, which is correct because the declaration is static for the
        # lifetime of the process. Rebuilding after clearing the cache entry
        # must still surface the declared JSON filters, and a second call
        # must hit the cache (identical class).
        from demo.apps.shop.models import Showcase
        from snapadmin.api.filters import build_filterset_for_model, _filterset_cache

        cache_key = f"{Showcase._meta.app_label}.{Showcase._meta.model_name}"
        _filterset_cache.pop(cache_key, None)

        fresh = build_filterset_for_model(Showcase)
        assert "json_field__a__b" in fresh.base_filters
        assert "json_field__tags" in fresh.base_filters

        cached = build_filterset_for_model(Showcase)
        assert cached is fresh


# ── #PERF5: JSON filter comma-OR, lazy native queryset, scan cap ─────────────

def _flatten_q(q):
    from django.db.models import Q
    out = set()
    for child in q.children:
        if isinstance(child, Q):
            out |= _flatten_q(child)
        else:
            key, val = child
            out.add((key, tuple(val) if isinstance(val, list) else val))
    return out


@pytest.mark.django_db
class TestJsonFilterCommaOrLazyAndCap:
    @pytest.fixture
    def showcase_records(self, db):
        from demo.apps.shop.models import Showcase
        return {
            "red": Showcase.objects.create(
                char_field="red", json_field={"a": {"b": "x"}, "tags": ["red", "blue"]}),
            "green": Showcase.objects.create(
                char_field="green", json_field={"a": {"b": "y"}, "tags": ["green"]}),
            "amber": Showcase.objects.create(
                char_field="amber", json_field={"a": {"b": "z"}, "tags": ["amber"]}),
        }

    def test_comma_separated_list_membership_or(self, auth_client, showcase_records):
        r = auth_client.get("/api/models/demo/Showcase/?json_field__tags=red,green")
        assert r.status_code == 200
        assert {i["char_field"] for i in r.json()["results"]} == {"red", "green"}

    def test_comma_separated_scalar_or(self, auth_client, showcase_records):
        r = auth_client.get("/api/models/demo/Showcase/?json_field__a__b=x,z")
        assert r.status_code == 200
        assert {i["char_field"] for i in r.json()["results"]} == {"red", "amber"}

    def test_single_value_still_matches(self, auth_client, showcase_records):
        r = auth_client.get("/api/models/demo/Showcase/?json_field__tags=green")
        assert r.status_code == 200
        assert {i["char_field"] for i in r.json()["results"]} == {"green"}

    def test_only_commas_is_treated_as_empty(self, auth_client, showcase_records):
        # value "," → no non-empty parts → the filter is a no-op (all rows back).
        r = auth_client.get("/api/models/demo/Showcase/?json_field__tags=,")
        assert r.status_code == 200
        assert len(r.json()["results"]) == 3

    def test_empty_value_is_noop(self, showcase_records):
        from demo.apps.shop.models import Showcase
        from snapadmin.api.filters import JsonKeyPathFilter
        jf = JsonKeyPathFilter(json_field_name="json_field", key_path="tags")
        qs = Showcase.objects.all()
        assert jf.filter(qs, "") is qs

    def test_scan_cap_exceeded_returns_400(self, auth_client, showcase_records):
        # 3 rows, cap 1 → the SQLite membership scan refuses rather than OOM.
        with override_settings(SNAPADMIN_API_JSON_FILTER_SCAN_CAP=1):
            r = auth_client.get("/api/models/demo/Showcase/?json_field__tags=red")
        assert r.status_code == 400

    def test_scan_cap_not_exceeded_ok(self, auth_client, showcase_records):
        with override_settings(SNAPADMIN_API_JSON_FILTER_SCAN_CAP=1000):
            r = auth_client.get("/api/models/demo/Showcase/?json_field__tags=red")
        assert r.status_code == 200
        assert {i["char_field"] for i in r.json()["results"]} == {"red"}

    def test_native_branch_is_lazy_no_pk_materialisation(self, monkeypatch):
        from django.db.models import Q
        from snapadmin.api.filters import JsonKeyPathFilter
        jf = JsonKeyPathFilter(json_field_name="json_field", key_path="tags")
        captured = {}

        class FakeQuerySet:
            db = "default"

            def filter(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                return "LAZY_QS"

        fake_conn = type("C", (), {
            "features": type("F", (), {"supports_json_field_contains": True})()
        })()
        monkeypatch.setattr("snapadmin.api.filters.connections", {"default": fake_conn})

        result = jf.filter(FakeQuerySet(), "green,blue")
        # Returned the lazy queryset directly — no PK set materialised, so an
        # export's .iterator() still streams. FakeQuerySet has *only* .filter():
        # any .count()/.values_list()/.iterator() call would AttributeError.
        assert result == "LAZY_QS"
        assert captured["kwargs"] == {}          # the Q is positional, no pk__in=
        q = captured["args"][0]
        assert isinstance(q, Q)
        assert _flatten_q(q) == {
            ("json_field__tags", "green"), ("json_field__tags__contains", ("green",)),
            ("json_field__tags", "blue"), ("json_field__tags__contains", ("blue",)),
        }


# ── PII masking excluded from filter/ordering/search (#SEC6) ────────────────

@pytest.mark.django_db
class TestMaskedFieldsExcludedFromApiQuerying:
    """A masked field must not be usable as a filter/ordering/search oracle."""

    @staticmethod
    def _regular_client_with_product_view(regular_user):
        from django.contrib.auth.models import Permission
        from django.contrib.auth import get_user_model
        from snapadmin.models import APIToken
        regular_user.user_permissions.add(Permission.objects.get(codename="view_product"))
        fresh = get_user_model().objects.get(pk=regular_user.pk)
        token = APIToken.create_for_user(fresh, "Reg")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        return client

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_filter_on_masked_field_ignored_for_unprivileged(self, regular_user):
        from demo.apps.shop.models import Product
        laptop = Product.objects.create(name="Laptop", price=Decimal("10.00"))
        desktop = Product.objects.create(name="Desktop", price=Decimal("20.00"))
        client = self._regular_client_with_product_view(regular_user)

        r = client.get("/api/models/demo/Product/?name=Laptop")
        assert r.status_code == 200
        # response also masks "name" (pre-existing serializer behaviour) —
        # assert on id, not the (uniformly starred) name string.
        ids = {row["id"] for row in r.json()["results"]}
        assert ids == {laptop.pk, desktop.pk}  # filter silently ignored, not applied

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_filter_on_masked_field_still_applies_for_privileged(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="Laptop", price=Decimal("10.00"))
        Product.objects.create(name="Desktop", price=Decimal("20.00"))

        r = auth_client.get("/api/models/demo/Product/?name=Laptop")
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["results"]}
        assert names == {"Laptop"}

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_ordering_by_masked_field_ignored_for_unprivileged(self, regular_user):
        from demo.apps.shop.models import Product
        alpha = Product.objects.create(name="Alpha", price=Decimal("1"))  # pk1, created first
        bravo = Product.objects.create(name="Bravo", price=Decimal("2"))  # pk2, created second
        client = self._regular_client_with_product_view(regular_user)

        r = client.get("/api/models/demo/Product/?ordering=name")
        assert r.status_code == 200
        # Invalid ordering term is dropped -> falls back to the default (-pk,
        # newest first): [bravo, alpha], not the alphabetical [alpha, bravo].
        ids = [row["id"] for row in r.json()["results"]]
        assert ids == [bravo.pk, alpha.pk]

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_ordering_by_unmasked_field_still_works_for_unprivileged(self, regular_user):
        # Masking one field must not narrow the ordering allowlist down to
        # only concrete model columns for every other field — a field the
        # serializer exposes (e.g. a to-many) that isn't masked stays orderable.
        from demo.apps.shop.models import Product
        cheap = Product.objects.create(name="Alpha", price=Decimal("1"))
        pricey = Product.objects.create(name="Bravo", price=Decimal("2"))
        client = self._regular_client_with_product_view(regular_user)

        r = client.get("/api/models/demo/Product/?ordering=price")
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()["results"]]
        assert ids == [cheap.pk, pricey.pk]

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_ordering_by_masked_field_applies_for_privileged(self, auth_client):
        from demo.apps.shop.models import Product
        alpha = Product.objects.create(name="Alpha", price=Decimal("1"))
        bravo = Product.objects.create(name="Bravo", price=Decimal("2"))

        r = auth_client.get("/api/models/demo/Product/?ordering=name")
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()["results"]]
        assert ids == [alpha.pk, bravo.pk]

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_search_on_masked_field_ignored_for_unprivileged(self, regular_user):
        from demo.apps.shop.models import Product
        Product.objects.create(name="UniqueWidgetXYZ", price=Decimal("1"))
        client = self._regular_client_with_product_view(regular_user)

        r = client.get("/api/models/demo/Product/?search=UniqueWidgetXYZ")
        assert r.status_code == 200
        assert r.json()["results"] == []

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_search_on_masked_field_applies_for_privileged(self, auth_client):
        from demo.apps.shop.models import Product
        Product.objects.create(name="UniqueWidgetXYZ", price=Decimal("1"))

        r = auth_client.get("/api/models/demo/Product/?search=UniqueWidgetXYZ")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["results"]]
        assert "UniqueWidgetXYZ" in names
