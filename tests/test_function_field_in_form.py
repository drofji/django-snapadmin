"""
tests/test_function_field_in_form.py — ``show_in_form`` on a computed column (#EXT3o).

``SnapFunctionField(show_in_form=...)`` and ``@snap_property(show_in_form=...)``
stored the flag and nothing ever read it: whatever a model declared, no computed
value reached a change form. The flag defaulted to ``True``, so honouring it as
it stood would have put every existing computed column into every change form
on upgrade. The default is now ``False`` — nothing changes until a model asks —
and ``True`` is honoured: the value is shown **read-only on the change form**, in
its ``tab=`` and ``row=``, in declaration order among the model's fields. The add
form never shows it: there is no saved row to compute from yet.

Missed because: a stored-and-never-read flag is invisible to line coverage (the
assignment runs), and every test of these columns looked at the changelist only.
The class guard: every parameter the two constructors accept has a test here
proving its effect — a parameter added later without one fails the suite.
"""

from __future__ import annotations

import inspect
import re
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.db import models
from django.test import RequestFactory
from django.test.utils import isolate_apps, override_settings

from snapadmin import fields as snap_fields
from snapadmin.models import SnapModel, snap_property
from tests.browser_forms import submit_like_a_browser


def _form_name(attr_name: str) -> str:
    """The admin method a computed column renders through on the change form."""
    return f"snap_form_{attr_name}"


def _flat(fieldsets) -> list[str]:
    names: list[str] = []
    for _title, options in fieldsets:
        for entry in options.get("fields", []):
            names.extend(entry if isinstance(entry, tuple) else [entry])
    return names


def _read_only_cell(body: str, value: str) -> bool:
    """A read-only form cell holding exactly ``value`` — ``<div class="readonly…">``
    in both themes (Unfold adds classes, the stock admin does not)."""
    return re.search(r'<div class="readonly[^"]*">' + re.escape(value) + r"</div>", body) is not None


def _isolated_model(name: str, body: dict, bases: tuple = (SnapModel,), abstract=False):
    meta = {"app_label": "snapadmin"}
    if abstract:
        meta["abstract"] = True
    return type(name, bases, {"__module__": __name__, **body, "Meta": type("Meta", (), meta)})


@pytest.fixture
def isolated_site(monkeypatch):
    """``register_admin()`` writes to ``admin.site``; point it at a throwaway site."""
    site = AdminSite(name="function-field-form")
    monkeypatch.setattr(admin, "site", site)
    yield site
    site._registry.clear()


@pytest.fixture
def staff_request(admin_user):
    request = RequestFactory().get("/")
    request.user = admin_user
    return request


def _generated_admin(model, site):
    model.register_admin()
    return site._registry[model]


# ── The default: nothing changes on upgrade ─────────────────────────────────


class TestTheDefaultIsOff:
    def test_a_function_field_defaults_to_not_in_the_form(self):
        field = snap_fields.SnapFunctionField(func=lambda obj: "x")

        assert field.show_in_form is False

    def test_a_snap_property_defaults_to_not_in_the_form(self):
        def label(self):
            return "x"

        assert snap_property()(label).show_in_form is False

    def test_a_status_badge_defaults_to_not_in_the_form(self):
        badge = snap_fields.SnapStatusBadgeField(
            "status", choices=[snap_fields.SnapStatusBadgeFieldChoice("paid")]
        )

        assert badge.show_in_form is False

    @override_settings(SNAPADMIN_SHOW_IN_FORM_DEFAULT=True)
    def test_the_project_wide_form_default_does_not_reach_computed_columns(self):
        """``SNAPADMIN_SHOW_IN_FORM_DEFAULT`` raises the default of editable
        fields. Following it here would put every computed column of such a
        project into its change forms on upgrade — the change the new default
        exists to avoid."""
        field = snap_fields.SnapFunctionField(func=lambda obj: "x")

        assert field.show_in_form is False

    @pytest.mark.django_db
    def test_an_unflagged_column_is_in_neither_the_form_nor_the_read_only_list(
        self, isolated_site, staff_request
    ):
        with isolate_apps("snapadmin"):
            model = _isolated_model("Unflagged", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "x"),
            })
        model_admin = _generated_admin(model, isolated_site)
        instance = model(pk=1, name="a")

        assert _flat(model_admin.get_fieldsets(staff_request, instance)) == ["name"]
        assert _form_name("computed") not in model_admin.get_readonly_fields(staff_request, instance)


