"""
tests/test_tenancy_operator_binding.py — creating a row under the all-tenants binding (#EXT3q).

A resolver (or a project's own middleware) binds :data:`snapadmin.tenancy.ALL_TENANTS`
for an operator who works across tenants. Every create surface treated that binding
like "no tenant" and refused — even when the operator named the tenant the row
belongs to — so an operator could read every tenant's rows and create none. Worse,
the importer took the binding as a value: it would have written the sentinel
object itself (``start_import(tenant=ALL_TENANTS)`` stored its ``str()``), an
orphan no tenant could ever see.

Now, under ``ALL_TENANTS``, the generated add form exposes the tenant field
(required) and the admin and the REST API take the tenant the operator names; with
no tenant named — and with no binding at all — they still refuse. The importer
refuses the sentinel outright.

Missed because: the tenancy tests covered "no tenant" and "one tenant"; the
operator binding was only ever exercised on reads. The class guard below is the
#EXT3b write-surface matrix with the binding as a second axis, every cell's
outcome written down.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied

from snapadmin import tenancy
from snapadmin.models import SnapadminAuditLog
from tests.browser_forms import submit_like_a_browser
from tests.test_tenancy_write_surfaces import (  # noqa: F401 - the fixture is used by name
    _admin_for,
    _page_formset,
    _management,
    _request,
    _serializer_for,
    _viewset_for,
    tenant_models,
)
from types import SimpleNamespace


# ── One cell per (surface, binding): what must happen ───────────────────────

BOUND = "acme"
CHOSEN = "globex"


def _admin_create(models_ns, user, *, form_tenant: str | None):
    model = models_ns.CharTenantNote
    note = model(text="admin", tenant_id=form_tenant)
    cleaned = {"text": note.text, **({"tenant_id": form_tenant} if form_tenant else {})}
    form = SimpleNamespace(cleaned_data=cleaned, changed_data=[], initial={})
    _admin_for(model).save_model(_request(user), note, form, change=False)
    return model._base_manager.get(text="admin").tenant_id


def _rest_create(models_ns, user, *, form_tenant: str | None):
    model = models_ns.CharTenantNote
    data = {"text": "rest", **({"tenant_id": form_tenant} if form_tenant else {})}
    serializer = _serializer_for(model)(data=data)
    assert serializer.is_valid(), serializer.errors
    _viewset_for(model, user).perform_create(serializer)
    return model._base_manager.get(text="rest").tenant_id


def _inline_create(models_ns, user, *, form_tenant: str | None):
    notebook = models_ns.Notebook.objects.create(title="Field notes")
    formset = _page_formset(models_ns, notebook, {**_management(1, 0), "pages-0-text": "inline"})
    assert formset.is_valid(), formset.errors
    _admin_for(models_ns.Notebook).save_formset(_request(user), None, formset, change=True)
    return models_ns.NotebookPage._base_manager.get(text="inline").tenant_id


def _bind(binding: str):
    if binding == "none":
        return tenancy.use_tenant(None)
    if binding == "one tenant":
        return tenancy.use_tenant(BOUND)
    return tenancy.use_all_tenants()


SURFACES = {"admin create": _admin_create, "REST create": _rest_create, "inline create": _inline_create}
BINDINGS = ["none", "one tenant", "all tenants"]

REFUSED = "refused"

#: (surface, binding, tenant the form/body names) → the stored tenant, or REFUSED.
EXPECTED = {
    ("admin create", "none", None): REFUSED,
    ("admin create", "none", CHOSEN): REFUSED,
    ("admin create", "one tenant", None): BOUND,
    # A single-tenant binding forces its own tenant, whatever the form held.
    ("admin create", "one tenant", CHOSEN): BOUND,
    ("admin create", "all tenants", None): REFUSED,
    ("admin create", "all tenants", CHOSEN): CHOSEN,
    ("REST create", "none", None): REFUSED,
    ("REST create", "none", CHOSEN): REFUSED,
    ("REST create", "one tenant", None): BOUND,
    # A body naming another tenant than the bound one is a 400 — the #EXT3b rule.
    ("REST create", "one tenant", CHOSEN): "400",
    ("REST create", "all tenants", None): REFUSED,
    ("REST create", "all tenants", CHOSEN): CHOSEN,
    # An inline child names no tenant (its form has no tenant field).
    ("inline create", "none", None): REFUSED,
    ("inline create", "one tenant", None): BOUND,
    ("inline create", "all tenants", None): REFUSED,
}


def test_every_surface_and_binding_has_an_expected_outcome():
    named = {"admin create": [None, CHOSEN], "REST create": [None, CHOSEN], "inline create": [None]}
    cells = {
        (surface, binding, tenant)
        for surface in SURFACES
        for binding in BINDINGS
        for tenant in named[surface]
    }

    assert cells - set(EXPECTED) == set(), (
        "review: a tenant write surface × binding has no expected outcome — write it down in "
        "EXPECTED in tests/test_tenancy_operator_binding.py"
    )


@pytest.mark.parametrize(("surface", "binding", "form_tenant"), sorted(EXPECTED, key=str))
def test_the_create_does_what_the_binding_allows(
    tenant_models, admin_user, surface, binding, form_tenant  # noqa: F811
):
    from rest_framework.exceptions import ValidationError

    expected = EXPECTED[(surface, binding, form_tenant)]

    with _bind(binding):
        try:
            outcome = SURFACES[surface](tenant_models, admin_user, form_tenant=form_tenant)
        except (PermissionDenied, ApiPermissionDenied):
            outcome = REFUSED
        except ValidationError:
            outcome = "400"

    assert outcome == expected, (
        f"review: {surface} under the {binding!r} binding with tenant {form_tenant!r} gave "
        f"{outcome!r}, expected {expected!r}"
    )


def test_the_admin_audit_entry_records_the_chosen_tenant(tenant_models, admin_user):  # noqa: F811
    with tenancy.use_all_tenants():
        _admin_create(tenant_models, admin_user, form_tenant=CHOSEN)

    note = tenant_models.CharTenantNote._base_manager.get(text="admin")
    entry = SnapadminAuditLog.objects.get(model="chartenantnote", object_id=str(note.pk))
    assert entry.changes["tenant_id"] == {"old": None, "new": CHOSEN}


@pytest.mark.parametrize("shape", ["FkTenantNote", "FkColumnTenantNote"])
def test_a_foreign_key_tenant_named_over_rest_is_stored_by_its_key(
    tenant_models, admin_user, shape  # noqa: F811
):
    model = getattr(tenant_models, shape)
    organisation = tenant_models.Organisation.objects.create(name="Globex")
    serializer = _serializer_for(model)(data={"text": "fk", "organisation": organisation.pk})
    assert serializer.is_valid(), serializer.errors

    with tenancy.use_all_tenants():
        _viewset_for(model, admin_user).perform_create(serializer)

    assert model._base_manager.get(text="fk").organisation_id == organisation.pk


# ── The generated admin: the add form asks for the tenant ──────────────────


def _operator_resolver(request):
    return tenancy.ALL_TENANTS


@pytest.fixture
def operator_binding(settings):
    settings.SNAPADMIN_TENANT_RESOLVER = _operator_resolver


@pytest.mark.django_db
class TestTheGeneratedAddForm:
    def test_under_all_tenants_the_add_form_has_a_required_tenant_field(
        self, admin_client, operator_binding
    ):
        response = admin_client.get("/admin/demo/order/add/")

        form = response.context["adminform"].form
        assert response.status_code == 200
        assert "tenant_id" in form.fields
        assert form.fields["tenant_id"].required is True

    def test_under_one_tenant_the_add_form_has_no_tenant_field(self, admin_client):
        response = admin_client.get("/admin/demo/order/add/")

        assert response.status_code == 200
        assert "tenant_id" not in response.context["adminform"].form.fields

    def test_the_change_form_never_has_it(self, admin_client, operator_binding, order):
        response = admin_client.get(f"/admin/demo/order/{order.pk}/change/")

        assert response.status_code == 200
        assert "tenant_id" not in response.context["adminform"].form.fields

    def test_an_operator_creates_a_row_for_the_tenant_they_name(
        self, admin_client, operator_binding, customer
    ):
        from demo.apps.shop.models import Order

        response = submit_like_a_browser(
            admin_client,
            "/admin/demo/order/add/",
            {"customer": [str(customer.pk)], "total": ["10.00"], "tenant_id": [CHOSEN]},
            form_id="order_form",
        )

        assert response.status_code == 302, response.context["adminform"].form.errors
        assert Order._base_manager.get().tenant_id == CHOSEN

    def test_an_operator_who_names_no_tenant_gets_a_form_error(
        self, admin_client, operator_binding, customer
    ):
        from demo.apps.shop.models import Order

        response = submit_like_a_browser(
            admin_client,
            "/admin/demo/order/add/",
            {"customer": [str(customer.pk)], "total": ["10.00"]},
            form_id="order_form",
        )

        assert response.status_code == 200
        assert "tenant_id" in response.context["adminform"].form.errors
        assert not Order._base_manager.exists()


# ── The importer refuses the sentinel ──────────────────────────────────────


@pytest.mark.django_db
class TestTheImporterRefusesTheSentinel:
    def test_start_import_refuses_all_tenants_as_a_tenant(self, tmp_path):
        from snapadmin.importing import SnapImportError, start_import
        from demo.apps.shop.models import Order

        source = tmp_path / "orders.csv"
        source.write_text("total\n1.00\n")

        with pytest.raises(SnapImportError, match="ALL_TENANTS"):
            start_import(Order, file_path=str(source), tenant=tenancy.ALL_TENANTS)

    def test_a_row_created_under_the_all_tenants_binding_fails_instead_of_storing_the_sentinel(
        self, customer
    ):
        from snapadmin import importing
        from demo.apps.shop.models import Order

        with tenancy.use_all_tenants():
            result = importing._process_row(
                Order, {"total": "total", "customer": "customer"}, None, "skip", 2,
                {"total": "1.00", "customer": str(customer.pk)},
            )

        assert result["action"] == "failed"
        assert "tenant" in result["errors"]["__all__"][0]
        assert not Order._base_manager.exists()

    def test_a_row_created_with_no_tenant_bound_fails_instead_of_an_orphan(self, customer):
        from snapadmin import importing
        from demo.apps.shop.models import Order

        with tenancy.use_tenant(None):
            result = importing._process_row(
                Order, {"total": "total", "customer": "customer"}, None, "skip", 2,
                {"total": "1.00", "customer": str(customer.pk)},
            )

        assert result["action"] == "failed"
        assert not Order._base_manager.exists()


class TestWhereTheTenantFieldGoes:
    def test_first_in_the_untabbed_fieldset(self):
        from snapadmin.admin_gen import _with_leading_field

        fieldsets = [("Tab", {"fields": ["a"], "classes": ("tab",)}), (None, {"fields": ["b"]})]

        assert _with_leading_field(fieldsets, "tenant_id") == [
            ("Tab", {"fields": ["a"], "classes": ("tab",)}),
            (None, {"fields": ["tenant_id", "b"]}),
        ]

    def test_a_fieldset_of_its_own_when_every_field_sits_in_a_tab(self):
        from snapadmin.admin_gen import _with_leading_field

        fieldsets = [("Tab", {"fields": ["a"], "classes": ("tab",)})]

        assert _with_leading_field(fieldsets, "tenant_id") == [
            (None, {"fields": ["tenant_id"]}),
            ("Tab", {"fields": ["a"], "classes": ("tab",)}),
        ]

    def test_left_alone_when_a_project_fieldset_already_shows_it(self):
        from snapadmin.admin_gen import _with_leading_field

        fieldsets = [(None, {"fields": [("text", "tenant_id")]})]

        assert _with_leading_field(fieldsets, "tenant_id") is fieldsets


# ── The REST API on a model whose write allowlist leaves the tenant out ─────


@pytest.mark.django_db
class TestTheRestApiWithAWriteAllowlist:
    """``demo.Order`` lists ``api_write_fields = ["customer", "total"]`` — the tenant
    column is read-only to the serializer, so the operator's tenant was dropped from
    ``validated_data`` and the create was refused for naming no tenant."""

    @pytest.fixture
    def api(self, admin_user, operator_binding):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(admin_user)
        return client

    def test_an_operator_names_the_tenant_in_the_body(self, api, customer):
        from demo.apps.shop.models import Order

        response = api.post(
            "/api/models/demo/Order/",
            {"customer": customer.pk, "total": "3.00", "tenant_id": CHOSEN},
            format="json",
        )

        assert response.status_code == 201, response.content
        assert Order._base_manager.get().tenant_id == CHOSEN

    def test_naming_no_tenant_is_still_refused(self, api, customer):
        from demo.apps.shop.models import Order

        response = api.post(
            "/api/models/demo/Order/", {"customer": customer.pk, "total": "3.00"}, format="json"
        )

        assert response.status_code == 403
        assert not Order._base_manager.exists()


@pytest.mark.parametrize("shape", ["FkTenantNote", "FkColumnTenantNote"])
def test_a_foreign_key_tenant_that_does_not_exist_is_a_400(tenant_models, admin_user, shape):  # noqa: F811
    from rest_framework.exceptions import ValidationError

    model = getattr(tenant_models, shape)
    serializer = _serializer_for(model)(data={"text": "ghost"})
    assert serializer.is_valid(), serializer.errors
    view = _viewset_for(model, admin_user)
    view.request = SimpleNamespace(user=admin_user, data={"text": "ghost", "organisation": 999999})

    with tenancy.use_all_tenants(), pytest.raises(ValidationError) as refused:
        view.perform_create(serializer)

    assert "organisation" in refused.value.detail
    assert not model._base_manager.filter(text="ghost").exists()


@pytest.fixture
def to_field_models(transactional_db):
    from django.db import connection, models
    from django.test.utils import isolate_apps

    from snapadmin.models import SnapModel

    with isolate_apps("snapadmin"):

        class CodedOrganisation(models.Model):
            code = models.CharField(max_length=20, unique=True)

            class Meta:
                app_label = "snapadmin"

        class CodedNote(SnapModel):
            organisation = models.ForeignKey(
                CodedOrganisation, to_field="code", null=True, on_delete=models.CASCADE
            )
            text = models.CharField(max_length=40)
            tenant_scoped = True
            tenant_field = "organisation"

            class Meta:
                app_label = "snapadmin"

    with connection.schema_editor(atomic=False) as editor:
        editor.create_model(CodedOrganisation)
        editor.create_model(CodedNote)
    try:
        yield CodedOrganisation, CodedNote
    finally:
        with connection.schema_editor(atomic=False) as editor:
            editor.delete_model(CodedNote)
            editor.delete_model(CodedOrganisation)


class TestAToFieldTenant:
    """``ForeignKey(Org, to_field="code")`` stores the code, not the primary key."""

    def test_an_operator_create_stores_the_code(self, to_field_models, admin_user):
        organisation_model, note = to_field_models
        organisation = organisation_model.objects.create(code="globex")
        serializer = _serializer_for(note)(data={"text": "coded", "organisation": "globex"})
        assert serializer.is_valid(), serializer.errors

        with tenancy.use_all_tenants():
            _viewset_for(note, admin_user).perform_create(serializer)

        assert note._base_manager.get(text="coded").organisation_id == organisation.code

    def test_a_bound_tenant_named_in_the_body_is_not_another_tenant(self, to_field_models, admin_user):
        organisation_model, note = to_field_models
        organisation_model.objects.create(code="acme")
        serializer = _serializer_for(note)(data={"text": "mine", "organisation": "acme"})
        assert serializer.is_valid(), serializer.errors

        with tenancy.use_tenant("acme"):
            _viewset_for(note, admin_user).perform_create(serializer)

        assert note._base_manager.get(text="mine").organisation_id == "acme"


class TestTheBodyTenantIsValidatedLikeTheSerializerWould:
    """When the serializer drops the tenant, the raw body value used to skip every
    serializer step: an FK named by ``"abc"`` was a 500, a dict was stored as text."""

    def _create(self, tenant_models, user, model_name, body):
        from rest_framework.exceptions import ValidationError

        model = getattr(tenant_models, model_name)
        serializer = _serializer_for(model)(data={"text": "body"})
        assert serializer.is_valid(), serializer.errors
        view = _viewset_for(model, user)
        view.request = SimpleNamespace(user=user, data={"text": "body", **body})
        with tenancy.use_all_tenants():
            try:
                view.perform_create(serializer)
            except ValidationError as exc:
                return exc.detail
        return model._base_manager.get(text="body")

    @pytest.mark.parametrize("bad", ["abc", {"x": 1}, [1, 2], True])
    def test_a_malformed_foreign_key_tenant_is_a_400(self, tenant_models, admin_user, bad):  # noqa: F811
        detail = self._create(tenant_models, admin_user, "FkTenantNote", {"organisation": bad})

        assert list(detail) == ["organisation"]

    @pytest.mark.parametrize("bad", [{"x": 1}, [1, 2], True, "x" * 65, "a\x00b"])
    def test_a_malformed_column_tenant_is_a_400(self, tenant_models, admin_user, bad):  # noqa: F811
        detail = self._create(tenant_models, admin_user, "CharTenantNote", {"tenant_id": bad})

        assert list(detail) == ["tenant_id"]

    def test_a_foreign_key_tenant_may_be_named_by_its_column(self, tenant_models, admin_user):  # noqa: F811
        organisation = tenant_models.Organisation.objects.create(name="Globex")

        note = self._create(tenant_models, admin_user, "FkColumnTenantNote", {"organisation_id": organisation.pk})

        assert note.organisation_id == organisation.pk

    def test_a_write_guarded_tenant_field_is_refused(self, tenant_models, regular_user, monkeypatch):  # noqa: F811
        model = tenant_models.CharTenantNote
        monkeypatch.setattr(model, "api_field_permissions", {"tenant_id": {"write": "auth.add_user"}}, raising=False)

        detail = self._create(tenant_models, regular_user, "CharTenantNote", {"tenant_id": "globex"})

        assert list(detail) == ["tenant_id"]
        assert not model._base_manager.filter(text="body").exists()


def test_a_body_that_is_not_an_object_names_no_tenant(tenant_models, admin_user):  # noqa: F811
    from rest_framework.exceptions import PermissionDenied as Refused

    model = tenant_models.CharTenantNote
    serializer = _serializer_for(model)(data={"text": "list body"})
    assert serializer.is_valid(), serializer.errors
    view = _viewset_for(model, admin_user)
    view.request = SimpleNamespace(user=admin_user, data=["globex"])

    with tenancy.use_all_tenants(), pytest.raises(Refused):
        view.perform_create(serializer)
