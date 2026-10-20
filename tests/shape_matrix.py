"""
tests/shape_matrix.py — one model per shape the suite's fixtures used to miss (#QA2a).

Most of the third round of adopter reports escaped through *homogeneous fixtures*:
every test declared its tenant column as a string, its computed columns on the
concrete class, its models editable and SnapModel-based. Each bug lived on the
other shape — a ``ForeignKey`` tenant (#EXT3b), a column inherited from an abstract
base (#EXT3o), a ``GeneratedField`` (#EXT3h), a read-only log model (#EXT3p), a
tenant-scoped child saved through an inline (#EXT3c), a plain model with no
``SnapModel`` anywhere (#EXT3i). This module builds each of them, so a new test can
ask for the other shape instead of re-deriving it.

=========================  ====================================================
Shape                      Builder
=========================  ====================================================
tenant column is a FK      :func:`fk_tenant_models` → ``(Organisation, Note)``
declarations inherited     :func:`declarations_on` (``"model"`` / ``"abstract
                           base"`` / ``"mixin"``)
database-generated column  :func:`generated_field_model`
read-only log              :func:`read_only_log_model`
tenant-scoped inline child :func:`inline_child_models` → ``(Notebook, Page)``
no SnapModel at all        :func:`plain_model`
=========================  ====================================================

Every builder runs inside ``isolate_apps("snapadmin")`` and gives each class a fresh
name, because several surfaces cache what they build per ``app_label.model_name``.
Builders return classes only; a test that needs tables creates them with
``connection.schema_editor()`` (see ``tests/test_tenancy_write_surfaces.py``).
"""

from __future__ import annotations

import itertools
from typing import Any

from django.db import models
from django.db.models.functions import Length
from django.test.utils import isolate_apps

from snapadmin import fields as snap_fields
from snapadmin import tenancy
from snapadmin.api.views import snap_action
from snapadmin.models import SnapModel, snap_property

_names = itertools.count(1)

PLACES = ("model", "abstract base", "mixin")


def _fresh(stem: str) -> str:
    return f"{stem}{next(_names)}"


def _meta(**options: Any) -> type:
    return type("Meta", (), {"app_label": "snapadmin", **options})


def fk_tenant_models() -> tuple[type, type]:
    """A tenant-scoped note whose tenant column is a real ``ForeignKey`` — the docs'
    recipe when the tenant is a project model (#EXT3b)."""
    with isolate_apps("snapadmin"):
        organisation = type(_fresh("ShapeOrganisation"), (models.Model,), {
            "__module__": __name__,
            "name": models.CharField(max_length=40),
            "Meta": _meta(),
        })
        note = type(_fresh("ShapeFkTenantNote"), (SnapModel,), {
            "__module__": __name__,
            "organisation": models.ForeignKey(organisation, null=True, on_delete=models.CASCADE),
            "text": models.CharField(max_length=40),
            "tenant_scoped": True,
            "tenant_field": "organisation",
            "Meta": _meta(),
        })
    return organisation, note


def standard_declarations() -> dict[str, Any]:
    """A computed column, a ``@snap_property`` and a ``@snap_action`` — the three
    kinds a model discovers from its own class *and* its bases (#EXT3o)."""

    def label(self):
        return "label"

    def approve(self, request):
        return {"ok": True}

    return {
        "computed": snap_fields.SnapFunctionField(func=lambda obj: "computed", verbose_name="Computed"),
        "label": snap_property(verbose_name="Label")(label),
        "approve": snap_action()(approve),
    }


def declarations_on(where: str, body: dict[str, Any] | None = None) -> type:
    """A concrete model carrying ``body`` (default :func:`standard_declarations`)
    on itself, on an abstract base, or on a plain mixin."""
    if where not in PLACES:
        raise ValueError(f"where must be one of {PLACES}, got {where!r}")
    body = standard_declarations() if body is None else body
    own = {"__module__": __name__, "name": models.CharField(max_length=10), "Meta": _meta()}
    with isolate_apps("snapadmin"):
        if where == "model":
            return type(_fresh("ShapeOnModel"), (SnapModel,), {**own, **body})
        if where == "abstract base":
            base = type(_fresh("ShapeBase"), (SnapModel,), {
                "__module__": __name__, **body, "Meta": _meta(abstract=True),
            })
            return type(_fresh("ShapeOnBase"), (base,), own)
        mixin = type(_fresh("ShapeMixin"), (), {"__module__": __name__, **body})
        return type(_fresh("ShapeOnMixin"), (mixin, SnapModel), own)


def generated_field_model() -> type:
    """A model with a database-generated column (#EXT3h)."""
    with isolate_apps("snapadmin"):
        return type(_fresh("ShapeGenerated"), (SnapModel,), {
            "__module__": __name__,
            "name": snap_fields.SnapCharField(max_length=40, show_in_form=True),
            "name_length": models.GeneratedField(
                expression=Length("name"), output_field=models.IntegerField(), db_persist=True,
                null=True,
            ),
            "Meta": _meta(),
        })


def read_only_log_model(**attrs: Any) -> type:
    """Rows the application writes and nobody edits — no field flagged for a form,
    ``admin_read_only = True`` (#EXT3p)."""
    with isolate_apps("snapadmin"):
        return type(_fresh("ShapeReadOnlyLog"), (SnapModel,), {
            "__module__": __name__,
            "action": snap_fields.SnapCharField(max_length=50),
            "created_at": snap_fields.SnapDateTimeField(auto_now_add=True),
            "total": snap_fields.SnapFunctionField(func=lambda obj: "t", show_in_form=True),
            "admin_read_only": True,
            **attrs,
            "Meta": _meta(),
        })


def inline_child_models() -> tuple[type, type]:
    """A plain parent and a tenant-scoped child edited inline on it (#EXT3c)."""
    with isolate_apps("snapadmin"):
        notebook = type(_fresh("ShapeNotebook"), (SnapModel,), {
            "__module__": __name__, "title": models.CharField(max_length=40), "Meta": _meta(),
        })
        page = type(_fresh("ShapeNotebookPage"), (SnapModel,), {
            "__module__": __name__,
            "notebook": models.ForeignKey(notebook, on_delete=models.CASCADE, related_name="pages"),
            "tenant_id": tenancy.tenant_field(),
            "text": models.CharField(max_length=40),
            "tenant_scoped": True,
            "Meta": _meta(),
        })
    return notebook, page


def plain_model() -> type:
    """A plain ``models.Model`` — no ``SnapModel`` in its hierarchy (#EXT3i)."""
    with isolate_apps("snapadmin"):
        return type(_fresh("ShapePlain"), (models.Model,), {
            "__module__": __name__, "name": models.CharField(max_length=40), "Meta": _meta(),
        })
