"""
tests/test_check_tenant_scoping.py — snapadmin.E009 (#FUT1b's negative sweep)

``check_tenant_scoping`` is deliberately narrow: it does not demand every
registered model declare ``tenant_scoped`` (most legitimately should not).
What it catches is a **declared but broken** opt-in — the far more common
way to reach that than a field-name typo is declaring the flag on a plain
``@snap_model``-registered model, where ``EsManager``'s scoping hook is
never actually consulted (see the check's own docstring in
``snapadmin/checks.py``). Both shapes are pinned here, plus the clean paths.
"""

from django.db import models as django_models
from django.test.utils import isolate_apps

from snapadmin import checks, tenancy
from snapadmin.models import SnapModel, snap_model


def _plain_model(name, **extra_attrs):
    """A plain django.db.models.Model registered via @snap_model — the door
    through which tenant_scoped is never actually enforceable (E009's first
    branch). tenant_scoped is set as a bare attribute rather than a
    snap_model() kwarg: the decorator does not accept it (it mirrors only
    SnapModel's runtime-machinery-backed keywords), which is itself part of
    why this door is the common way to reach a broken declaration.
    """
    with isolate_apps("snapadmin"):
        attrs = {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "snapadmin"})}
        attrs.update(extra_attrs)
        model = type(name, (django_models.Model,), attrs)
        snap_model(subject_path=None)(model)
    return model


def _snapmodel(name, **class_attrs):
    """A throwaway, concrete SnapModel subclass."""
    with isolate_apps("snapadmin"):
        attrs = {
            **class_attrs,
            "subject_path": None,
            "__module__": __name__,
            "Meta": type("Meta", (), {"app_label": "snapadmin"}),
        }
        return type(name, (SnapModel,), attrs)


class TestTenantScopingCheck:
    def test_clean_when_nothing_registered(self, monkeypatch):
        monkeypatch.setattr(checks.apps, "get_models", lambda: [])
        assert checks.check_tenant_scoping(None) == []

    def test_not_tenant_scoped_is_clean(self, monkeypatch):
        model = _snapmodel("Untenanted0")
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        assert checks.check_tenant_scoping(None) == []

    def test_correctly_configured_snapmodel_is_clean(self, monkeypatch):
        model = _snapmodel(
            "Tenanted0",
            tenant_id=tenancy.tenant_field(),
            tenant_scoped=True,
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        assert checks.check_tenant_scoping(None) == []

    def test_custom_field_name_is_honoured(self, monkeypatch):
        model = _snapmodel(
            "Tenanted1",
            org_id=tenancy.tenant_field(),
            tenant_scoped=True,
            tenant_field="org_id",
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        assert checks.check_tenant_scoping(None) == []

    def test_snapmodel_missing_the_field_is_e009(self, monkeypatch):
        model = _snapmodel("Broken0", tenant_scoped=True)
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_tenant_scoping(None)
        assert [e.id for e in result] == ["snapadmin.E009"]
        assert "no field named 'tenant_id'" in result[0].msg

    def test_snapmodel_wrong_custom_field_name_is_e009(self, monkeypatch):
        model = _snapmodel(
            "Broken1",
            tenant_id=tenancy.tenant_field(),  # declared, but tenant_field points elsewhere
            tenant_scoped=True,
            tenant_field="org_id",
        )
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_tenant_scoping(None)
        assert [e.id for e in result] == ["snapadmin.E009"]
        assert "'org_id'" in result[0].msg

    def test_decorated_plain_model_is_e009_regardless_of_the_field(self, monkeypatch):
        # tenant_id declared with the right shape, but @snap_model's default
        # manager is never EsManager — the declaration is unenforceable
        # either way, so this is E009 even though the field itself is fine.
        model = _plain_model("Decorated0", tenant_id=tenancy.tenant_field())
        model.tenant_scoped = True
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])
        result = checks.check_tenant_scoping(None)
        assert [e.id for e in result] == ["snapadmin.E009"]
        assert "@snap_model" in result[0].msg

    def test_unregistered_model_is_ignored(self, monkeypatch):
        with isolate_apps("snapadmin"):
            class Plain0(django_models.Model):
                tenant_scoped = True  # would-be declaration, never registered

                class Meta:
                    app_label = "snapadmin"
        monkeypatch.setattr(checks.apps, "get_models", lambda: [Plain0])
        assert checks.check_tenant_scoping(None) == []
