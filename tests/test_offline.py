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
    def source(self):
        return _read_asset(OFFLINE_JS)

    def test_uses_indexeddb(self, source):
        assert "indexedDB" in source
        assert "snapadmin_offline" in source

    def test_defines_cache_read_helpers(self, source):
        assert "cacheRows" in source
        assert "readRows" in source

    def test_renders_offline_banner(self, source):
        assert "snapadmin-offline-banner" in source
        assert "#DC2626" in source  # red banner background

    def test_syncs_on_reconnect(self, source):
        assert 'addEventListener("online"' in source
        assert "function sync" in source
        assert "queueMutation" in source

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
    def source(self):
        return _read_asset(CONNECTIVITY_JS)

    def test_warns_when_offline_on_non_capable_page(self, source):
        assert "snapadmin-conn-banner" in source
        assert "will NOT be saved" in source
        assert "#DC2626" in source

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