# ── show_in_form=True: read-only on the change form ─────────────────────────


@pytest.mark.django_db
class TestTheFlagPlacesTheColumnOnTheChangeForm:
    def test_it_is_read_only_and_in_the_change_form(self, isolated_site, staff_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("Shown", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True),
            })
        model_admin = _generated_admin(model, isolated_site)
        instance = model(pk=1, name="a")

        assert _flat(model_admin.get_fieldsets(staff_request, instance)) == [
            "name", _form_name("computed"),
        ]
        assert _form_name("computed") in model_admin.get_readonly_fields(staff_request, instance)

    def test_the_add_form_leaves_it_out(self, isolated_site, staff_request):
        """No saved row yet: ``func`` would run on an empty instance, and a
        ``quantity * price`` body raises ``TypeError`` on ``None``."""
        with isolate_apps("snapadmin"):
            model = _isolated_model("AddForm", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True),
            })
        model_admin = _generated_admin(model, isolated_site)

        assert _flat(model_admin.get_fieldsets(staff_request, None)) == ["name"]

    def test_a_tab_holding_only_computed_columns_is_not_left_empty_on_the_add_form(
        self, isolated_site, staff_request
    ):
        with isolate_apps("snapadmin"):
            model = _isolated_model("OnlyComputedTab", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(
                    func=lambda obj: "x", show_in_form=True, tab="Totals"
                ),
            })
        model_admin = _generated_admin(model, isolated_site)

        add_titles = [title for title, _ in model_admin.get_fieldsets(staff_request, None)]
        change_titles = [
            title for title, _ in model_admin.get_fieldsets(staff_request, model(pk=1))
        ]

        assert add_titles == [None]
        assert change_titles == [None, "Totals"]

    def test_it_sits_where_it_was_declared_among_the_fields(self, isolated_site, staff_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("Ordered", {
                "first": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True),
                "second": snap_fields.SnapCharField(max_length=10, show_in_form=True),
            })
        model_admin = _generated_admin(model, isolated_site)

        assert _flat(model_admin.get_fieldsets(staff_request, model(pk=1))) == [
            "first", _form_name("computed"), "second",
        ]

    def test_tab_puts_it_in_that_tab(self, isolated_site, staff_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("Tabbed", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "price": snap_fields.SnapDecimalField(
                    max_digits=8, decimal_places=2, show_in_form=True, tab="Pricing"
                ),
                "total": snap_fields.SnapFunctionField(
                    func=lambda obj: "x", show_in_form=True, tab="Pricing"
                ),
            })
        model_admin = _generated_admin(model, isolated_site)

        fieldsets = model_admin.get_fieldsets(staff_request, model(pk=1))

        assert fieldsets[0][1]["fields"] == ["name"]
        assert fieldsets[1][0] == "Pricing"
        assert fieldsets[1][1]["fields"] == ["price", _form_name("total")]
        assert "tab" in fieldsets[1][1]["classes"]

    def test_row_puts_it_on_one_line_with_the_fields_of_that_row(self, isolated_site, staff_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("Rowed", {
                "quantity": snap_fields.SnapIntegerField(show_in_form=True, row="money"),
                "price": snap_fields.SnapDecimalField(
                    max_digits=8, decimal_places=2, show_in_form=True, row="money"
                ),
                "total": snap_fields.SnapFunctionField(
                    func=lambda obj: "x", show_in_form=True, row="money"
                ),
                "note": snap_fields.SnapCharField(max_length=10, show_in_form=True),
            })
        model_admin = _generated_admin(model, isolated_site)

        fields = model_admin.get_fieldsets(staff_request, model(pk=1))[0][1]["fields"]

        assert fields == [("quantity", "price", _form_name("total")), "note"]

    def test_the_form_rendering_is_the_plain_value_in_either_theme(self, isolated_site):
        """The changelist column is an Unfold "header" cell, a ``[value, None,
        None]`` triple — a read-only form row would print that as a list."""
        with isolate_apps("snapadmin"):
            model = _isolated_model("Plain", {
                "computed": snap_fields.SnapFunctionField(
                    func=lambda obj: f"v{obj.pk}", verbose_name="Computed", show_in_form=True
                ),
            })
        model_admin = _generated_admin(model, isolated_site)
        display = getattr(model_admin, _form_name("computed"))

        assert display(model(pk=7)) == "v7"
        assert display.short_description == "Computed"

    def test_the_form_never_gets_an_input_for_it(self, isolated_site, staff_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("NoInput", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True),
            })
        model_admin = _generated_admin(model, isolated_site)

        form_class = model_admin.get_form(staff_request, model(pk=1))

        assert list(form_class.base_fields) == ["name"]

    def test_a_project_fieldset_can_name_the_column(self, isolated_site, staff_request):
        """``admin_overrides["fieldsets"]`` replaces the generated layout; the
        documented name of the read-only row is what a project writes there."""
        with isolate_apps("snapadmin"):
            model = _isolated_model("Custom", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True),
                "admin_overrides": {
                    "fieldsets": [("Summary", {"fields": [_form_name("computed"), "name"]})],
                },
            })
        model_admin = _generated_admin(model, isolated_site)

        assert model_admin.get_fieldsets(staff_request, model(pk=1)) == [
            ("Summary", {"fields": [_form_name("computed"), "name"]}),
        ]
        assert _form_name("computed") in model_admin.get_readonly_fields(staff_request, model(pk=1))
        assert list(model_admin.get_form(staff_request, model(pk=1)).base_fields) == ["name"]

    def test_show_in_list_and_show_in_form_are_independent(self, isolated_site, staff_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("FormOnly", {
                "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
                "computed": snap_fields.SnapFunctionField(
                    func=lambda obj: "x", show_in_list=False, show_in_form=True
                ),
            })
        model_admin = _generated_admin(model, isolated_site)

        assert "SnapFunctionFieldComputed" not in model_admin.list_display
        assert _form_name("computed") in _flat(model_admin.get_fieldsets(staff_request, model(pk=1)))


# ── Through the real admin views (the demo's OrderItem and Showcase) ────────


@pytest.fixture
def order_item(order, product):
    from demo.apps.shop.models import OrderItem

    return OrderItem.objects.create(order=order, product=product, quantity=3, price=Decimal("2.50"))


@pytest.fixture
def showcase(db):
    from demo.apps.shop.models import Showcase

    return Showcase.objects.create(char_field="<b>Hi</b>", integer_field=4)


@pytest.mark.django_db
class TestTheRealChangeForm:
    def test_the_change_form_shows_the_computed_value_read_only(self, admin_client, order_item):
        response = admin_client.get(f"/admin/demo/orderitem/{order_item.pk}/change/")

        body = response.content.decode()
        assert response.status_code == 200
        assert _read_only_cell(body, "7.50")
        assert "Line Total" in body
        assert f'name="{_form_name("line_total")}"' not in body

    def test_the_add_form_renders_without_it(self, admin_client):
        response = admin_client.get("/admin/demo/orderitem/add/")

        assert response.status_code == 200
        assert "Line Total" not in response.content.decode()

    def test_the_change_form_saves_when_posted_back_like_a_browser(self, admin_client, order_item):
        response = submit_like_a_browser(
            admin_client,
            f"/admin/demo/orderitem/{order_item.pk}/change/",
            {"quantity": ["5"]},
            form_id="orderitem_form",
        )

        assert response.status_code == 302, response.content.decode()[:2000]
        order_item.refresh_from_db()
        assert order_item.quantity == 5

    def test_save_as_new_takes_the_add_path_and_saves(self, admin_client, order_item):
        from demo.apps.shop.models import OrderItem

        response = submit_like_a_browser(
            admin_client,
            f"/admin/demo/orderitem/{order_item.pk}/change/",
            {"_saveasnew": ["Save as new"]},
            form_id="orderitem_form",
        )

        assert response.status_code == 302, response.content.decode()[:2000]
        assert OrderItem.objects.count() == 2

    def test_a_tabbed_column_is_escaped_in_its_tab(self, admin_client, showcase):
        response = admin_client.get(f"/admin/demo/showcase/{showcase.pk}/change/")

        body = response.content.decode()
        assert response.status_code == 200
        assert _read_only_cell(body, "&lt;b&gt;Hi&lt;/b&gt; · int=4")
        assert "<b>Hi</b> · int=4" not in body

    def test_a_view_only_user_sees_it_too(self, client, django_user_model, order_item):
        from django.contrib.auth.models import Permission

        viewer = django_user_model.objects.create_user(
            "viewer", "viewer@example.com", "pw", is_staff=True
        )
        viewer.user_permissions.add(Permission.objects.get(codename="view_orderitem"))
        client.force_login(viewer)

        response = client.get(f"/admin/demo/orderitem/{order_item.pk}/change/")

        assert response.status_code == 200
        assert _read_only_cell(response.content.decode(), "7.50")

    def test_safe_html_true_renders_the_markup(self, isolated_site, staff_request):
        with isolate_apps("snapadmin"):
            model = _isolated_model("Trusted", {
                "computed": snap_fields.SnapFunctionField(
                    func=lambda obj: "<b>ok</b>", show_in_form=True, safe_html=True
                ),
                "raw": snap_fields.SnapFunctionField(func=lambda obj: "<b>no</b>", show_in_form=True),
            })
        model_admin = _generated_admin(model, isolated_site)

        trusted = getattr(model_admin, _form_name("computed"))(model(pk=1))
        escaped = getattr(model_admin, _form_name("raw"))(model(pk=1))

        assert hasattr(trusted, "__html__")
        assert not hasattr(escaped, "__html__")


# ── The class guard: every accepted parameter has an effect ─────────────────


def _model_with(field) -> type:
    with isolate_apps("snapadmin"):
        return _isolated_model(f"Probe{abs(hash(id(field)))}", {
            "name": snap_fields.SnapCharField(max_length=10, show_in_form=True),
            "computed": field,
        })


def _effect_of_verbose_name(site, request):
    model = _model_with(snap_fields.SnapFunctionField(func=lambda obj: "x", verbose_name="Shown as"))
    model_admin = _generated_admin(model, site)
    return model_admin.SnapFunctionFieldComputed.short_description == "Shown as"


def _effect_of_func(site, request):
    model = _model_with(snap_fields.SnapFunctionField(func=lambda obj: "from func", show_in_form=True))
    model_admin = _generated_admin(model, site)
    return getattr(model_admin, _form_name("computed"))(model(pk=1)) == "from func"


def _effect_of_show_in_list(site, request):
    model = _model_with(snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_list=False))
    return "SnapFunctionFieldComputed" not in _generated_admin(model, site).list_display


