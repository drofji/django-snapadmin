"""
Tests for per-model offline mode (#9).

Offline mode is a per-model toggle (`offline_mode = True`) that injects
`snapadmin/js/offline.js` into the model's admin Media. The JS layer handles
IndexedDB caching, the offline banner, and reconnect sync; these Python tests
verify the wiring (attribute defaults, conditional JS injection) and that the
shipped JS asset contains the expected offline machinery.
"""

from pathlib import Path

import pytest
from django.contrib import admin

from snapadmin.models import SnapModel

OFFLINE_JS = "snapadmin/js/offline.js"
CONNECTIVITY_JS = "snapadmin/js/connectivity.js"

STATIC_ROOT = Path(__file__).resolve().parent.parent / "snapadmin" / "static"


def _media_js(model):
    """Return the list of JS files declared on a model's registered admin."""
    model_admin = admin.site._registry[model]
    return list(model_admin.Media.js)


def _read_asset(rel_path):
    path = STATIC_ROOT / rel_path
    assert path.exists(), f"missing asset: {path}"
    return path.read_text(encoding="utf-8")


class TestOfflineModeAttribute:
    def test_default_is_false(self):
        assert SnapModel.offline_mode is False

    def test_customer_enables_offline_mode(self):
        from demo.models import Customer
        assert Customer.offline_mode is True

    def test_product_keeps_offline_mode_disabled(self):
        from demo.models import Product
        assert Product.offline_mode is False


class TestOfflineCacheLimit:
    def test_default_limit_is_100(self):
        assert SnapModel.offline_cache_limit == 100

    def test_customer_overrides_limit(self):
        from demo.models import Customer
        assert Customer.offline_cache_limit == 50

    def test_non_offline_model_keeps_default(self):
        from demo.models import Product
        assert Product.offline_cache_limit == 100


class TestOfflineJsInjection:
    def test_offline_model_includes_offline_js(self):
        from demo.models import Customer
        assert OFFLINE_JS in _media_js(Customer)

    def test_non_offline_model_excludes_offline_js(self):
        from demo.models import Product
        assert OFFLINE_JS not in _media_js(Product)

    def test_register_admin_appends_offline_js_for_offline_model(self):
        """A model toggled to offline_mode gets offline.js without touching other JS."""
        from demo.models import Order

        original = Order.offline_mode
        try:
            Order.offline_mode = True
            admin.site.unregister(Order)
            Order.register_admin()
            js = _media_js(Order)
            assert OFFLINE_JS in js
            # offline.js must be appended after the base admin bundle, not replace it.
            assert "snapadmin/js/admin.js" in js
        finally:
            Order.offline_mode = original
            admin.site.unregister(Order)
            Order.register_admin()

    def test_offline_js_listed_once(self):
        from demo.models import Customer
        js = _media_js(Customer)
        assert js.count(OFFLINE_JS) == 1


class TestOfflineJsAsset:
    @pytest.fixture(scope="class")
    @staticmethod
    def source():
        return _read_asset(OFFLINE_JS)

    def test_uses_indexeddb(self, source):
        assert "indexedDB" in source
        assert "snapadmin_offline" in source

    def test_defines_cache_read_helpers(self, source):
        assert "cacheRows" in source
        assert "readRows" in source

    def test_prefetches_from_offline_data_endpoint(self, source):
        assert "offline-data/" in source
        assert "fetchOfflineData" in source

    def test_renders_saved_objects_panel(self, source):
        assert "snapadmin-offline-panel" in source
        assert "renderPanel" in source
        assert "Saved objects" in source

    def test_reacts_to_shared_connectivity_event(self, source):
        # offline.js follows connectivity.js's resolved state, not navigator.onLine.
        assert "snapadmin:connectivity" in source
        assert "function sync" in source
        assert "queueMutation" in source

    def test_uses_dynamic_toasts(self, source):
        assert "SnapAdminToast" in source
        assert "snapadmin-offline-banner" not in source  # static banner replaced

    def test_exposes_test_hooks(self, source):
        assert "window.SnapAdminOffline" in source

    def test_marks_page_capable_and_records_model(self, source):
        # connectivity.js relies on these to choose the friendly banner and badge
        # the sidebar even while offline.
        assert "SNAPADMIN_OFFLINE_CAPABLE" in source
        assert "snapadmin:offline-models" in source


