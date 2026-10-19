"""
tests/test_admin_mixin_precedence.py — a mixin in ``admin_mixins`` can extend what
the generator builds (#EXT3n).

``register_admin()`` put ``get_readonly_fields``, ``get_fieldsets`` and
``formfield_for_dbfield`` into the generated class's own namespace — the most
derived class — so a project mixin listed in ``admin_mixins`` could neither
override them nor extend them through ``super()``: its method was simply never
reached. The mixin tests only ever used methods the generator does not define.

The generated callables now live on a generated base placed after the mixins:
``admin_overrides`` > ``admin_mixins`` > generated > ``PIIMaskingAdminMixin`` >
``SnapSaveMixin`` > ``ModelAdmin``. The class guard: every callable on that base
is reachable from a mixin through ``super()``.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.test import RequestFactory

MARK = "added_by_the_project_mixin"


class ExtendingMixin:
    """What a project writes: extend, never replace, the generated behaviour."""

    def get_readonly_fields(self, request, obj=None):
        return [*super().get_readonly_fields(request, obj), MARK]

    def get_fieldsets(self, request, obj=None):
        return [*super().get_fieldsets(request, obj), (MARK, {"fields": []})]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if formfield is not None:
            formfield.help_text = MARK
        return formfield


@pytest.fixture
def product_admin_with_mixin(monkeypatch, admin_user):
    from demo.apps.shop.models import Product

    site = AdminSite(name="mixin-precedence")
    monkeypatch.setattr(admin, "site", site)
    monkeypatch.setattr(Product, "admin_mixins", [ExtendingMixin])
    Product.register_admin()
    request = RequestFactory().get("/")
    request.user = admin_user
    yield site._registry[Product], request
    site._registry.clear()


@pytest.mark.django_db
class TestAMixinExtendsTheGeneratedMethods:
    def test_get_readonly_fields_keeps_the_generated_ones_and_adds_the_mixin_s(
        self, product_admin_with_mixin
    ):
        from demo.apps.shop.models import Product

        model_admin, request = product_admin_with_mixin
        generated = Product._admin_generated_overrides["get_readonly_fields"](model_admin, request)

        assert model_admin.get_readonly_fields(request) == [*generated, MARK]

    def test_get_fieldsets_keeps_the_generated_layout_and_adds_a_section(self, product_admin_with_mixin):
        model_admin, request = product_admin_with_mixin

        fieldsets = model_admin.get_fieldsets(request)

        assert fieldsets[-1] == (MARK, {"fields": []})
        assert any("name" in str(options.get("fields")) for _title, options in fieldsets[:-1])

    def test_formfield_for_dbfield_is_the_generated_one_plus_the_mixin_s_tweak(self, product_admin_with_mixin):
        from demo.apps.shop.models import Product

        model_admin, request = product_admin_with_mixin

        formfield = model_admin.formfield_for_dbfield(Product._meta.get_field("description"), request)

        assert formfield.help_text == MARK
        assert "CKEditor" in type(formfield.widget).__name__ or "ckeditor" in type(formfield.widget).__module__.lower()


@pytest.mark.django_db
class TestEveryGeneratedCallableIsReachableFromAMixin:
    def test_the_class_guard(self, product_admin_with_mixin):
        model_admin, _request = product_admin_with_mixin
        mro = type(model_admin).__mro__
        generated_bases = [klass for klass in mro if vars(klass).get("snapadmin_generated_base")]

        assert len(generated_bases) == 1, "review: the generated admin has no generated base class"
        generated = generated_bases[0]
        callables = {name for name, value in vars(generated).items() if callable(value) and not name.startswith("__")}
        assert {"get_readonly_fields", "get_fieldsets", "formfield_for_dbfield"} <= callables
        shadowing = sorted(callables & set(vars(type(model_admin))))
        assert not shadowing, (
            f"review: {shadowing} sit in the generated admin's own namespace again — a mixin in "
            "admin_mixins can no longer extend them through super()"
        )
        assert mro.index(ExtendingMixin) < mro.index(generated)
