"""
tests/test_tenancy.py — the tenant-context primitive and its default-deny queryset scoping.

Pins the mechanism in isolation from any surface built on top of it: the
context var itself, the opt-in gate (``tenant_scoped``), and
``scope_queryset``'s three outcomes (no-op / filtered / empty). Per-surface
cross-tenant leak tests (admin, REST, GraphQL, exports, imports, ES, the
offline cache, the negative sweep) live alongside each surface's own test
file — see ``.claude/roadmap.md``'s #FUT1b block for the full list.
"""

import pytest
from django.db import models as django_models
from django.test import override_settings
from django.test.utils import isolate_apps

from snapadmin import registry, tenancy


def _fake_queryset(rows):
    """A minimal stand-in exposing exactly the contract scope_queryset() needs
    (.filter()/.none()), so this module's tests do not depend on a real DB."""

    class FakeQuerySet:
        def __init__(self, items):
            self.items = list(items)

        def filter(self, **kwargs):
            (field, value), = kwargs.items()
            return FakeQuerySet([r for r in self.items if r.get(field) == value])

        def none(self):
            return FakeQuerySet([])

        def __eq__(self, other):
            return isinstance(other, FakeQuerySet) and self.items == other.items

        def __repr__(self):
            return f"FakeQuerySet({self.items!r})"

    return FakeQuerySet(rows)


class _ScopedModel:
    tenant_scoped = True


class _UnscopedModel:
    tenant_scoped = False


# ── the context var ──────────────────────────────────────────────────────────

class TestTenantContext:
    def test_no_context_bound_reads_as_none(self):
        assert tenancy.get_current_tenant() is None
        assert tenancy.tenant_context_bound() is False

    def test_use_tenant_binds_and_restores(self):
        with tenancy.use_tenant("acme"):
            assert tenancy.get_current_tenant() == "acme"
            assert tenancy.tenant_context_bound() is True
        assert tenancy.get_current_tenant() is None
        assert tenancy.tenant_context_bound() is False

    def test_use_tenant_bound_to_none_is_distinguishable_from_unbound(self):
        """A resolver that ran and explicitly found no tenant differs from
        nobody having asked at all — tenant_context_bound() tells them apart
        even though get_current_tenant() reads the same (None) for both."""
        with tenancy.use_tenant(None):
            assert tenancy.get_current_tenant() is None
            assert tenancy.tenant_context_bound() is True

    def test_nested_use_tenant_restores_the_outer_value(self):
        with tenancy.use_tenant("outer"):
            with tenancy.use_tenant("inner"):
                assert tenancy.get_current_tenant() == "inner"
            assert tenancy.get_current_tenant() == "outer"

    def test_use_tenant_restores_on_exception(self):
        with pytest.raises(ValueError):
            with tenancy.use_tenant("acme"):
                raise ValueError("boom")
        assert tenancy.get_current_tenant() is None

    def test_use_all_tenants_binds_the_sentinel(self):
        with tenancy.use_all_tenants():
            assert tenancy.get_current_tenant() is tenancy.ALL_TENANTS
        assert tenancy.get_current_tenant() is None

    def test_use_all_tenants_restores_on_exception(self):
        with pytest.raises(ValueError):
            with tenancy.use_all_tenants():
                raise ValueError("boom")
        assert tenancy.get_current_tenant() is None


# ── the opt-in gate ───────────────────────────────────────────────────────────

class TestOptIn:
    def test_is_tenant_scoped_true(self):
        assert tenancy.is_tenant_scoped(_ScopedModel) is True

    def test_is_tenant_scoped_false_by_default(self):
        class Plain:
            pass

        assert tenancy.is_tenant_scoped(Plain) is False

    def test_is_tenant_scoped_false_explicit(self):
        assert tenancy.is_tenant_scoped(_UnscopedModel) is False

    def test_tenant_field_name_default(self):
        assert tenancy.tenant_field_name(_ScopedModel) == "tenant_id"

    def test_tenant_field_name_override(self):
        class Custom:
            tenant_field = "org_id"

        assert tenancy.tenant_field_name(Custom) == "org_id"

    def test_tenant_field_name_none_falls_back_to_default(self):
        class Explicit:
            tenant_field = None

        assert tenancy.tenant_field_name(Explicit) == "tenant_id"

    def test_registry_route_is_read_too(self):
        """A plain model registered via snapadmin.registry (the @snap_model
        door) answers tenant_scoped/tenant_field the same way a SnapModel
        class attribute does — get_model_meta's tier 1."""

        class Decorated:
            pass

        registry.register(Decorated, tenant_scoped=True, tenant_field="org_id")
        assert tenancy.is_tenant_scoped(Decorated) is True
        assert tenancy.tenant_field_name(Decorated) == "org_id"


# ── scope_queryset ────────────────────────────────────────────────────────────

