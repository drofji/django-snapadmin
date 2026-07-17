"""
tests/test_ecosystem_compat.py — Django ecosystem compatibility (issue #1)

SnapAdmin only auto-registers SnapModel subclasses, so third-party models are
never touched. Where a package wants to enrich *your* model's admin
(django-import-export, reversion, simple-history, guardian), its admin mixin
composes via `admin_mixins`; `admin_enabled = False` hands the admin over
entirely. Auto-registration also never clobbers an already-registered admin.
"""

import pytest
from django.contrib import admin as dj_admin

from snapadmin.models import PIIMaskingAdminMixin, SnapSaveMixin


class _EcoMixin:
    """Stand-in for an ecosystem ModelAdmin mixin (e.g. ImportExportModelAdmin)."""
    eco_flag = True

    def eco_action(self):
        return "exported"


@pytest.mark.django_db
class TestAdminMixins:
    def test_mixin_composes_with_snapadmin(self):
        from demo.app.models import Product
        dj_admin.site.unregister(Product)
        Product.admin_mixins = [_EcoMixin]
        try:
            Product.register_admin()
            ma = dj_admin.site._registry[Product]
            # Ecosystem behaviour is present…
            assert isinstance(ma, _EcoMixin)
            assert ma.eco_flag is True
            assert ma.eco_action() == "exported"
            # …and SnapAdmin's own behaviour is retained underneath.
            assert isinstance(ma, SnapSaveMixin)
            assert isinstance(ma, PIIMaskingAdminMixin)
            assert len(ma.list_display) > 0  # auto-generated config intact
        finally:
            Product.admin_mixins = []
            dj_admin.site.unregister(Product)
            Product.register_admin()

    def test_mixin_precedes_snapadmin_in_mro(self):
        from demo.app.models import Product
        dj_admin.site.unregister(Product)
        Product.admin_mixins = [_EcoMixin]
        try:
            Product.register_admin()
            mro = type(dj_admin.site._registry[Product]).__mro__
            assert mro.index(_EcoMixin) < mro.index(SnapSaveMixin)
        finally:
            Product.admin_mixins = []
            dj_admin.site.unregister(Product)
            Product.register_admin()

    def test_default_is_no_extra_bases(self):
        from demo.app.models import Product
        ma = dj_admin.site._registry[Product]
        assert isinstance(ma, SnapSaveMixin)  # normal composition unaffected


@pytest.mark.django_db
class TestEscapeHatches:
    def test_admin_enabled_false_skips_registration(self):
        from demo.app.models import Product
        dj_admin.site.unregister(Product)
        Product.admin_enabled = False
        try:
            Product.register_admin()  # opts out — a package can own this admin
            assert Product not in dj_admin.site._registry
        finally:
            Product.admin_enabled = True
            Product.register_admin()

    def test_register_admin_never_clobbers_existing(self):
        # A package (or the user) may register a custom admin first; SnapAdmin's
        # auto-registration must skip it, not overwrite it.
        from demo.app.models import Product

        class CustomAdmin(dj_admin.ModelAdmin):
            marker = "custom"

        dj_admin.site.unregister(Product)
        dj_admin.site.register(Product, CustomAdmin)
        try:
            Product.register_admin()  # must be a no-op (AlreadyRegistered)
            assert getattr(dj_admin.site._registry[Product], "marker", None) == "custom"
        finally:
            dj_admin.site.unregister(Product)
            Product.register_admin()

    def test_third_party_non_snapmodel_untouched(self):
        # SnapAdmin only auto-registers SnapModel subclasses, so a plain Django
        # model (like a taggit/guardian model) is never auto-registered by us.
        from django.contrib.auth.models import Permission
        from snapadmin.models import SnapModel
        assert not issubclass(Permission, SnapModel)
