"""
tests/test_snap_model_decorator.py — ``@snap_model`` on a plain ``django.db.models.Model``

The decorator is the second way to declare a SnapAdmin model: instead of
subclassing :class:`~snapadmin.models.SnapModel`, a project keeps its own model
layer and opts in from the outside. What is pinned here is the whole bargain —
both what a decorated plain model *gets* (registration, and every surface that
reads its configuration through ``get_model_meta``) and, just as deliberately,
what it does **not** get (the ES manager, ``purge_expired``, ``formatted_id``,
the generated admin). The absences are asserted directly rather than left
untested, because the docs promise them.

Models are declared under ``isolate_apps`` so they never enter the project's
real app registry: they need no table, no migration, and they cannot leak into
the sweeps that other tests run over ``apps.get_models()``.
"""

from itertools import count

import pytest
from django.apps import apps as global_apps
from django.db import models as django_models
from django.test.utils import isolate_apps

from snapadmin import registry
from snapadmin.models import EsManager, EsStorageMode, SnapModel, snap_model


# ── declaration helpers ──────────────────────────────────────────────────────

_counter = count()


def _make_plain_model(**meta):
    """A decorated plain ``models.Model``, isolated from the project registry.

    Each call gets a fresh class *name* as well as a fresh class: several of the
    surfaces under test cache what they build per ``app_label.model_name``
    (serializers, filtersets, GraphQL type names), so reusing one name would let
    one test's model answer another test's assertion.
    """
    name = f"Ledger{next(_counter)}"
    with isolate_apps("snapadmin"):
        model = type(
            name,
            (django_models.Model,),
            {
                "__module__": __name__,
                "name": django_models.CharField(max_length=50),
                "secret": django_models.CharField(max_length=50),
                "amount": django_models.IntegerField(default=0),
                "Meta": type("Meta", (), {"app_label": "snapadmin"}),
            },
        )
        return snap_model(**meta)(model)


# ── registration: the gate every surface asks ────────────────────────────────

class TestDecoratorRegisters:
    def test_a_decorated_plain_model_is_registered(self):
        Ledger = _make_plain_model()

        assert registry.is_registered(Ledger) is True
        assert SnapModel.is_concrete_subclass(Ledger) is True

    def test_the_same_model_undecorated_is_not_registered(self):
        with isolate_apps("snapadmin"):
            class Bare(django_models.Model):
                class Meta:
                    app_label = "snapadmin"

            assert registry.is_registered(Bare) is False

    def test_the_decorator_returns_the_class_unchanged(self):
        with isolate_apps("snapadmin"):
            class Target(django_models.Model):
                class Meta:
                    app_label = "snapadmin"

            assert snap_model()(Target) is Target

    def test_it_refuses_anything_that_is_not_a_django_model(self):
        class NotAModel:
            pass

        with pytest.raises(TypeError, match="django.db.models.Model"):
            snap_model()(NotAModel)

    def test_it_refuses_a_non_class(self):
        with pytest.raises(TypeError, match="django.db.models.Model"):
            snap_model()("demo.Product")


# ── the metadata it stores, and how it is read back ──────────────────────────

class TestDecoratorMetadata:
    def test_only_the_keywords_given_are_stored(self):
        Ledger = _make_plain_model(api_read_only=True)

        assert registry.meta_for(Ledger) == {"api_read_only": True}

    def test_stored_values_are_read_through_the_accessor(self):
        Ledger = _make_plain_model(api_write_fields=["name"], offline_cache_limit=7)

        assert registry.get_model_meta(Ledger, "api_write_fields") == ["name"]
        assert registry.get_model_meta(Ledger, "offline_cache_limit", 100) == 7

    def test_an_unset_name_falls_back_to_the_supplied_default(self):
        Ledger = _make_plain_model(api_read_only=True)

        assert registry.get_model_meta(Ledger, "api_write_fields", None) is None
        assert registry.get_model_meta(Ledger, "offline_mode", False) is False

    def test_sequences_are_normalised_to_lists(self):
        """A tuple in, a list out — every reader treats these as lists."""
        Ledger = _make_plain_model(api_write_fields=("name",), search_fields=("name",))

        meta = registry.meta_for(Ledger)
        assert meta["api_write_fields"] == ["name"]
        assert meta["search_fields"] == ["name"]

    def test_mappings_are_copied_so_the_caller_cannot_mutate_the_entry(self):
        lookups = {"name": ["exact"]}
        Ledger = _make_plain_model(api_filter_lookups=lookups)

        lookups["name"].append("icontains")
        assert registry.get_model_meta(Ledger, "api_filter_lookups") == {"name": ["exact"]}

    def test_an_explicit_none_is_stored_as_none(self):
        """``None`` is a meaningful value (no allowlist) — not "unset"."""
        Ledger = _make_plain_model(api_write_fields=None)

        assert registry.meta_for(Ledger) == {"api_write_fields": None}


