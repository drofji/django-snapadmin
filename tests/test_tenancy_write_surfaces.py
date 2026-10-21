"""
tests/test_tenancy_write_surfaces.py — every write surface × every tenant-column shape.

The class guard for #EXT3b and #EXT3c. Every earlier tenancy test used a string
column (``tenant_id = tenant_field()``), so two defects stayed invisible:

* a tenant column declared as a real ``ForeignKey`` — the docs' own recipe when
  the tenant is a project model — broke every create: the admin assigned the
  resolver's raw value to the relation descriptor (``ValueError``), the REST API
  did the same through ``serializer.save()``, and an update compared a related
  row with a raw value and always refused it;
* a tenant-scoped row created through an admin **inline** skipped tenant
  stamping (Django's formset saves it, not ``save_model``) and became an orphan
  nobody could ever see, with no audit entry either.

The matrix below runs each write surface against each shape, so a new surface or
a new shape has to pass all of it. Test models are isolated (``isolate_apps``)
and get real tables, the precedent ``tests/test_retention_protected_rows.py``
set.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.db import connection, models
from django.forms.models import inlineformset_factory
from django.test import RequestFactory
from django.test.utils import isolate_apps
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from snapadmin import tenancy
from snapadmin.models import SnapModel, SnapSaveMixin, SnapadminAuditLog


def _build_models() -> SimpleNamespace:
    with isolate_apps("snapadmin"):

        class Organisation(models.Model):
            name = models.CharField(max_length=40)

            class Meta:
                app_label = "snapadmin"

        class CharTenantNote(SnapModel):
            tenant_id = tenancy.tenant_field()
            text = models.CharField(max_length=40)
            tenant_scoped = True

            class Meta:
                app_label = "snapadmin"

        class FkTenantNote(SnapModel):
            organisation = models.ForeignKey(Organisation, null=True, on_delete=models.CASCADE)
            text = models.CharField(max_length=40)
            tenant_scoped = True
            tenant_field = "organisation"

            class Meta:
                app_label = "snapadmin"

        class FkColumnTenantNote(SnapModel):
            organisation = models.ForeignKey(Organisation, null=True, on_delete=models.CASCADE)
            text = models.CharField(max_length=40)
            tenant_scoped = True
            tenant_field = "organisation_id"

            class Meta:
                app_label = "snapadmin"

        class Notebook(SnapModel):
            title = models.CharField(max_length=40)

            class Meta:
                app_label = "snapadmin"

        class NotebookPage(SnapModel):
            notebook = models.ForeignKey(Notebook, on_delete=models.CASCADE, related_name="pages")
            tenant_id = tenancy.tenant_field()
            text = models.CharField(max_length=40)
            tenant_scoped = True

            class Meta:
                app_label = "snapadmin"

        return SimpleNamespace(
            Organisation=Organisation,
            CharTenantNote=CharTenantNote,
            FkTenantNote=FkTenantNote,
            FkColumnTenantNote=FkColumnTenantNote,
            Notebook=Notebook,
            NotebookPage=NotebookPage,
        )


@pytest.fixture
def tenant_models(transactional_db):
    built = _build_models()
    ordered = [
        built.Organisation, built.CharTenantNote, built.FkTenantNote,
        built.FkColumnTenantNote, built.Notebook, built.NotebookPage,
    ]
    with connection.schema_editor(atomic=False) as editor:
        for model in ordered:
            editor.create_model(model)
    try:
        yield built
    finally:
        with connection.schema_editor(atomic=False) as editor:
            for model in reversed(ordered):
                editor.delete_model(model)


#: (model attribute, the column a tenant value lands in)
SHAPES = [
    ("CharTenantNote", "tenant_id"),
    ("FkTenantNote", "organisation_id"),
    ("FkColumnTenantNote", "organisation_id"),
]


def _tenant_value_for(shape: str, models_ns) -> object:
    if shape == "CharTenantNote":
        return "acme"
    return models_ns.Organisation.objects.create(name="Acme").pk


def _request(user):
    request = RequestFactory().post("/admin/x/")
    request.user = user
    return request


def _admin_for(model):
    return type(f"{model.__name__}Admin", (SnapSaveMixin, admin.ModelAdmin), {})(model, admin.site)


def _serializer_for(model):
    meta = type("Meta", (), {"model": model, "fields": "__all__"})
    return type(f"{model.__name__}Serializer", (serializers.ModelSerializer,), {"Meta": meta})


def _viewset_for(model, user):
    from snapadmin.api.views import DynamicModelViewSet

    view = DynamicModelViewSet()
    view.request = SimpleNamespace(user=user)
    view._get_model_class = lambda: model
    return view


class TestTheColumnATenantValueIsWrittenTo:
    @pytest.mark.parametrize(("shape", "column"), SHAPES)
    def test_each_shape_resolves_to_its_concrete_column(self, tenant_models, shape, column):
        assert tenancy._tenant_attname(getattr(tenant_models, shape)) == column

    def test_a_tenant_field_naming_no_field_is_returned_as_written(self, tenant_models, monkeypatch):
        model = tenant_models.CharTenantNote
        monkeypatch.setattr(model, "tenant_field", "no_such_column")

        assert tenancy._tenant_attname(model) == "no_such_column"
        assert tenancy._tenant_field_names(model) == {"no_such_column"}
        assert tenancy._tenant_model_field_name(model) == "no_such_column"

    def test_every_name_a_foreign_key_tenant_column_answers_to(self, tenant_models):
        assert tenancy._tenant_field_names(tenant_models.FkTenantNote) == {
            "organisation", "organisation_id"
        }
        assert tenancy._tenant_field_names(tenant_models.FkColumnTenantNote) == {
            "organisation", "organisation_id"
        }

    def test_a_related_row_is_reduced_to_its_key(self, tenant_models):
        organisation = tenant_models.Organisation.objects.create(name="Acme")

        assert tenancy._tenant_value(organisation) == organisation.pk
        assert tenancy._tenant_value("acme") == "acme"


class TestAdminCreate:
    @pytest.mark.parametrize(("shape", "column"), SHAPES)
    def test_the_bound_tenant_is_stamped_whatever_the_column_shape(
        self, tenant_models, admin_user, shape, column
    ):
        model = getattr(tenant_models, shape)
        tenant_value = _tenant_value_for(shape, tenant_models)
        note = model(text="created in the admin")
        form = SimpleNamespace(cleaned_data={"text": note.text}, changed_data=[], initial={})

        with tenancy.use_tenant(tenant_value):
            _admin_for(model).save_model(_request(admin_user), note, form, change=False)

        stored = model._base_manager.get(pk=note.pk)
        assert getattr(stored, column) == tenant_value, (
            f"review: the admin stamped the wrong tenant on a {shape} create — "
            "SnapSaveMixin._stamp_tenant / tenancy._tenant_attname"
        )


class TestRestApiCreateAndUpdate:
    @pytest.mark.parametrize(("shape", "column"), SHAPES)
    def test_create_stamps_the_bound_tenant_whatever_the_column_shape(
        self, tenant_models, admin_user, shape, column
    ):
        model = getattr(tenant_models, shape)
        tenant_value = _tenant_value_for(shape, tenant_models)
        serializer = _serializer_for(model)(data={"text": "created over REST"})
        assert serializer.is_valid(), serializer.errors

        with tenancy.use_tenant(tenant_value):
            _viewset_for(model, admin_user).perform_create(serializer)

        assert getattr(model._base_manager.get(text="created over REST"), column) == tenant_value, (
            f"review: the REST API stamped the wrong tenant on a {shape} create — "
            "DynamicModelViewSet.perform_create"
        )

    @pytest.mark.parametrize("shape", ["FkTenantNote", "FkColumnTenantNote"])
    def test_create_accepts_the_bound_tenant_supplied_explicitly(self, tenant_models, admin_user, shape):
        model = getattr(tenant_models, shape)
        organisation_pk = _tenant_value_for(shape, tenant_models)
        serializer = _serializer_for(model)(data={"text": "explicit", "organisation": organisation_pk})
        assert serializer.is_valid(), serializer.errors

        with tenancy.use_tenant(organisation_pk):
            _viewset_for(model, admin_user).perform_create(serializer)

        assert model._base_manager.get(text="explicit").organisation_id == organisation_pk

    @pytest.mark.parametrize("shape", ["FkTenantNote", "FkColumnTenantNote"])
    def test_create_refuses_a_different_tenant(self, tenant_models, admin_user, shape):
        model = getattr(tenant_models, shape)
        bound = _tenant_value_for(shape, tenant_models)
        other = tenant_models.Organisation.objects.create(name="Other").pk
        serializer = _serializer_for(model)(data={"text": "smuggled", "organisation": other})
        assert serializer.is_valid(), serializer.errors

        with tenancy.use_tenant(bound), pytest.raises(ValidationError) as refused:
            _viewset_for(model, admin_user).perform_create(serializer)

        assert "organisation" in str(refused.value.detail)
        assert not model._base_manager.filter(text="smuggled").exists()

    @pytest.mark.parametrize("shape", ["FkTenantNote", "FkColumnTenantNote"])
    def test_update_naming_the_same_tenant_is_accepted(self, tenant_models, admin_user, shape):
        model = getattr(tenant_models, shape)
        organisation_pk = _tenant_value_for(shape, tenant_models)
        note = model._base_manager.create(text="before", organisation_id=organisation_pk)
        serializer = _serializer_for(model)(
            note, data={"text": "after", "organisation": organisation_pk}, partial=True
        )
        assert serializer.is_valid(), serializer.errors

        with tenancy.use_tenant(organisation_pk):
            _viewset_for(model, admin_user).perform_update(serializer)

        assert model._base_manager.get(pk=note.pk).text == "after"

    @pytest.mark.parametrize("shape", ["FkTenantNote", "FkColumnTenantNote"])
    def test_update_moving_the_row_to_another_tenant_is_refused(self, tenant_models, admin_user, shape):
        model = getattr(tenant_models, shape)
        organisation_pk = _tenant_value_for(shape, tenant_models)
        other = tenant_models.Organisation.objects.create(name="Other").pk
        note = model._base_manager.create(text="stays", organisation_id=organisation_pk)
        serializer = _serializer_for(model)(note, data={"organisation": other}, partial=True)
        assert serializer.is_valid(), serializer.errors

        with tenancy.use_tenant(organisation_pk), pytest.raises(ValidationError):
            _viewset_for(model, admin_user).perform_update(serializer)

        assert model._base_manager.get(pk=note.pk).organisation_id == organisation_pk


def _page_formset(tenant_models, notebook, data):
    page_formset = inlineformset_factory(
        tenant_models.Notebook, tenant_models.NotebookPage, fields=["text"], extra=1, can_delete=True
    )
    return page_formset(data, instance=notebook, prefix="pages")


def _management(total: int, initial: int) -> dict:
    return {
        "pages-TOTAL_FORMS": str(total),
        "pages-INITIAL_FORMS": str(initial),
        "pages-MIN_NUM_FORMS": "0",
        "pages-MAX_NUM_FORMS": "1000",
    }


class TestAdminInlineFormset:
    """The child rows of an inline go through the same guards as the parent row."""

    def test_a_tenant_scoped_child_added_inline_is_stamped_and_audited(self, tenant_models, admin_user):
        notebook = tenant_models.Notebook.objects.create(title="Field notes")
        formset = _page_formset(
            tenant_models, notebook, {**_management(1, 0), "pages-0-text": "first page"}
        )
        assert formset.is_valid(), formset.errors

        with tenancy.use_tenant("acme"):
            _admin_for(tenant_models.Notebook).save_formset(
                _request(admin_user), None, formset, change=True
            )

        page = tenant_models.NotebookPage._base_manager.get(text="first page")
        assert page.tenant_id == "acme"
        entry = SnapadminAuditLog.objects.get(model="notebookpage", object_id=str(page.pk))
        assert entry.action == "create"
        assert entry.changes["text"] == {"old": None, "new": "first page"}

    def test_a_child_added_inline_with_no_tenant_bound_is_refused(self, tenant_models, admin_user):
        from django.core.exceptions import PermissionDenied

        notebook = tenant_models.Notebook.objects.create(title="Field notes")
        formset = _page_formset(
            tenant_models, notebook, {**_management(1, 0), "pages-0-text": "orphan"}
        )
        assert formset.is_valid(), formset.errors

        with pytest.raises(PermissionDenied):
            _admin_for(tenant_models.Notebook).save_formset(
                _request(admin_user), None, formset, change=True
            )

        assert not tenant_models.NotebookPage._base_manager.filter(text="orphan").exists()

    def test_a_child_changed_inline_is_audited_with_its_diff(self, tenant_models, admin_user):
        notebook = tenant_models.Notebook.objects.create(title="Field notes")
        page = tenant_models.NotebookPage._base_manager.create(
            notebook=notebook, text="draft", tenant_id="acme"
        )
        # The request's tenant is bound before the formset validates (middleware),
        # and a tenant-scoped manager shows nothing without one.
        with tenancy.use_tenant("acme"):
            formset = _page_formset(
                tenant_models,
                notebook,
                {**_management(1, 1), "pages-0-id": str(page.pk), "pages-0-notebook": str(notebook.pk),
                 "pages-0-text": "final"},
            )
            assert formset.is_valid(), formset.errors
            _admin_for(tenant_models.Notebook).save_formset(
                _request(admin_user), None, formset, change=True
            )

        page.refresh_from_db()
        assert page.text == "final"
        assert page.tenant_id == "acme"
        entry = SnapadminAuditLog.objects.get(model="notebookpage", object_id=str(page.pk))
        assert entry.action == "update"
        assert entry.changes == {"text": {"old": "draft", "new": "final"}}

    def test_a_child_deleted_inline_is_audited_and_removed(self, tenant_models, admin_user):
        notebook = tenant_models.Notebook.objects.create(title="Field notes")
        page = tenant_models.NotebookPage._base_manager.create(
            notebook=notebook, text="obsolete", tenant_id="acme"
        )
        with tenancy.use_tenant("acme"):
            formset = _page_formset(
                tenant_models,
                notebook,
                {**_management(1, 1), "pages-0-id": str(page.pk), "pages-0-notebook": str(notebook.pk),
                 "pages-0-text": "obsolete", "pages-0-DELETE": "on"},
            )
            assert formset.is_valid(), formset.errors
            _admin_for(tenant_models.Notebook).save_formset(
                _request(admin_user), None, formset, change=True
            )

        assert not tenant_models.NotebookPage._base_manager.filter(pk=page.pk).exists()
        entry = SnapadminAuditLog.objects.get(model="notebookpage", object_id=str(page.pk))
        assert entry.action == "delete"
