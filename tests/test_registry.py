"""
tests/test_registry.py — the registry seam behind every "is this a SnapAdmin model?" gate

Two things are pinned here. First the primitives of :mod:`snapadmin.registry`
(register / is_registered / meta_for, and the weak entries). Second — and this is
the one that matters — that the registry answers *exactly* what the
``issubclass(model, SnapModel) and model is not SnapModel`` test it replaced
answered, for every model installed in the project. The gates in ``apps.py``,
``api/views.py``, ``api/graphql.py``, ``api/offline.py``, ``checks.py`` and
``models.py`` all read the registry now, so that equivalence is what keeps the
admin, the REST surface, the GraphQL schema and the system checks selecting the
same models as before.
"""

import gc
import weakref
from weakref import WeakKeyDictionary

import pytest
from django.apps import apps
from django.contrib.auth.models import Permission

from snapadmin import registry
from snapadmin.models import SnapModel, reindexable_snapmodels


# ── the primitives ───────────────────────────────────────────────────────────

class TestRegistryPrimitives:
    def test_unknown_class_is_unregistered_and_has_no_meta(self):
        class Unknown:
            pass

        assert registry.is_registered(Unknown) is False
        assert registry.meta_for(Unknown) == {}

    def test_register_returns_the_model_unchanged(self):
        class Target:
            pass

        assert registry.register(Target) is Target
        assert registry.is_registered(Target) is True
        assert registry.meta_for(Target) == {}

    def test_register_is_idempotent_and_merges_meta(self):
        class Target:
            pass

        registry.register(Target, search_fields=["name"])
        registry.register(Target, api_read_only=True)
        assert registry.meta_for(Target) == {"search_fields": ["name"], "api_read_only": True}

        registry.register(Target, search_fields=["title"])
        assert registry.meta_for(Target) == {"search_fields": ["title"], "api_read_only": True}

    def test_meta_for_hands_back_a_copy(self):
        class Target:
            pass

        registry.register(Target, search_fields=["name"])
        registry.meta_for(Target)["search_fields"] = ["tampered"]
        assert registry.meta_for(Target) == {"search_fields": ["name"]}

    def test_entries_are_weak_so_a_throwaway_class_is_not_pinned(self):
        class Throwaway:
            pass

        registry.register(Throwaway, api_read_only=True)
        ref = weakref.ref(Throwaway)

        del Throwaway
        gc.collect()

        assert ref() is None


# ── SnapModel registers its subclasses as they are declared ──────────────────

class TestSnapModelRegistration:
    def test_the_abstract_base_itself_is_never_registered(self):
        assert registry.is_registered(SnapModel) is False
        assert SnapModel.is_concrete_subclass(SnapModel) is False

    def test_declaring_a_subclass_registers_it(self):
        class Gadget(SnapModel):
            class Meta:
                app_label = "demo"
                abstract = True

        assert registry.is_registered(Gadget) is True
        assert SnapModel.is_concrete_subclass(Gadget) is True

    def test_a_subclass_of_a_subclass_is_registered_too(self):
        class Base(SnapModel):
            class Meta:
                app_label = "demo"
                abstract = True

        class Derived(Base):
            class Meta:
                app_label = "demo"
                abstract = True

        assert registry.is_registered(Derived) is True

    def test_a_plain_django_model_stays_out(self):
        assert registry.is_registered(Permission) is False
        assert SnapModel.is_concrete_subclass(Permission) is False

    def test_is_concrete_subclass_reads_the_registry(self, monkeypatch):
        """The predicate is a registry lookup, not an inheritance test any more."""
        class Gadget(SnapModel):
            class Meta:
                app_label = "demo"
                abstract = True

        monkeypatch.setattr(registry, "_REGISTRY", WeakKeyDictionary())
        assert SnapModel.is_concrete_subclass(Gadget) is False


# ── equivalence with the predicate the gates used to run ─────────────────────

class TestGatesSelectTheSameModels:
    def test_registry_matches_the_old_inheritance_test_for_every_model(self):
        for model in apps.get_models():
            expected = issubclass(model, SnapModel) and model is not SnapModel
            assert registry.is_registered(model) is expected, model._meta.label

    def test_the_demo_models_are_registered(self):
        """A guard against the sweep above passing on an empty/degenerate set."""
        from demo.apps.shop.models import Customer, Order, Product

        assert [registry.is_registered(m) for m in (Product, Customer, Order)] == [True] * 3

    @pytest.mark.django_db
    def test_reindexable_snapmodels_still_reads_the_snap_models(self):
        from demo.apps.shop.models import Product

        selected = reindexable_snapmodels()
        assert Product in selected
        assert all(registry.is_registered(model) for model in selected)
        assert Permission not in selected
