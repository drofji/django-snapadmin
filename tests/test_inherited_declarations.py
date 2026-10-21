"""
tests/test_inherited_declarations.py — a declaration on an abstract base or a plain
mixin counts like one on the model itself (#EXT3o).

``get_admin_fields()`` read ``SnapFunctionField`` (and ``@snap_property``, which
builds one) from the concrete class's own ``__dict__``, and ``@snap_action``
discovery did the same — so a computed column or an action declared once on an
abstract base shared by ten models appeared on none of them. Every test declared
these on the concrete class. ``show_in_list=False`` on a ``SnapFunctionField`` was
ignored too.

Discovery now walks the class hierarchy: the model, its abstract bases and plain
mixins — never a concrete parent of multi-table inheritance, whose declarations
belong to its own table. The class guard is parity: each declaration kind, on an
abstract base or a mixin, behaves exactly like the same declaration on the model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db import models
from django.test.utils import isolate_apps

from snapadmin import fields as snap_fields
from snapadmin.api.views import get_snap_action, iter_snap_actions, snap_action
from snapadmin.models import SnapModel
from tests import shape_matrix


def _approve(self, request):
    return {}


def _function_field():
    return snap_fields.SnapFunctionField(func=lambda obj: "computed", verbose_name="Computed")


def _declarations_on(where: str) -> SimpleNamespace:
    """The same three declarations, placed on the model, an abstract base or a mixin
    (the shared builder in ``tests/shape_matrix.py``)."""
    return SimpleNamespace(model=shape_matrix.declarations_on(where))


PLACES = list(shape_matrix.PLACES)


class TestInheritanceParity:
    """The class guard: where a declaration sits must not change what it does."""

    @pytest.mark.parametrize("where", PLACES)
    def test_function_fields_and_snap_properties_become_columns(self, where):
        model = _declarations_on(where).model

        list_display = model.get_admin_fields().list_display

        assert {"SnapFunctionFieldComputed", "SnapFunctionFieldLabel"} <= set(list_display), (
            f"review: a computed column declared on the {where} is not discovered — "
            "registry._declared_attributes() / get_admin_fields()"
        )

    @pytest.mark.parametrize("where", PLACES)
    def test_snap_actions_are_found(self, where):
        model = _declarations_on(where).model

        assert [spec.name for spec in iter_snap_actions(model)] == ["approve"], (
            f"review: an @snap_action declared on the {where} is not discovered — "
            "registry._declared_attributes() / iter_snap_actions()"
        )
        assert get_snap_action(model, "approve").name == "approve"

    def test_a_subclass_s_own_declaration_wins_over_the_base_s(self):
        with isolate_apps("snapadmin"):
            base = type("Base", (SnapModel,), {
                "__module__": __name__,
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "base", verbose_name="Base"),
                "Meta": type("Meta", (), {"app_label": "snapadmin", "abstract": True}),
            })
            model = type("Child", (base,), {
                "__module__": __name__,
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "child", verbose_name="Child"),
                "Meta": type("Meta", (), {"app_label": "snapadmin"}),
            })

        model.get_admin_fields()
        display = model._admin_generated_overrides["SnapFunctionFieldComputed"]

        assert display(None, object()) in ("child", ["child", None, None])

    def test_a_concrete_parent_s_declarations_stay_with_its_own_table(self):
        with isolate_apps("snapadmin"):
            parent = type("ConcreteParent", (SnapModel,), {
                "__module__": __name__, "name": models.CharField(max_length=10),
                "computed": _function_field(),
                "approve": snap_action()(_approve),
                "Meta": type("Meta", (), {"app_label": "snapadmin"}),
            })
            child = type("MultiTableChild", (parent,), {
                "__module__": __name__, "extra": models.CharField(max_length=10),
                "Meta": type("Meta", (), {"app_label": "snapadmin"}),
            })

        assert "SnapFunctionFieldComputed" not in child.get_admin_fields().list_display
        assert get_snap_action(child, "approve") is None


class TestFunctionFieldFlags:
    def test_show_in_list_false_keeps_the_column_off_the_changelist(self):
        with isolate_apps("snapadmin"):
            model = type("Hidden", (SnapModel,), {
                "__module__": __name__,
                "computed": snap_fields.SnapFunctionField(func=lambda obj: "x", show_in_list=False),
                "Meta": type("Meta", (), {"app_label": "snapadmin"}),
            })

        assert "SnapFunctionFieldComputed" not in model.get_admin_fields().list_display