def _effect_of_show_in_form(site, request):
    model = _model_with(snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True))
    fieldsets = _generated_admin(model, site).get_fieldsets(request, model(pk=1))
    return _form_name("computed") in _flat(fieldsets)


def _effect_of_safe_html(site, request):
    model = _model_with(
        snap_fields.SnapFunctionField(func=lambda obj: "<i>x</i>", show_in_form=True, safe_html=True)
    )
    return hasattr(getattr(_generated_admin(model, site), _form_name("computed"))(model(pk=1)), "__html__")


def _effect_of_tab(site, request):
    model = _model_with(snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True, tab="T"))
    titles = [title for title, _ in _generated_admin(model, site).get_fieldsets(request, model(pk=1))]
    return titles == [None, "T"]


def _effect_of_row(site, request):
    with isolate_apps("snapadmin"):
        model = _isolated_model("RowProbe", {
            "name": snap_fields.SnapCharField(max_length=10, show_in_form=True, row="r"),
            "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True, row="r"),
        })
    fields = _generated_admin(model, site).get_fieldsets(request, model(pk=1))[0][1]["fields"]
    return fields == [("name", _form_name("computed"))]


#: One entry per constructor parameter: a probe that is True only when the
#: parameter did what it says. A parameter missing here is one nobody proved is
#: honoured — exactly how ``show_in_form`` went unread for its whole life.
PARAMETER_EFFECTS = {
    "func": _effect_of_func,
    "verbose_name": _effect_of_verbose_name,
    "show_in_list": _effect_of_show_in_list,
    "show_in_form": _effect_of_show_in_form,
    "safe_html": _effect_of_safe_html,
    "tab": _effect_of_tab,
    "row": _effect_of_row,
}