class TestAccessorFallback:
    def test_a_snapmodel_subclass_still_reads_its_class_attributes(self):
        class Gadget(SnapModel):
            api_write_fields = ["name"]

            class Meta:
                app_label = "demo"
                abstract = True

        assert registry.meta_for(Gadget) == {}
        assert registry.get_model_meta(Gadget, "api_write_fields") == ["name"]
        assert registry.get_model_meta(Gadget, "api_read_only", False) is False

    def test_decorating_a_snapmodel_subclass_overrides_only_what_it_passes(self):
        @snap_model(api_read_only=True)
        class Gadget(SnapModel):
            api_write_fields = ["name"]

            class Meta:
                app_label = "demo"
                abstract = True

        assert registry.get_model_meta(Gadget, "api_read_only") is True
        assert registry.get_model_meta(Gadget, "api_write_fields") == ["name"]

    def test_a_plain_getattr_default_still_wins_for_an_unknown_name(self):
        Ledger = _make_plain_model()

        assert registry.get_model_meta(Ledger, "not_a_setting", "fallback") == "fallback"


# ── the REST surface actually reads the decorated configuration ──────────────

class TestRestSurfaceReadsTheMetadata:
    def test_the_generated_serializer_honours_api_write_fields(self):
        from snapadmin.api.serializers import build_model_serializer

        Ledger = _make_plain_model(api_write_fields=["name"])
        serializer = build_model_serializer(Ledger)()

        assert serializer.fields["name"].read_only is False
        assert serializer.fields["amount"].read_only is True

    def test_the_generated_serializer_honours_api_exclude_fields(self):
        from snapadmin.api.serializers import build_model_serializer

        Ledger = _make_plain_model(api_exclude_fields=["secret"])
        serializer = build_model_serializer(Ledger)()

        assert "secret" not in serializer.fields
        assert "name" in serializer.fields

    def test_without_the_decorator_config_every_field_stays_writable(self):
        """The comparison case, so the assertion above pins the decorator, not the default."""
        from snapadmin.api.serializers import build_model_serializer

        Ledger = _make_plain_model()
        serializer = build_model_serializer(Ledger)()

        assert serializer.fields["amount"].read_only is False

    def test_the_viewset_http_method_policy_honours_api_read_only(self):
        from snapadmin.api.views import DynamicModelViewSet

        Ledger = _make_plain_model(api_read_only=True)
        view = DynamicModelViewSet()
        view.kwargs = {"app_label": "snapadmin", "model_name": "ledger"}
        view._get_model_class = lambda: Ledger

        assert sorted(view._resolve_http_method_names()) == ["get", "head", "options"]

    def test_the_viewset_http_method_policy_honours_the_explicit_allowlist(self):
        from snapadmin.api.views import DynamicModelViewSet

        Ledger = _make_plain_model(api_http_method_names=["get", "post"])
        view = DynamicModelViewSet()
        view.kwargs = {"app_label": "snapadmin", "model_name": "ledger"}
        view._get_model_class = lambda: Ledger

        assert view._resolve_http_method_names() == ["get", "head", "options", "post"]

    def test_search_fields_make_the_db_search_path_work_without_snap_fields(self):
        """A plain model has no ``get_admin_fields()``; ``search_fields`` replaces it."""
        from snapadmin.api.views import DynamicModelViewSet

        assert DynamicModelViewSet._db_search_fields(_make_plain_model()) is None
        assert DynamicModelViewSet._db_search_fields(
            _make_plain_model(search_fields=["name"])
        ) == ("name",)

    def test_the_generated_filter_lookups_honour_api_filter_lookups(self):
        from snapadmin.api.filters import _build_filters_for_model

        narrowed = _build_filters_for_model(_make_plain_model(api_filter_lookups={"name": ["exact"]}))
        default = _build_filters_for_model(_make_plain_model())

        assert [key for key in narrowed if key.startswith("name")] == ["name"]
        assert "name__icontains" in default

    def test_the_generated_filter_lookups_honour_api_default_text_lookups(self):
        from snapadmin.api.filters import _build_filters_for_model

        filters = _build_filters_for_model(_make_plain_model(api_default_text_lookups=["exact"]))

        assert [key for key in filters if key.startswith(("name", "secret"))] == ["name", "secret"]

    def test_the_generated_filters_honour_api_json_filters(self):
        from snapadmin.api.filters import _build_filters_for_model

        with isolate_apps("snapadmin"):
            @snap_model(api_json_filters={"payload": ["a.b"]})
            class Doc(django_models.Model):
                payload = django_models.JSONField(default=dict)

                class Meta:
                    app_label = "snapadmin"

        assert "payload__a__b" in _build_filters_for_model(Doc)


