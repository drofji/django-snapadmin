"""Generated-admin integration fixes from the second field report (#EXT2g/h/k).

Each class pins one thing a project integrating SnapAdmin next to its own
hand-written admin code ran into:

* the generated ``ModelAdmin`` class names a module it does not live in (k);
* a masked changelist column loses the model's own ``verbose_name`` (g2);
* the primary-key column is forced into every changelist, by the literal name
  ``"id"``, even for UUID keys and keys named something else (g3);
* the API-token admin is offered while no API that accepts tokens is on (h).
"""

import uuid

import pytest
from django.contrib import admin
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from snapadmin import fields as snapfields
from snapadmin.models import APIToken, PIIMaskingAdminMixin, SnapModel


# ─────────────────────────────────────────────────────────────────────────────
# #EXT2k — __module__ of a generated admin class
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGeneratedAdminModule:
    def test_a_generated_admin_class_lives_in_its_model_s_module(self):
        from demo.apps.shop.models import Product

        registered = admin.site._registry[Product]

        assert getattr(registered, "snapadmin_generated_admin", False) is True
        assert type(registered).__module__ == Product.__module__
        assert type(registered).__qualname__ == "ProductAdmin"

    def test_every_generated_admin_names_its_model_s_module(self):
        generated = [
            (model, model_admin) for model, model_admin in admin.site._registry.items()
            if getattr(model_admin, "snapadmin_generated_admin", False)
        ]

        assert generated
        assert all(type(a).__module__ == m.__module__ for m, a in generated)


# ─────────────────────────────────────────────────────────────────────────────
# #EXT2g2 — masked column label
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def masked_host():
    """A concrete model kept out of the real app registry."""
    with isolate_apps("demo"):
        class MaskedHost(models.Model):
            tax_number = models.CharField("Steuernummer", max_length=20)

            class Meta:
                app_label = "demo"

        yield MaskedHost


class _MaskingAdmin(PIIMaskingAdminMixin, admin.ModelAdmin):
    pass


class TestMaskedColumnLabel:
    @pytest.fixture(autouse=True)
    def _host(self, masked_host):
        self.model = masked_host

    def _column(self, field_name: str):
        model_admin = _MaskingAdmin(self.model, admin.AdminSite(name="ext2g2"))
        return model_admin._snap_mask_column(field_name)

    def test_the_label_is_the_field_s_own_verbose_name(self):
        assert self._column("tax_number").short_description == "Steuernummer"

    def test_a_name_that_is_not_a_model_field_falls_back_to_a_readable_label(self):
        assert self._column("computed_secret").short_description == "Computed Secret"

    def test_a_lazy_verbose_name_stays_lazy_so_it_follows_the_request_language(self):
        from django.utils.translation import gettext_lazy

        field = self.model._meta.get_field("tax_number")
        original = field.verbose_name
        lazy_label = gettext_lazy("Tax number")
        field.verbose_name = lazy_label
        try:
            assert self._column("tax_number").short_description is lazy_label
        finally:
            field.verbose_name = original


# ─────────────────────────────────────────────────────────────────────────────
# #EXT2g3 — the primary-key changelist column
# ─────────────────────────────────────────────────────────────────────────────

class _IntegerKeyModel(SnapModel):
    title = snapfields.SnapCharField(max_length=20)

    class Meta:
        app_label = "demo"
        abstract = True