def _effect_of_extra_arguments(site, request):
    field = snap_fields.SnapFunctionField(lambda obj: "x", None, True, False, False, "stray", typo=1)
    return field.unused_arguments == ("1 extra positional argument", "typo")


PARAMETER_EFFECTS["*args"] = _effect_of_extra_arguments
PARAMETER_EFFECTS["**kwargs"] = _effect_of_extra_arguments


def _declared_parameters(callable_) -> list[str]:
    prefix = {inspect.Parameter.VAR_POSITIONAL: "*", inspect.Parameter.VAR_KEYWORD: "**"}
    return [
        prefix.get(param.kind, "") + name
        for name, param in inspect.signature(callable_).parameters.items()
        if name != "self"
    ]


class TestEveryParameterIsHonoured:
    def test_every_function_field_parameter_has_an_effect_probe(self):
        missing = set(_declared_parameters(snap_fields.SnapFunctionField.__init__)) - set(
            PARAMETER_EFFECTS
        )

        assert not missing, (
            f"review: SnapFunctionField accepts {sorted(missing)} with no test proving it has an "
            "effect — add a probe to PARAMETER_EFFECTS in tests/test_function_field_in_form.py"
        )

    def test_snap_property_accepts_exactly_the_field_s_display_parameters(self):
        field_parameters = set(_declared_parameters(snap_fields.SnapFunctionField.__init__)) - {
            "func", "*args", "**kwargs",
        }

        assert set(_declared_parameters(snap_property)) == field_parameters, (
            "review: @snap_property and SnapFunctionField drifted apart — a parameter one "
            "accepts and the other does not"
        )

    def test_snap_property_forwards_every_parameter_unchanged(self):
        def method(self):
            return "x"

        values = {
            "verbose_name": "Label", "show_in_list": False, "show_in_form": True,
            "safe_html": True, "tab": "T", "row": "r",
        }
        field = snap_property(**values)(method)

        assert {name: getattr(field, name) for name in values} == values

    @pytest.mark.django_db
    @pytest.mark.parametrize("parameter", sorted(PARAMETER_EFFECTS))
    def test_the_parameter_has_its_effect(self, parameter, isolated_site, staff_request):
        assert PARAMETER_EFFECTS[parameter](isolated_site, staff_request), (
            f"review: SnapFunctionField's {parameter!r} is accepted but has no effect"
        )