class TestOtherSurfacesReadTheMetadata:
    def test_the_offline_endpoints_pick_up_a_decorated_model(self, monkeypatch):
        from snapadmin.api import offline

        Ledger = _make_plain_model(offline_mode=True, offline_cache_limit=25)
        key = f"snapadmin/{Ledger._meta.model_name}"
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        assert offline.get_offline_model_keys() == [key]
        assert offline.get_offline_model_limits() == {key: 25}

    def test_a_decorated_model_without_offline_mode_stays_out(self, monkeypatch):
        from snapadmin.api import offline

        Ledger = _make_plain_model()
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        assert offline.get_offline_model_keys() == []

    def test_the_graphql_schema_builds_a_type_for_a_decorated_model(self, monkeypatch):
        from snapadmin.api import graphql

        Ledger = _make_plain_model(api_exclude_fields=["secret"])
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        schema = graphql.get_dynamic_graphql_schema()
        object_type = schema.graphql_schema.type_map[f"Snapadmin{Ledger.__name__}Type"]

        assert "name" in object_type.fields
        assert "secret" not in object_type.fields

    def test_the_write_field_system_check_sees_a_decorated_model(self, monkeypatch):
        from snapadmin import checks

        Ledger = _make_plain_model()
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        warnings = checks.check_api_write_fields(None)
        assert [w.id for w in warnings] == ["snapadmin.W004"]

        monkeypatch.setattr(global_apps, "get_models", lambda: [_make_plain_model(api_write_fields=["name"])])
        assert checks.check_api_write_fields(None) == []

    def test_the_diagnostics_inventory_lists_a_decorated_model(self, monkeypatch):
        from snapadmin.diagnostics import inventory

        Ledger = _make_plain_model(api_write_fields=["name"])
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        items = inventory._model_items(set())
        assert items == [{
            "model": f"snapadmin.{Ledger.__name__}",
            "es_mode": EsStorageMode.DB_ONLY.name,
            "retention_days": None,
            "write_restricted": True,
            "masked": False,
        }]


# ── what a decorated plain model deliberately does NOT get ───────────────────