class TestScopeQueryset:
    def test_unscoped_model_is_a_no_op_regardless_of_context(self):
        qs = _fake_queryset([{"tenant_id": "a"}, {"tenant_id": "b"}])
        assert tenancy.scope_queryset(_UnscopedModel, qs) is qs
        with tenancy.use_tenant("a"):
            assert tenancy.scope_queryset(_UnscopedModel, qs) is qs

    def test_scoped_model_no_context_bound_is_empty(self):
        qs = _fake_queryset([{"tenant_id": "a"}])
        result = tenancy.scope_queryset(_ScopedModel, qs)
        assert result.items == []

    def test_scoped_model_context_bound_to_none_is_empty(self):
        """A resolver that ran and found no tenant fails closed exactly like
        no resolver having run at all — see the module docstring."""
        qs = _fake_queryset([{"tenant_id": "a"}])
        with tenancy.use_tenant(None):
            result = tenancy.scope_queryset(_ScopedModel, qs)
        assert result.items == []

    def test_scoped_model_with_tenant_bound_filters(self):
        qs = _fake_queryset([{"tenant_id": "a"}, {"tenant_id": "b"}, {"tenant_id": "a"}])
        with tenancy.use_tenant("a"):
            result = tenancy.scope_queryset(_ScopedModel, qs)
        assert result.items == [{"tenant_id": "a"}, {"tenant_id": "a"}]

    def test_scoped_model_null_tenant_row_matches_no_tenant(self):
        """A NULL tenant column is unassigned data, not shared data — it
        matches nobody's filter, by ordinary equality semantics."""
        qs = _fake_queryset([{"tenant_id": None}, {"tenant_id": "a"}])
        with tenancy.use_tenant("a"):
            result = tenancy.scope_queryset(_ScopedModel, qs)
        assert result.items == [{"tenant_id": "a"}]

    def test_use_all_tenants_bypasses_scoping(self):
        qs = _fake_queryset([{"tenant_id": "a"}, {"tenant_id": "b"}])
        with tenancy.use_all_tenants():
            result = tenancy.scope_queryset(_ScopedModel, qs)
        assert result is qs

    def test_custom_tenant_field_name_is_used(self):
        class CustomFieldModel:
            tenant_scoped = True
            tenant_field = "org_id"

        qs = _fake_queryset([{"org_id": "x"}, {"org_id": "y"}])
        with tenancy.use_tenant("x"):
            result = tenancy.scope_queryset(CustomFieldModel, qs)
        assert result.items == [{"org_id": "x"}]


# ── tenant_field() factory ────────────────────────────────────────────────────

class TestTenantFieldFactory:
    def test_default_shape(self):
        field = tenancy.tenant_field()
        assert isinstance(field, django_models.CharField)
        assert field.max_length == 64
        assert field.null is True
        assert field.blank is True
        assert field.db_index is True

    def test_overrides_are_applied(self):
        field = tenancy.tenant_field(max_length=36, db_index=False)
        assert field.max_length == 36
        assert field.db_index is False

    @isolate_apps("snapadmin")
    def test_usable_as_a_real_model_field(self):
        class TenantThing(django_models.Model):
            tenant_id = tenancy.tenant_field()

            class Meta:
                app_label = "snapadmin"

        field = TenantThing._meta.get_field("tenant_id")
        assert field.max_length == 64
        assert field.null is True


# ── resolvers ─────────────────────────────────────────────────────────────────

class TestResolveTenantForRequest:
    def test_unset_resolver_returns_none(self):
        # The demo project (settings_test inherits demo/core/settings.py)
        # configures a real resolver, so "unset" must be forced here rather
        # than relied on as the ambient default.
        with override_settings(SNAPADMIN_TENANT_RESOLVER=None):
            assert tenancy.resolve_tenant_for_request(object()) is None

    def test_configured_resolver_is_called_with_the_request(self):
        seen = []

        def _resolver(request):
            seen.append(request)
            return "acme"

        with override_settings(SNAPADMIN_TENANT_RESOLVER=_resolver):
            request = object()
            assert tenancy.resolve_tenant_for_request(request) == "acme"
        assert seen == [request]

    def test_configured_resolver_by_dotted_path(self):
        with override_settings(
            SNAPADMIN_TENANT_RESOLVER="tests.test_tenancy._dotted_request_resolver"
        ):
            assert tenancy.resolve_tenant_for_request(object()) == "dotted-tenant"


class TestResolveTenantForUser:
    def test_unset_resolver_returns_none(self):
        # The demo project configures a real resolver, so "unset" must be
        # forced here rather than relied on as the ambient default.
        with override_settings(SNAPADMIN_TENANT_USER_RESOLVER=None):
            assert tenancy.resolve_tenant_for_user(object()) is None

    def test_configured_resolver_is_called_with_the_user(self):
        seen = []

        def _resolver(user):
            seen.append(user)
            return "acme"

        with override_settings(SNAPADMIN_TENANT_USER_RESOLVER=_resolver):
            user = object()
            assert tenancy.resolve_tenant_for_user(user) == "acme"
        assert seen == [user]


def _dotted_request_resolver(request):
    return "dotted-tenant"


# ── the middleware ────────────────────────────────────────────────────────────

class TestSnapTenantMiddleware:
    def test_binds_the_resolved_tenant_for_the_response_call_only(self):
        seen = {}

        def get_response(request):
            seen["during"] = tenancy.get_current_tenant()
            return "response"

        with override_settings(SNAPADMIN_TENANT_RESOLVER=lambda request: "acme"):
            middleware = tenancy.SnapTenantMiddleware(get_response)
            result = middleware(object())

        assert result == "response"
        assert seen["during"] == "acme"
        assert tenancy.get_current_tenant() is None

    def test_clears_the_binding_even_if_get_response_raises(self):
        def get_response(request):
            raise RuntimeError("boom")

        with override_settings(SNAPADMIN_TENANT_RESOLVER=lambda request: "acme"):
            middleware = tenancy.SnapTenantMiddleware(get_response)
            with pytest.raises(RuntimeError):
                middleware(object())

        assert tenancy.get_current_tenant() is None

    def test_no_resolver_configured_binds_none(self):
        seen = {}

        def get_response(request):
            seen["during"] = tenancy.get_current_tenant()
            return "response"

        with override_settings(SNAPADMIN_TENANT_RESOLVER=None):
            tenancy.SnapTenantMiddleware(get_response)(object())
        assert seen["during"] is None
