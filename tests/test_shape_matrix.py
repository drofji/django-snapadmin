"""
tests/test_shape_matrix.py — each builder in ``tests/shape_matrix.py`` has the shape it
claims (#QA2a). A builder that quietly produced the common shape would hand every
test using it the homogeneous fixture it exists to replace.
"""

from __future__ import annotations

import pytest
from django.db import models

from snapadmin import tenancy
from snapadmin.api.views import get_snap_action
from snapadmin.models import SnapModel
from tests import shape_matrix


def test_the_tenant_column_is_a_real_foreign_key():
    organisation, note = shape_matrix.fk_tenant_models()

    field = note._meta.get_field("organisation")
    assert isinstance(field, models.ForeignKey)
    assert field.related_model is organisation
    assert tenancy.is_tenant_scoped(note) is True
    assert tenancy._tenant_attname(note) == "organisation_id"


@pytest.mark.parametrize("where", shape_matrix.PLACES)
def test_the_declarations_sit_where_they_say(where):
    model = shape_matrix.declarations_on(where)

    in_own_dict = {"computed", "label", "approve"} & set(vars(model))
    assert in_own_dict == ({"computed", "label", "approve"} if where == "model" else set())
    assert {"SnapFunctionFieldComputed", "SnapFunctionFieldLabel"} <= set(
        model.get_admin_fields().list_display
    )
    assert get_snap_action(model, "approve").name == "approve"


def test_an_unknown_place_is_refused():
    with pytest.raises(ValueError, match="'parent'"):
        shape_matrix.declarations_on("parent")


def test_the_generated_column_is_database_generated():
    model = shape_matrix.generated_field_model()

    field = model._meta.get_field("name_length")
    assert isinstance(field, models.GeneratedField)
    assert field.editable is False


def test_the_log_model_is_read_only_and_flags_no_form_field():
    model = shape_matrix.read_only_log_model()

    assert model.admin_read_only is True
    assert [f.name for f in model._meta.fields if getattr(f, "show_in_form", False)] == []


def test_the_inline_child_is_tenant_scoped_under_a_plain_parent():
    notebook, page = shape_matrix.inline_child_models()

    assert tenancy.is_tenant_scoped(page) is True
    assert tenancy.is_tenant_scoped(notebook) is False
    assert page._meta.get_field("notebook").related_model is notebook


def test_the_plain_model_has_no_snapmodel_anywhere():
    model = shape_matrix.plain_model()

    assert not issubclass(model, SnapModel)
    assert models.Model in model.__mro__


def test_every_build_gets_a_fresh_name():
    assert shape_matrix.plain_model().__name__ != shape_matrix.plain_model().__name__
