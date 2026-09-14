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
from django.test import override_settings
from django.urls import reverse


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminRegistration:
    def test_product_in_registry(self):
        from demo.apps.shop.models import Product
        assert Product in admin.site._registry

    def test_customer_in_registry(self):
        from demo.apps.shop.models import Customer
        assert Customer in admin.site._registry

    def test_order_in_registry(self):
        from demo.apps.shop.models import Order
        assert Order in admin.site._registry

    def test_api_token_in_registry(self):
        from snapadmin.models import APIToken
        assert APIToken in admin.site._registry

    def test_register_admin_idempotent(self):
        """Calling register_admin twice must not raise."""
        from demo.apps.shop.models import Product
        Product.register_admin()  # already registered – must be silent

    def test_product_admin_has_list_display(self):
        from demo.apps.shop.models import Product
        model_admin = admin.site._registry[Product]
        assert len(model_admin.list_display) > 0

    def test_product_admin_has_search_fields(self):
        from demo.apps.shop.models import Product
        model_admin = admin.site._registry[Product]
        assert len(model_admin.search_fields) > 0

    def test_customer_admin_has_list_filter(self):
        """Customer.origin is filterable=True, must appear in list_filter."""
        from demo.apps.shop.models import Customer
        model_admin = admin.site._registry[Customer]
        assert len(model_admin.list_filter) > 0

    def test_order_admin_has_autocomplete_fields(self):
        from demo.apps.shop.models import Order
        model_admin = admin.site._registry[Order]
        assert "customer" in model_admin.autocomplete_fields


# ─────────────────────────────────────────────────────────────────────────────
# A project-supplied admin_overrides always wins (#ADM2a, DECISIONS.md D2)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminOverridesPrecedence:
    """The generated get_readonly_fields and safe_html_<field> display methods
    are merged into admin_attrs before admin_overrides, never written into
    admin_overrides itself — so a project's own callable of the same name
    always wins, regardless of write order."""

    def test_project_override_wins_over_generated_callables(self):
        from demo.apps.shop.models import Product

        def project_readonly(self, request, obj=None):
            return ["name"]

        def project_safe_html_description(self, obj):
            return "PROJECT OVERRIDE"

        original = Product.admin_overrides
        Product.admin_overrides = {
            "get_readonly_fields": project_readonly,
            "safe_html_description": project_safe_html_description,
        }
        admin.site.unregister(Product)
        try:
            Product.register_admin()
            admin_class = type(admin.site._registry[Product])
            assert admin_class.__dict__["get_readonly_fields"] is project_readonly
            assert admin_class.__dict__["safe_html_description"] is project_safe_html_description
        finally:
            Product.admin_overrides = original
            admin.site.unregister(Product)
            Product.register_admin()

    def test_generated_callables_still_apply_with_no_project_override(self):
        from demo.apps.shop.models import Product

        assert Product.admin_overrides == {}
        admin.site.unregister(Product)
        try:
            Product.register_admin()
            admin_class = type(admin.site._registry[Product])
            assert "get_readonly_fields" in admin_class.__dict__
            # Product.description is a wysiwyg field shown in list_display.
            assert "safe_html_description" in admin_class.__dict__
            assert admin_class.__dict__["get_readonly_fields"] is (
                Product._admin_generated_overrides["get_readonly_fields"]
            )
        finally:
            admin.site.unregister(Product)
            Product.register_admin()


