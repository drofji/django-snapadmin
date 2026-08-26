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
from itertools import count
from weakref import WeakKeyDictionary

import pytest
from django.apps import apps
from django.contrib.auth.models import Permission
from django.db import models as django_models
from django.test import override_settings
from django.test.utils import isolate_apps

from snapadmin import registry
from snapadmin.models import SnapModel, reindexable_snapmodels, snap_model


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
        """Holds because no installed model uses ``@snap_model``.

        The decorator (see ``tests/test_snap_model_decorator.py``) is the one way
        a model can be registered without subclassing ``SnapModel``; every model
        this project installs still subclasses it, so the equivalence with the
        predicate the gates used to run is exact. Should a decorated plain model
        ever join an installed app, this sweep is the test that must be relaxed —
        deliberately, not by deleting it.
        """
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


# ── #RFC1e — the model-meta precedence rule ───────────────────────────────────
#
# get_model_meta() resolves four tiers in order: registry entry (the decorator
# argument) > class attribute > a project-wide SNAPADMIN_<NAME> setting > the
# caller's built-in default. Proven here for one representative name from each
# family the roadmap names (api_*, es_*, offline_*, data_retention_days),
# not just one name in isolation.
#
# SnapModel declares a concrete class attribute for every name below, and
# getattr() finds that *inherited* value before tier 3 is ever reached — so
# the settings tier can only actually be observed on a plain model registered
# with @snap_model, which carries no such base-class default. Testing it on a
# SnapModel subclass would prove nothing (tier 2 always wins first there); see
# get_model_meta's own docstring for the full explanation.
#
# @snap_model()'s own kwargs cover only a subset of these names today
# (es_index_enabled and data_retention_days are not yet accepted — see
# models._SNAP_MODEL_UNEXPOSED_ATTRIBUTES / #RFC1g). registry.register() is
# exactly what the decorator calls underneath, so it populates tier 1
# identically for every name get_model_meta reads, regardless of which names
# snap_model()'s current kwarg allowlist happens to expose.

_rfc1e_counter = count()


def _rfc1e_plain_model(**meta):
    """A `@snap_model`-decorated plain model with no base-class defaults —
    the one route on which the settings tier (#RFC1e) is ever reachable."""
    name = f"Rfc1eTarget{next(_rfc1e_counter)}"
    with isolate_apps("snapadmin"):
        model = type(
            name,
            (django_models.Model,),
            {"__module__": __name__, "Meta": type("Meta", (), {"app_label": "snapadmin"})},
        )
        return snap_model(**meta)(model)


def _rfc1e_snapmodel_subclass(**class_attrs):
    """A throwaway abstract SnapModel subclass carrying the given class attributes."""
    name = f"Rfc1eGadget{next(_rfc1e_counter)}"
    attrs = {
        **class_attrs,
        "__module__": __name__,
        "Meta": type("Meta", (), {"app_label": "demo", "abstract": True}),
    }
    return type(name, (SnapModel,), attrs)


@pytest.mark.parametrize("name, value, builtin_default", [
    ("api_read_only", True, False),        # api_*
    ("es_index_enabled", True, False),      # es_*
    ("offline_mode", True, False),          # offline_*
    ("data_retention_days", 30, None),      # data_retention_days
])
class TestGetModelMetaPrecedence:
    def test_decorator_only(self, name, value, builtin_default):
        model = _rfc1e_plain_model()
        registry.register(model, **{name: value})

        assert registry.get_model_meta(model, name, builtin_default) == value

    def test_class_attribute_only(self, name, value, builtin_default):
        Gadget = _rfc1e_snapmodel_subclass(**{name: value})

        assert registry.get_model_meta(Gadget, name, builtin_default) == value

    def test_decorator_wins_over_a_disagreeing_class_attribute_and_setting(self, name, value, builtin_default):
        """Hybrid: a SnapModel subclass also decorated, the two disagreeing —
        decorator wins — plus a SNAPADMIN_<NAME> global that disagrees with
        both thrown in too, to prove it loses to both (#RFC1e)."""
        Gadget = _rfc1e_snapmodel_subclass(**{name: builtin_default})
        registry.register(Gadget, **{name: value})

        setting_name = f"SNAPADMIN_{name.upper()}"
        with override_settings(**{setting_name: builtin_default}):
            assert registry.get_model_meta(Gadget, name, builtin_default) == value

    def test_class_attribute_wins_over_a_disagreeing_setting(self, name, value, builtin_default):
        Gadget = _rfc1e_snapmodel_subclass(**{name: value})

        setting_name = f"SNAPADMIN_{name.upper()}"
        with override_settings(**{setting_name: builtin_default}):
            assert registry.get_model_meta(Gadget, name, builtin_default) == value

    def test_setting_is_the_third_tier_on_the_snap_model_route(self, name, value, builtin_default):
        """Only reachable via @snap_model — a SnapModel subclass never falls
        through to this tier, see the section docstring above."""
        model = _rfc1e_plain_model()  # no registry entry for `name`

        setting_name = f"SNAPADMIN_{name.upper()}"
        with override_settings(**{setting_name: value}):
            assert registry.get_model_meta(model, name, builtin_default) == value

    def test_builtin_default_is_the_last_tier(self, name, value, builtin_default):
        model = _rfc1e_plain_model()  # no registry entry, no global setting

        assert registry.get_model_meta(model, name, builtin_default) == builtin_default

    def test_a_snapmodel_subclass_never_reaches_the_settings_tier(self, name, value, builtin_default):
        """The asymmetry get_model_meta's docstring calls out explicitly: an
        undecorated SnapModel subclass always answers from its (possibly
        inherited) class attribute, so a global setting cannot reach it even
        when the subclass never overrides the name itself."""
        Gadget = _rfc1e_snapmodel_subclass()  # no override — answers via SnapModel's own default

        setting_name = f"SNAPADMIN_{name.upper()}"
        with override_settings(**{setting_name: value}):
            assert registry.get_model_meta(Gadget, name, builtin_default) == builtin_default