class _UuidKeyModel(SnapModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    title = snapfields.SnapCharField(max_length=20)

    class Meta:
        app_label = "demo"
        abstract = True


class _RenamedIntegerKeyModel(SnapModel):
    record_no = models.BigAutoField(primary_key=True)
    title = snapfields.SnapCharField(max_length=20)

    class Meta:
        app_label = "demo"
        abstract = True


class _UuidKeyShownModel(_UuidKeyModel):
    admin_list_display_pk = True

    class Meta:
        app_label = "demo"
        abstract = True


class _IntegerKeyHiddenModel(_IntegerKeyModel):
    admin_list_display_pk = False

    class Meta:
        app_label = "demo"
        abstract = True


class TestPrimaryKeyColumn:
    def test_an_integer_key_is_shown_first_by_default(self):
        _form, list_display, search, *_ = _IntegerKeyModel.get_admin_fields()

        assert list_display[0] == "id"
        assert "id" in search

    def test_a_uuid_key_is_not_shown_by_default(self):
        _form, list_display, *_ = _UuidKeyModel.get_admin_fields()

        assert "id" not in list_display
        assert list_display == ["title"]

    def test_a_key_not_named_id_is_referenced_by_its_real_name(self):
        _form, list_display, search, *_ = _RenamedIntegerKeyModel.get_admin_fields()

        assert list_display[0] == "record_no"
        assert "id" not in list_display
        assert "record_no" in search
        assert "id" not in search

    def test_the_model_can_force_a_uuid_key_into_the_changelist(self):
        _form, list_display, *_ = _UuidKeyShownModel.get_admin_fields()

        assert list_display[0] == "id"

    def test_the_model_can_hide_an_integer_key(self):
        _form, list_display, *_ = _IntegerKeyHiddenModel.get_admin_fields()

        assert "id" not in list_display

    def test_the_default_is_the_automatic_rule(self):
        assert SnapModel.admin_list_display_pk is None


# ─────────────────────────────────────────────────────────────────────────────
# #EXT2h — the API-token admin follows the APIs that accept tokens
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def token_admin_unregistered():
    """Start from no APIToken registration; put back exactly what was there."""
    original = admin.site._registry.pop(APIToken, None)
    yield
    admin.site._registry.pop(APIToken, None)
    if original is not None:
        admin.site._registry[APIToken] = original


@pytest.mark.django_db
@pytest.mark.usefixtures("token_admin_unregistered")
class TestTokenAdminRegistration:
    @override_settings(SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_GRAPHQL_ENABLED=False)
    def test_not_registered_while_no_token_api_is_on(self):
        SnapModel.register_all_admins()

        assert APIToken not in admin.site._registry

    @override_settings(SNAPADMIN_REST_API_ENABLED=True, SNAPADMIN_GRAPHQL_ENABLED=False)
    def test_registered_with_the_rest_api_on(self):
        SnapModel.register_all_admins()

        assert APIToken in admin.site._registry

    @override_settings(SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_GRAPHQL_ENABLED=True)
    def test_registered_with_graphql_on(self):
        SnapModel.register_all_admins()

        assert APIToken in admin.site._registry

    @override_settings(
        SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_GRAPHQL_ENABLED=False,
        SNAPADMIN_TOKEN_ADMIN_ENABLED=True,
    )
    def test_a_project_using_token_auth_in_its_own_views_can_force_it_on(self):
        SnapModel.register_all_admins()

        assert APIToken in admin.site._registry

    @override_settings(
        SNAPADMIN_REST_API_ENABLED=True, SNAPADMIN_GRAPHQL_ENABLED=True,
        SNAPADMIN_TOKEN_ADMIN_ENABLED=False,
    )
    def test_a_project_can_force_it_off(self):
        SnapModel.register_all_admins()

        assert APIToken not in admin.site._registry

    @override_settings(SNAPADMIN_REST_API_ENABLED=False, SNAPADMIN_GRAPHQL_ENABLED=False)
    def test_the_audit_and_error_admins_do_not_depend_on_the_apis(self):
        from snapadmin.models import ErrorEvent, SnapadminAuditLog

        SnapModel.register_all_admins()

        assert ErrorEvent in admin.site._registry
        assert SnapadminAuditLog in admin.site._registry


# ─────────────────────────────────────────────────────────────────────────────
# #QA1d — generated-admin branches no test reached
# ─────────────────────────────────────────────────────────────────────────────

class _SearchableKeyModel(SnapModel):
    id = snapfields.snap_field(models.BigAutoField(primary_key=True), searchable=True)
    title = snapfields.SnapCharField(max_length=20)

    class Meta:
        app_label = "demo"
        abstract = True


class _HiddenRichTextModel(SnapModel):
    body = snapfields.SnapTextField(wysiwyg=True, show_in_list=False)

    class Meta:
        app_label = "demo"
        abstract = True


class TestGeneratedAdminBranches:
    def test_a_searchable_primary_key_is_listed_once_in_search_fields(self):
        _form, _list, search, *_ = _SearchableKeyModel.get_admin_fields()

        assert search.count("id") == 1

    def test_a_rich_text_field_hidden_from_the_list_gets_no_display_method(self):
        _form, list_display, *_ = _HiddenRichTextModel.get_admin_fields()

        assert "safe_html_body" not in list_display
        assert "safe_html_body" not in _HiddenRichTextModel._admin_generated_overrides

    @pytest.mark.django_db
    def test_a_row_whose_every_field_is_masked_disappears_from_the_form(self, rf):
        from django.contrib.auth import get_user_model
        from demo.apps.shop.models import Category

        staff = get_user_model().objects.create_user("rowmask", password="x", is_staff=True)
        request = rf.get("/")
        request.user = staff
        with override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Category": ["slug", "is_active"]}):
            fieldsets = admin.site._registry[Category].get_fieldsets(request)

        shown = [f for _name, opts in fieldsets for f in opts["fields"]]
        flat = {x for f in shown for x in (f if isinstance(f, tuple) else (f,))}
        assert {"slug", "is_active"}.isdisjoint(flat)
        assert "name" in flat

    @pytest.mark.django_db
    def test_without_unfold_rows_get_no_unfold_row_class(self, rf, monkeypatch):
        from django.contrib.auth import get_user_model
        from demo.apps.shop.models import Category
        from snapadmin import admin_gen

        monkeypatch.setattr(admin_gen, "UNFOLD_INSTALLED", False)
        request = rf.get("/")
        request.user = get_user_model().objects.create_superuser("nounfold", password="x")

        fieldsets = admin.site._registry[Category].get_fieldsets(request)

        assert all("snap-field-row" not in opts.get("classes", ()) for _n, opts in fieldsets)

    @pytest.mark.django_db
    def test_registering_another_app_leaves_this_one_alone(self):
        from demo.apps.shop.models import Tag

        original = admin.site._registry.pop(Tag)
        try:
            SnapModel.register_all_admins(app_label="some_other_app")
            assert Tag not in admin.site._registry
        finally:
            admin.site._registry[Tag] = original
