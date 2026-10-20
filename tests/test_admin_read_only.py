"""
tests/test_admin_read_only.py — ``admin_read_only``: a model the admin only shows (#EXT3p).

A log model — rows written by the application, never by a person — had no
declarative way to say so. Its generated admin offered add, change and delete,
and leaving every field without ``show_in_form`` (the honest declaration: there
is no form) tripped ``snapadmin.W015``, so adopters set ``show_in_form=True`` on
fields nobody should edit just to silence it.

``admin_read_only = True`` makes the generated admin view-only — no add, change or
delete, for anyone — and its view page shows every field read-only when none
carries ``show_in_form``. ``W015`` skips such a model.

Missed because: W015's tests covered editable models only, and "read-only" had no
declarative form, so there was nothing to test. The class guard is a check × shape
matrix: every admin-related check, over every admin shape a model can have, with
the outcome written down — a new check or a new shape has to fill in its row.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.admin import AdminSite, ModelAdmin
from django.test import RequestFactory
from django.test.utils import isolate_apps

from snapadmin import checks
from snapadmin import fields as snap_fields
from snapadmin.models import SnapModel
from tests import shape_matrix


def _isolated_model(name: str, body: dict):
    return type(name, (SnapModel,), {
        "__module__": __name__, **body,
        "Meta": type("Meta", (), {"app_label": "snapadmin"}),
    })


def _log_model(name: str = "ReadOnlyLog", **attrs):
    """The read-only log shape from ``tests/shape_matrix.py`` (``name`` is kept for
    readability at the call site; the builder gives every class a fresh name)."""
    return shape_matrix.read_only_log_model(**attrs)


@pytest.fixture
def isolated_site(monkeypatch):
    site = AdminSite(name="admin-read-only")
    monkeypatch.setattr(admin, "site", site)
    yield site
    site._registry.clear()


@pytest.fixture
def superuser_request(admin_user):
    request = RequestFactory().get("/")
    request.user = admin_user
    return request


def _flat(fieldsets) -> list[str]:
    names: list[str] = []
    for _title, options in fieldsets:
        for entry in options.get("fields", []):
            names.extend(entry if isinstance(entry, tuple) else [entry])
    return names


class TestTheDefault:
    def test_a_model_is_editable_unless_it_says_otherwise(self):
        assert SnapModel.admin_read_only is False


@pytest.mark.django_db
class TestTheGeneratedAdminIsViewOnly:
    def test_nobody_may_add_change_or_delete_not_even_a_superuser(
        self, isolated_site, superuser_request
    ):
        model = _log_model()
        model.register_admin()
        model_admin = isolated_site._registry[model]
        row = model(pk=1)

        assert model_admin.has_add_permission(superuser_request) is False
        assert model_admin.has_change_permission(superuser_request, row) is False
        assert model_admin.has_delete_permission(superuser_request, row) is False
        assert model_admin.has_view_permission(superuser_request, row) is True

    def test_the_view_page_shows_every_field_when_none_is_flagged(
        self, isolated_site, superuser_request
    ):
        model = _log_model()
        model.register_admin()
        model_admin = isolated_site._registry[model]

        names = _flat(model_admin.get_fieldsets(superuser_request, model(pk=1)))

        assert names == ["action", "created_at", "snap_form_total"]

    def test_every_shown_field_is_read_only_and_the_form_has_no_input(
        self, isolated_site, superuser_request
    ):
        """A plain non-editable Django column carries no Snap ``editable`` flag;
        left out of the read-only list it would reach the view page's form, and
        Django refuses a non-editable field in a form (``FieldError``)."""
        from django.db import models

        model = _log_model("PlainColumnLog", stamped=models.DateTimeField(auto_now_add=True))
        model.register_admin()
        model_admin = isolated_site._registry[model]
        row = model(pk=1)

        # Declaration order: ``stamped`` is built first, as the call's argument.
        assert model_admin.get_readonly_fields(superuser_request, row) == [
            "stamped", "action", "created_at", "snap_form_total",
        ]
        assert list(model_admin.get_form(superuser_request, row).base_fields) == []

    def test_flagged_fields_still_choose_what_the_view_page_shows(
        self, isolated_site, superuser_request
    ):
        model = _log_model(
            "FlaggedLog",
            action=snap_fields.SnapCharField(max_length=50, show_in_form=True),
        )
        model.register_admin()

        names = _flat(isolated_site._registry[model].get_fieldsets(superuser_request, model(pk=1)))

        assert names == ["action", "snap_form_total"]

    def test_a_mixin_may_still_grant_a_permission(self, isolated_site, superuser_request):
        """The permission methods live on the generated base (#EXT3n), so a
        project mixin can still widen one — delete for a retention clean-up."""

        class AllowDelete:
            def has_delete_permission(self, request, obj=None):
                return True

        model = _log_model("MixinLog", admin_mixins=[AllowDelete])
        model.register_admin()
        model_admin = isolated_site._registry[model]

        assert model_admin.has_delete_permission(superuser_request) is True
        assert model_admin.has_add_permission(superuser_request) is False

    def test_an_editable_model_keeps_every_permission(self, isolated_site, superuser_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("EditableModel", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
            })
        model.register_admin()
        model_admin = isolated_site._registry[model]

        assert model_admin.has_add_permission(superuser_request) is True
        assert model_admin.has_change_permission(superuser_request) is True
        assert model_admin.has_delete_permission(superuser_request) is True


@pytest.mark.django_db
class TestTheDemoAuditLog:
    """``demo.AuditLog`` is the worked example: rows the application writes."""

    def test_the_add_page_is_refused(self, admin_client):
        assert admin_client.get("/admin/demo/auditlog/add/").status_code == 403

    def test_the_change_page_is_a_read_only_view(self, admin_client):
        from demo.apps.shop.models import AuditLog

        row = AuditLog.objects.create(action="login", user_email="a@example.com")

        response = admin_client.get(f"/admin/demo/auditlog/{row.pk}/change/")

        body = response.content.decode()
        assert response.status_code == 200
        assert 'name="action"' not in body
        assert "login" in body
        assert 'name="_save"' not in body

    def test_a_post_cannot_change_a_row(self, admin_client):
        from demo.apps.shop.models import AuditLog

        row = AuditLog.objects.create(action="login", user_email="a@example.com")

        response = admin_client.post(
            f"/admin/demo/auditlog/{row.pk}/change/", {"action": "tampered"}
        )

        row.refresh_from_db()
        assert response.status_code == 403
        assert row.action == "login"


# ── The class guard: admin-related checks × admin shapes ─────────────────────


def _shape_editable():
    with isolate_apps("snapadmin"):
        return _isolated_model("ShapeEditable", {
            "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
        })


def _shape_editable_empty():
    with isolate_apps("snapadmin"):
        return _isolated_model("ShapeEmpty", {"name": snap_fields.SnapCharField(max_length=10)})


def _shape_read_only():
    return _log_model("ShapeReadOnly")


def _shape_admin_disabled():
    with isolate_apps("snapadmin"):
        return _isolated_model("ShapeDisabled", {
            "name": snap_fields.SnapCharField(max_length=10), "admin_enabled": False,
        })


def _shape_hand_written():
    with isolate_apps("snapadmin"):
        return _isolated_model("ShapeHandWritten", {"name": snap_fields.SnapCharField(max_length=10)})


#: shape → (model factory, how it is registered on the site)
SHAPES = {
    "editable": (_shape_editable, "generated"),
    "editable, no form field": (_shape_editable_empty, "generated"),
    "admin_read_only": (_shape_read_only, "generated"),
    "admin_enabled = False": (_shape_admin_disabled, "none"),
    "hand-written admin": (_shape_hand_written, "hand-written"),
}

#: (check, shape) → the ids it must report. Every admin-related check has a row
#: per shape; ``W025`` is exercised with masking configured for the model.
EXPECTED = {
    ("W015", "editable"): [],
    ("W015", "editable, no form field"): ["snapadmin.W015"],
    ("W015", "admin_read_only"): [],
    ("W015", "admin_enabled = False"): [],
    ("W015", "hand-written admin"): [],
    ("W025", "editable"): [],
    ("W025", "editable, no form field"): [],
    ("W025", "admin_read_only"): [],
    ("W025", "admin_enabled = False"): [],
    ("W025", "hand-written admin"): ["snapadmin.W025"],
}

ADMIN_CHECKS = {
    "W015": checks.check_empty_admin_forms,
    "W025": checks.check_masked_models_use_masking_admin,
}


def _register(model, how: str, site) -> None:
    if how == "generated":
        model.register_admin()
    elif how == "hand-written":
        site.register(model, ModelAdmin)


def test_every_admin_check_has_an_expected_outcome_for_every_shape():
    missing = {(check, shape) for check in ADMIN_CHECKS for shape in SHAPES} - set(EXPECTED)

    assert not missing, (
        f"review: {sorted(missing)} have no expected outcome — write down what each "
        "admin-related check must report for each admin shape in tests/test_admin_read_only.py"
    )


@pytest.mark.parametrize(("check_id", "shape"), sorted(EXPECTED))
def test_the_check_reports_what_the_shape_deserves(
    check_id, shape, isolated_site, monkeypatch, settings
):
    factory, how = SHAPES[shape]
    model = factory()
    _register(model, how, isolated_site)
    monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
    monkeypatch.setattr(
        "django.contrib.admin.sites.all_sites", {isolated_site}, raising=False
    )
    settings.SNAPADMIN_MASKED_FIELDS = {f"snapadmin.{model.__name__.lower()}": ["name"]}

    ids = [message.id for message in ADMIN_CHECKS[check_id](None)]

    assert ids == EXPECTED[(check_id, shape)], (
        f"review: {check_id} on a {shape!r} model reported {ids}, expected "
        f"{EXPECTED[(check_id, shape)]}"
    )