# ─────────────────────────────────────────────────────────────────────────────
# get_admin_media() — byte-identical output for an unchanged model (#ADM2c)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetAdminMedia:
    def test_public_and_returns_two_lists(self):
        from demo.apps.shop.models import Product

        js, css = Product.get_admin_media()
        assert isinstance(js, list) and all(isinstance(x, str) for x in js)
        assert isinstance(css, list) and all(isinstance(x, str) for x in css)

    def test_matches_the_registered_admin_s_media_exactly(self):
        """The whole backward-compatibility story: register_admin() has no
        duplicate list literal left, it calls get_admin_media() directly."""
        from demo.apps.shop.models import Product

        model_admin = admin.site._registry[Product]
        js, css = Product.get_admin_media()
        assert list(model_admin.Media.js) == js
        assert list(model_admin.Media.css["all"]) == css

    def test_offline_js_only_appended_for_an_offline_capable_model(self):
        from demo.apps.shop.models import Customer, Product

        assert "snapadmin/js/offline.js" in Customer.get_admin_media()[0]
        assert "snapadmin/js/offline.js" not in Product.get_admin_media()[0]

    @override_settings(DEBUG=True)
    def test_unminified_jquery_in_debug(self):
        from demo.apps.shop.models import Product
        js, _ = Product.get_admin_media()
        assert "admin/js/vendor/jquery/jquery.js" in js
        assert "admin/js/vendor/jquery/jquery.min.js" not in js

    @override_settings(DEBUG=False)
    def test_minified_jquery_outside_debug(self):
        from demo.apps.shop.models import Product
        js, _ = Product.get_admin_media()
        assert "admin/js/vendor/jquery/jquery.min.js" in js
        assert "admin/js/vendor/jquery/jquery.js" not in js

    @override_settings(DEBUG=False)
    def test_merging_with_a_stock_modeladmin_yields_one_jquery_entry(self):
        """Off DEBUG, SnapAdmin must pick the same filename Django's own
        ModelAdmin.media does, or the media merge cannot collapse them and
        the browser downloads jQuery twice (#JS2b)."""
        from django import forms
        from demo.apps.shop.models import Product

        js, css = Product.get_admin_media()
        snap_media = forms.Media(js=js, css={"all": css})
        stock_media = admin.ModelAdmin(Product, admin.site).media
        merged = snap_media + stock_media
        jquery_entries = [f for f in merged._js if "vendor/jquery/jquery" in f]
        assert jquery_entries == ["admin/js/vendor/jquery/jquery.min.js"]


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
        url = reverse("admin:snapadmin_apitoken_changelist")
        assert admin_client.get(url).status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Names in a fieldset that are not model fields (re-homed in #QA1b)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestFieldsetNamesThatAreNotModelFields:
    """A generated fieldset may name something ``_meta.get_field`` cannot find.

    That is how an admin method or a ``SnapFunctionField``'s generated callable
    reaches the form. Both grouping passes — rows and tabs — have to pass such a
    name straight through rather than raise ``FieldDoesNotExist``, and neither
    may drop it, which would silently remove a field from the form.
    """

    def test_an_unknown_name_is_passed_through_to_the_fieldset(self):
        from unittest.mock import patch

        from demo.apps.shop.models import Product
        from snapadmin.models import AdminFieldSets

        fieldsets_with_a_non_field = AdminFieldSets(
            ["ghost_field", "name"], ["id"], ["id"], [], []
        )

        admin.site.unregister(Product)
        try:
            with patch.object(
                Product, "get_admin_fields", return_value=fieldsets_with_a_non_field
            ):
                Product.register_admin()

            generated = admin.site._registry[Product]
            declared = [
                name
                for _title, options in generated.fieldsets
                for entry in options["fields"]
                for name in (entry if isinstance(entry, tuple) else (entry,))
            ]
            assert declared == ["ghost_field", "name"]
        finally:
            admin.site.unregister(Product)
            Product.register_admin()


# ─────────────────────────────────────────────────────────────────────────────
# SnapSaveMixin — what an admin save records (re-homed in #QA1b)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSnapSaveMixinRecordsTheSave:
    """``SnapSaveMixin`` is what puts an admin edit into the audit trail.

    A create snapshots the initial values; an edit records only the fields that
    changed, in Django's own ``LogEntry`` as well as SnapAdmin's trail. An inline
    form whose field cannot be introspected must not abort the whole save — the
    other inlines still get logged.
    """

    def _generated_admin(self, model_class):
        from django.contrib.admin import ModelAdmin

        from snapadmin.models import SnapSaveMixin

        class GeneratedAdmin(SnapSaveMixin, ModelAdmin):
            pass

        instance = GeneratedAdmin(model_class, admin.site)
        instance.model = model_class
        return instance

    def _request(self, username):
        from django.contrib.auth.models import User
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser(username, password="pass")
        return request

    def test_creating_an_object_saves_it_and_records_a_create_entry(self):
        from decimal import Decimal

        from demo.apps.shop.models import Product
        from snapadmin.models import SnapadminAuditLog

        request = self._request("savemixin_new")
        product = Product(name="Fresh", price=Decimal("12.00"))

        class NewObjectForm:
            changed_data = []
            initial = {}
            cleaned_data = {"name": "Fresh"}

        self._generated_admin(Product).save_model(
            request, product, NewObjectForm(), change=False
        )

        assert Product.objects.filter(pk=product.pk, name="Fresh").exists()
        entry = SnapadminAuditLog.objects.filter(
            object_repr=str(product), action="create"
        ).latest("timestamp")
        assert entry.changes["name"]["new"] == "Fresh"
        assert entry.changes["name"]["old"] is None

    def test_an_inline_whose_field_cannot_be_read_does_not_stop_the_others(self):
        from decimal import Decimal
        from unittest.mock import MagicMock

        from django.contrib.admin.models import LogEntry

        from demo.apps.shop.models import Product

        product = Product.objects.create(name="Parent", price=Decimal("20.00"))
        request = self._request("saverelated")

        changed_inline = MagicMock()
        changed_inline.instance = product
        changed_inline.has_changed.return_value = True
        changed_inline.changed_data = ["name"]
        changed_inline.initial = {"name": "Parent"}
        changed_inline.cleaned_data = {"name": "Renamed"}

        uninspectable_inline = MagicMock()
        uninspectable_inline.instance.pk = 999
        uninspectable_inline.instance._meta.get_field.side_effect = Exception("no such field")
        uninspectable_inline.has_changed.return_value = True
        uninspectable_inline.changed_data = ["mystery"]
        uninspectable_inline.initial = {"mystery": "a"}
        uninspectable_inline.cleaned_data = {"mystery": "b"}

        formset = MagicMock()
        formset.has_changed.return_value = True
        formset.forms = [changed_inline, uninspectable_inline]

        before = set(LogEntry.objects.values_list("pk", flat=True))
        self._generated_admin(Product).save_related(
            request, MagicMock(), [formset], change=True
        )
        written = LogEntry.objects.exclude(pk__in=before)

        # Exactly one entry: the readable inline's. The other was skipped, not
        # logged blank and not allowed to raise.
        assert written.count() == 1
        assert "Parent" in written.get().change_message
        assert "Renamed" in written.get().change_message