class TestHonestAbsences:
    def test_it_gets_no_elasticsearch_manager_or_queryset(self):
        Ledger = _make_plain_model()

        assert not isinstance(Ledger.objects, EsManager)
        assert not hasattr(Ledger, "es_search")
        assert not hasattr(Ledger, "es_reindex_all")
        assert not hasattr(Ledger, "_ensure_es_index_and_mapping")

    def test_it_gets_no_retention_purge(self):
        Ledger = _make_plain_model()

        assert not hasattr(Ledger, "purge_expired")

    def test_it_gets_no_formatted_id_or_generated_admin(self):
        Ledger = _make_plain_model()

        assert not hasattr(Ledger, "formatted_id")
        assert not hasattr(Ledger, "register_admin")
        assert not hasattr(Ledger, "get_admin_fields")

    def test_register_all_admins_skips_it_instead_of_crashing(self, monkeypatch):
        from django.contrib import admin as django_admin

        Ledger = _make_plain_model()
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        SnapModel.register_all_admins()

        assert Ledger not in django_admin.site._registry

    def test_the_post_migrate_es_hook_skips_it_instead_of_crashing(self, monkeypatch):
        from snapadmin.apps import sync_es_mappings

        Ledger = _make_plain_model()
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        sync_es_mappings(sender=None)  # must not raise

    def test_it_is_never_selected_for_reindexing(self, monkeypatch):
        from snapadmin import models as snap_models

        Ledger = _make_plain_model()
        # Even with the ES flags forced on, the model carries no es_reindex_all,
        # so the reindex sweep must leave it alone rather than crash on it.
        registry.register(Ledger, es_index_enabled=True, es_storage_mode=EsStorageMode.DUAL)
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        assert snap_models.reindexable_snapmodels() == []

    def test_it_is_never_selected_for_the_retention_purge(self, monkeypatch):
        from snapadmin.tasks import purge_expired_data

        Ledger = _make_plain_model()
        registry.register(Ledger, data_retention_days=1)
        monkeypatch.setattr(global_apps, "get_models", lambda: [Ledger])

        assert purge_expired_data() == {"purged": {}, "total": 0, "errors": {}}


# ── #PAR1d — the model-side mirror of the field parity drift guard ───────────
#
# fields._SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS tracks Snap*Field attributes
# with no snap_field() equivalent (empty since #PAR1c). The model side has no
# single enum of SnapModel's ~30 configuration attributes to diff against —
# unlike SnapFieldAttributeEnum, they are declared inline with no shared
# marker — so models._SNAP_MODEL_UNEXPOSED_ATTRIBUTES is a maintained,
# reasoned list instead (every entry names the #RFC1g row that blocks it).
# These tests keep that list honest in the two ways that matter: a tracked
# name must still exist on SnapModel (catching a stale/renamed entry), and it
# must never simultaneously be a decorator keyword (catching a #RFC1g
# capability that shipped without removing its now-stale exclusion).
class TestSnapModelDriftGuard:
    def test_every_tracked_attribute_still_exists_on_snapmodel(self):
        from snapadmin.models import _SNAP_MODEL_UNEXPOSED_ATTRIBUTES

        for name in _SNAP_MODEL_UNEXPOSED_ATTRIBUTES:
            assert hasattr(SnapModel, name), (
                f"{name!r} is tracked in _SNAP_MODEL_UNEXPOSED_ATTRIBUTES but no longer "
                "exists on SnapModel — the entry is stale, remove or update it."
            )

    def test_no_tracked_attribute_is_a_decorator_keyword(self):
        import inspect

        from snapadmin.models import _SNAP_MODEL_UNEXPOSED_ATTRIBUTES

        params = set(inspect.signature(snap_model).parameters)
        overlap = params & _SNAP_MODEL_UNEXPOSED_ATTRIBUTES
        assert overlap == set(), (
            f"{overlap} are both a snap_model() keyword and in "
            "_SNAP_MODEL_UNEXPOSED_ATTRIBUTES — the gap has shipped, remove the entry."
        )

    def test_every_decorator_keyword_matches_a_snapmodel_attribute(self):
        """search_fields is the one deliberate exception: SnapModel derives its
        own search_fields from searchable=True Snap fields rather than a class
        attribute, so it has no attribute of the same name to compare against."""
        import inspect

        params = set(inspect.signature(snap_model).parameters)
        for name in params - {"search_fields"}:
            assert hasattr(SnapModel, name), (
                f"snap_model() accepts {name!r} but SnapModel has no matching attribute"
            )