# ─────────────────────────────────────────────────────────────────────────────
# Connectivity layer — loaded on every SnapModel admin page
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectivityJsInjection:
    def test_connectivity_js_loaded_on_offline_model(self):
        from demo.models import Customer
        assert CONNECTIVITY_JS in _media_js(Customer)

    def test_connectivity_js_loaded_on_non_offline_model(self):
        from demo.models import Product
        assert CONNECTIVITY_JS in _media_js(Product)

    def test_connectivity_loads_before_offline_js(self):
        """offline.js sets the capability flag connectivity.js reads, so it must follow."""
        from demo.models import Customer
        js = _media_js(Customer)
        assert js.index(CONNECTIVITY_JS) < js.index(OFFLINE_JS)


class TestConnectivityJsAsset:
    @pytest.fixture(scope="class")
    @staticmethod
    def source():
        return _read_asset(CONNECTIVITY_JS)

    def test_warns_when_offline_on_non_capable_page(self, source):
        assert "objects can't be shown right now" in source
        assert "will NOT be saved" in source
        assert "#DC2626" in source  # warn-toast background

    def test_polls_backend_health(self, source):
        assert "health/" in source
        assert "checkBackend" in source
        assert "AbortController" in source

    def test_broadcasts_connectivity_event(self, source):
        assert "snapadmin:connectivity" in source
        assert "isBackendUp" in source

    def test_uses_dynamic_toasts(self, source):
        assert "SnapAdminToast" in source
        assert "snapadmin-toasts" in source
        assert "snapadmin-conn-banner" not in source  # static banner replaced

    def test_blocks_form_submit(self, source):
        assert "submitGuard" in source
        assert "preventDefault" in source
        assert "setSaveBlocked" in source

    def test_defers_to_offline_engine_when_capable(self, source):
        assert "SNAPADMIN_OFFLINE_CAPABLE" in source
        assert "isCurrentCapable" in source

    def test_badges_sidebar(self, source):
        assert "decorateSidebar" in source
        assert "snap-sync-badge" in source
        assert "snap-nooffline-badge" in source

    def test_fetches_offline_model_list(self, source):
        assert "offline-models/" in source
        assert "snapadmin:offline-models" in source  # localStorage fallback key

    def test_exposes_test_hooks(self, source):
        assert "window.SnapAdminConnectivity" in source


# ─────────────────────────────────────────────────────────────────────────────
# Offline-capable models endpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOfflineModelsEndpoint:
    URL = "/api/offline-models/"

    def test_lists_offline_capable_models(self, auth_client):
        r = auth_client.get(self.URL)
        assert r.status_code == 200
        models = r.json()["models"]
        assert "demo/customer" in models

    def test_excludes_non_offline_models(self, auth_client):
        r = auth_client.get(self.URL)
        assert "demo/product" not in r.json()["models"]

    def test_requires_authentication(self, anon_client):
        r = anon_client.get(self.URL)
        assert r.status_code in (401, 403)

    def test_helper_returns_sorted_keys(self):
        from snapadmin.api.offline import get_offline_model_keys
        keys = get_offline_model_keys()
        assert keys == sorted(keys)
        assert "demo/customer" in keys

    def test_reports_per_model_cache_limits(self, auth_client):
        r = auth_client.get(self.URL)
        limits = r.json()["limits"]
        assert limits["demo/customer"] == 50

    def test_limits_helper(self):
        from snapadmin.api.offline import get_offline_model_limits
        limits = get_offline_model_limits()
        assert limits["demo/customer"] == 50