# ── An argument the column cannot use is reported, never dropped (W033) ────


def _models_for_check(monkeypatch, *models_) -> None:
    from snapadmin import checks

    monkeypatch.setattr(checks.apps, "get_models", lambda: list(models_))


class TestUnusedArgumentsAreReported:
    """The constructor took ``*args, **kwargs`` and dropped them: a typo such as
    ``show_in_fom=True`` or a flag only editable fields have (``searchable=``)
    did nothing, with no signal — the same class of bug as ``show_in_form``.
    Every other Snap field fails on an unknown argument (Django's ``TypeError``);
    this one now records it and ``snapadmin.W033`` names it at startup."""

    def test_unknown_keywords_and_extra_positionals_are_recorded(self):
        field = snap_fields.SnapFunctionField(
            lambda obj: "x", "Label", True, False, False, "stray", searchable=True, show_in_fom=True
        )

        assert field.unused_arguments == ("1 extra positional argument", "searchable", "show_in_fom")

    def test_a_clean_declaration_records_nothing(self):
        field = snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True, tab="T")

        assert field.unused_arguments == ()

    def test_a_badge_forwards_its_unknown_keywords_to_the_same_record(self):
        badge = snap_fields.SnapStatusBadgeField(
            "status", choices=[snap_fields.SnapStatusBadgeFieldChoice("paid")], filterable=True
        )

        assert badge.unused_arguments == ("filterable",)

    def test_w033_names_the_model_the_column_and_the_arguments(self, monkeypatch):
        from snapadmin import checks

        with isolate_apps("snapadmin"):
            model = _isolated_model("Sloppy", {
                "total": snap_fields.SnapFunctionField(func=lambda obj: "x", searchable=True),
                "fine": snap_fields.SnapFunctionField(func=lambda obj: "y"),
            })
        _models_for_check(monkeypatch, model)

        messages = checks.check_function_field_arguments(None)

        assert [(m.id, m.level) for m in messages] == [("snapadmin.W033", 30)]
        assert messages[0].msg == (
            "SnapFunctionField 'total' on snapadmin.Sloppy ignores argument(s): searchable."
        )
        assert "func, verbose_name, show_in_list, show_in_form, safe_html, tab, row" in messages[0].hint

    def test_w033_reaches_a_column_inherited_from_an_abstract_base(self, monkeypatch):
        from snapadmin import checks

        with isolate_apps("snapadmin"):
            base = _isolated_model("SloppyBase", {
                "total": snap_fields.SnapFunctionField(func=lambda obj: "x", editable=False),
            }, abstract=True)
            model = _isolated_model("SloppyChild", {}, bases=(base,))
        _models_for_check(monkeypatch, model)

        assert [m.msg for m in checks.check_function_field_arguments(None)] == [
            "SnapFunctionField 'total' on snapadmin.SloppyChild ignores argument(s): editable.",
        ]

    def test_w033_is_registered(self):
        from snapadmin import checks

        assert checks.check_function_field_arguments in checks.ALL_CHECKS

    def test_the_demo_project_is_clean(self):
        from snapadmin import checks

        assert checks.check_function_field_arguments(None) == []


# ── Inherited declarations behave like the model's own (#EXT3o parity) ─────


@pytest.mark.django_db
@pytest.mark.parametrize("where", ["model", "abstract base", "mixin"])
def test_show_in_form_works_wherever_the_column_is_declared(where, isolated_site, staff_request):
    def label(self):
        return "label"

    body = {
        "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_form=True),
        "label": snap_property(show_in_form=True)(label),
    }
    name_field = {"name": models.CharField(max_length=10)}
    with isolate_apps("snapadmin"):
        if where == "model":
            model = _isolated_model("FormOnModel", {**name_field, **body})
        elif where == "abstract base":
            base = _isolated_model("FormBase", body, abstract=True)
            model = _isolated_model("FormOnBase", name_field, bases=(base,))
        else:
            mixin = type("FormMixin", (), {"__module__": __name__, **body})
            model = _isolated_model("FormOnMixin", name_field, bases=(mixin, SnapModel))
    model_admin = _generated_admin(model, isolated_site)

    names = _flat(model_admin.get_fieldsets(staff_request, model(pk=1)))

    assert names == [_form_name("computed"), _form_name("label")]
