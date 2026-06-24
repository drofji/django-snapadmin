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


def _media_js(model):
    """Return the list of JS files declared on a model's registered admin."""
    model_admin = admin.site._registry[model]
    return list(model_admin.Media.js)


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
        path = Path(__file__).resolve().parent.parent / "snapadmin" / "static" / OFFLINE_JS
        assert path.exists(), f"missing offline asset: {path}"
        return path.read_text(encoding="utf-8")

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