# ─────────────────────────────────────────────────────────────────────────────
# Offline-data feed — recent rows of one offline-capable model
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOfflineModelDataEndpoint:
    URL = "/api/offline-data/demo/customer/"

    @pytest.fixture
    def customers(self, db):
        from demo.models import Customer
        return [
            Customer.objects.create(
                first_name=f"User{i}", last_name="Test",
                email=f"user{i}@example.com", origin="status_a", active=True,
            )
            for i in range(5)
        ]

    def test_requires_authentication(self, anon_client):
        r = anon_client.get(self.URL)
        assert r.status_code in (401, 403)

    def test_returns_recent_objects(self, auth_client, customers):
        r = auth_client.get(self.URL)
        assert r.status_code == 200
        body = r.json()
        assert body["model"] == "demo/customer"
        assert body["count"] == len(body["objects"]) == 5
        assert body["limit"] == 50  # Customer.offline_cache_limit
        assert body["fields"]  # verbose labels present

    def test_orders_by_descending_pk(self, auth_client, customers):
        r = auth_client.get(self.URL)
        ids = [obj["id"] for obj in r.json()["objects"]]
        assert ids == sorted(ids, reverse=True)

    def test_limit_query_param_is_respected(self, auth_client, customers):
        r = auth_client.get(self.URL + "?limit=2")
        body = r.json()
        assert body["limit"] == 2
        assert body["count"] == 2

    def test_limit_is_clamped_to_model_cap(self, auth_client, customers):
        r = auth_client.get(self.URL + "?limit=9999")
        assert r.json()["limit"] == 50  # capped at offline_cache_limit

    def test_invalid_limit_falls_back_to_cap(self, auth_client, customers):
        r = auth_client.get(self.URL + "?limit=abc")
        assert r.json()["limit"] == 50

    def test_non_positive_limit_falls_back_to_cap(self, auth_client, customers):
        r = auth_client.get(self.URL + "?limit=0")
        assert r.json()["limit"] == 50

    def test_non_offline_model_is_404(self, auth_client):
        r = auth_client.get("/api/offline-data/demo/product/")
        assert r.status_code == 404

    def test_unknown_model_is_404(self, auth_client):
        r = auth_client.get("/api/offline-data/demo/nope/")
        assert r.status_code == 404

    def test_non_snapmodel_is_404(self, auth_client):
        # auth.User is a real model but not a SnapModel → not exposed offline.
        r = auth_client.get("/api/offline-data/auth/user/")
        assert r.status_code == 404

    def test_serves_related_fields_with_join_and_prefetch(self, auth_client, product):
        """A model with FK + M2M exercises the select_related / prefetch_related path."""
        from demo.models import Product

        original = Product.offline_mode
        try:
            Product.offline_mode = True
            r = auth_client.get("/api/offline-data/demo/product/")
            assert r.status_code == 200
            assert r.json()["count"] == 1
        finally:
            Product.offline_mode = original

    def test_url_route_resolves(self):
        from django.urls import resolve, reverse
        url = reverse("offline-data", kwargs={"app_label": "demo", "model_name": "customer"})
        assert url == self.URL
        assert resolve(url).view_name == "offline-data"


# ─────────────────────────────────────────────────────────────────────────────
# Offline endpoints honour admin access — staff + per-model view permission
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOfflineEndpointPermissions:
    DATA_URL = "/api/offline-data/demo/customer/"
    MODELS_URL = "/api/offline-models/"

    @pytest.fixture
    def client_factory(self):
        from rest_framework.test import APIClient

        def _make(user):
            client = APIClient()
            client.force_authenticate(user=user)
            return client
        return _make

    @pytest.fixture
    def non_staff_user(self, db):
        from django.contrib.auth.models import User
        return User.objects.create_user(username="plain", password="x")

    @pytest.fixture
    def staff_no_perm(self, db):
        from django.contrib.auth.models import User
        return User.objects.create_user(username="staff_noperm", password="x", is_staff=True)

    @pytest.fixture
    def staff_with_perm(self, db):
        from django.contrib.auth.models import User
        from django.contrib.auth.models import Permission
        user = User.objects.create_user(username="staff_perm", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_customer"))
        return user

    def test_data_feed_denied_for_non_staff(self, client_factory, non_staff_user, customer):
        r = client_factory(non_staff_user).get(self.DATA_URL)
        assert r.status_code == 403

    def test_data_feed_denied_for_staff_without_view_perm(self, client_factory, staff_no_perm, customer):
        r = client_factory(staff_no_perm).get(self.DATA_URL)
        assert r.status_code == 403

    def test_data_feed_allowed_for_staff_with_view_perm(self, client_factory, staff_with_perm, customer):
        r = client_factory(staff_with_perm).get(self.DATA_URL)
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_models_list_filtered_by_permission(self, client_factory, non_staff_user, staff_with_perm):
        # A user who cannot view Customer never sees it advertised as offline-capable.
        empty = client_factory(non_staff_user).get(self.MODELS_URL).json()
        assert "demo/customer" not in empty["models"]
        assert "demo/customer" not in empty["limits"]
        allowed = client_factory(staff_with_perm).get(self.MODELS_URL).json()
        assert "demo/customer" in allowed["models"]
        assert allowed["limits"]["demo/customer"] == 50
