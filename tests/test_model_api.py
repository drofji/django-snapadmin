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
        from demo.models import Product
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

    def test_export_invalid_limit_streams_all(self, auth_client, many_products):
        # Non-numeric, zero and negative caps must not silently truncate.
        for bad in ("abc", "0", "-3", ""):
            rows = _ndjson_rows(
                auth_client.get(f"/api/models/demo/Product/export/?limit={bad}")
            )
            assert len(rows) == 30, bad

    def test_export_carries_backend_header(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/export/")
        assert r["X-Snap-Query-Backend"] == "database"

    def test_export_unknown_model_404(self, auth_client):
        assert auth_client.get("/api/models/demo/GhostModel/export/").status_code == 404

    def test_export_unauthenticated_denied(self, anon_client):
        assert anon_client.get("/api/models/demo/Product/export/").status_code in (401, 403)


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
        from demo.models import Product
        r = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 204
        assert not Product.objects.filter(pk=product.pk).exists()

    def test_model_hook_veto_returns_403(self, auth_client, product, monkeypatch):
        from demo.models import Product
        monkeypatch.setattr(Product, "api_can_delete", lambda self, request: False)
        r = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 403
        assert Product.objects.filter(pk=product.pk).exists()  # not deleted

    def test_setting_guard_veto_returns_403(self, auth_client, product):
        from demo.models import Product
        with override_settings(
            SNAPADMIN_API_DELETE_GUARD="tests.test_model_api._veto_all_deletes"
        ):
            r = auth_client.delete(f"/api/models/demo/Product/{product.pk}/")
        assert r.status_code == 403
        assert Product.objects.filter(pk=product.pk).exists()

    def test_setting_guard_receives_object(self, auth_client, product, product_unavailable):
        from demo.models import Product
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
        from demo.models import Product
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
    from demo.models import Product

    monkeypatch.setattr(Product, "api_write_fields", ["available"], raising=False)
    serializers_module._serializer_cache.pop("demo.Product", None)
    yield
    serializers_module._serializer_cache.pop("demo.Product", None)


@pytest.mark.django_db
class TestApiWriteFieldsAllowlist:
    def test_default_unset_leaves_every_field_writable(self):
        from snapadmin.api.serializers import build_model_serializer
        from demo.models import Product
        fields = build_model_serializer(Product)().fields
        assert fields["name"].read_only is False
        assert fields["price"].read_only is False
        assert fields["available"].read_only is False

    def test_explicit_list_forces_other_fields_read_only(self, monkeypatch):
        from snapadmin.api.serializers import build_model_serializer
        from demo.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["available"], raising=False)
        fields = build_model_serializer(Product)().fields
        assert fields["available"].read_only is False
        assert fields["name"].read_only is True
        assert fields["price"].read_only is True

    def test_non_writable_field_still_readable(self, monkeypatch):
        # Restricting writes must not narrow what api_exclude_fields controls.
        from snapadmin.api.serializers import build_model_serializer
        from demo.models import Product
        monkeypatch.setattr(Product, "api_write_fields", ["available"], raising=False)
        fields = build_model_serializer(Product)().fields
        assert "name" in fields
        assert "price" in fields

    def test_pk_stays_read_only_even_if_listed(self, monkeypatch):
        from snapadmin.api.serializers import build_model_serializer
        from demo.models import Product
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
        from demo.models import Product
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
        # The test settings pin both to None (see sandbox/settings_test.py) so
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
