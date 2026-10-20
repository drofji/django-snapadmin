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
