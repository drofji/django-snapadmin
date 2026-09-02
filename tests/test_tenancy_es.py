"""
tests/test_tenancy_es.py — tenant scoping (#FUT1) on the ES-native query methods

``es_search``/``es_filter``/``es_aggregate``/``es_count``/``es_scan`` build
their own Elasticsearch query body and, for ``ES_ONLY`` models, reconstruct
objects straight from the ES response — none of that ever reaches
``EsManager.get_queryset()``'s scoping hook (see ``EsManager.get_queryset``'s
own docstring), so ``SnapModel._tenant_es_term()`` /
``_with_tenant_es_scope()`` / ``_resolve_es_terms()`` exist to force the
tenant constraint into the query body itself. This file pins that mechanism
in isolation, against a throwaway ``ES_ONLY`` + ``tenant_scoped`` model —
not the demo's ``SearchLog``, which every other ES test file in this suite
already uses untenanted; making it tenant-scoped too would require rewiring
all of them for a concern this file already covers on its own.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.db import models as django_models
from django.test import override_settings
from django.test.utils import isolate_apps

from snapadmin import tenancy
from snapadmin.models import EsStorageMode, SnapModel


def _es_returning(sources):
    es = MagicMock()
    es.search.return_value = {"hits": {"hits": [{"_source": s} for s in sources]}}
    es.count.return_value = {"count": len(sources)}
    return es


def _body(es):
    return es.search.call_args.kwargs["body"]


@isolate_apps("snapadmin")
def _tenant_es_model():
    """A throwaway ES_ONLY + tenant_scoped SnapModel, freshly built per call
    so tests never share registry/mapping state."""

    class TenantWidget(SnapModel):
        name = django_models.CharField(max_length=100)
        tenant_id = tenancy.tenant_field()
        tenant_scoped = True
        es_storage_mode = EsStorageMode.ES_ONLY
        es_auto_mapping = True
        subject_path = None

        class Meta:
            app_label = "snapadmin"
            managed = False

    return TenantWidget


@pytest.fixture
def Model():
    return _tenant_es_model()


# ── _tenant_es_term() ─────────────────────────────────────────────────────────

class TestTenantEsTerm:
    def test_not_tenant_scoped_returns_none(self):
        class Plain(SnapModel):
            name = django_models.CharField(max_length=10)
            subject_path = None

            class Meta:
                app_label = "snapadmin"
                abstract = True

        assert Plain._tenant_es_term() is None

    def test_all_tenants_returns_none(self, Model):
        with tenancy.use_all_tenants():
            assert Model._tenant_es_term() is None

    def test_no_tenant_bound_returns_the_unmatchable_sentinel(self, Model):
        assert Model._tenant_es_term() == ("_id", "__snapadmin_no_tenant_context__")

    def test_bound_tenant_resolves_the_mapped_keyword_subfield(self, Model):
        # tenant_id is a plain CharField -> auto-mapped "text" with a ".raw"
        # keyword sub-field (see _derive_es_field_mapping) -> the term must
        # target that sub-field, exactly like any other text field would.
        with tenancy.use_tenant("acme"):
            assert Model._tenant_es_term() == ("tenant_id.raw", "acme")

    def test_unmapped_tenant_field_fails_closed(self, Model):
        # es_auto_mapping off with no explicit es_mapping -> the tenant field
        # cannot be resolved at all; must still fail closed, not raise and
        # not silently skip the constraint.
        Model.es_auto_mapping = False
        Model.es_mapping = None
        with tenancy.use_tenant("acme"):
            assert Model._tenant_es_term() == ("_id", "__snapadmin_no_tenant_context__")


# ── _with_tenant_es_scope() / es_search() ─────────────────────────────────────

class TestEsSearchTenantScope:
    def test_query_unwrapped_with_no_tenant_scoping(self):
        class Untenanted(SnapModel):
            name = django_models.CharField(max_length=10)
            es_storage_mode = EsStorageMode.ES_ONLY
            es_auto_mapping = True
            subject_path = None

            class Meta:
                app_label = "snapadmin"
                abstract = True

        assert Untenanted._with_tenant_es_scope({"match_all": {}}) == {"match_all": {}}

    def test_bound_tenant_wraps_the_query_in_a_filter_clause(self, Model):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es), \
                tenancy.use_tenant("acme"):
            Model.es_search("hello")
        body = _body(es)
        assert body["query"]["bool"]["filter"] == [{"term": {"tenant_id.raw": "acme"}}]
        assert "multi_match" in body["query"]["bool"]["must"][0]

    def test_no_tenant_bound_never_matches_a_real_document(self, Model):
        es = _es_returning([{"id": 1, "name": "leaked"}])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es):
            result = Model.es_search("hello")
        body = _body(es)
        assert body["query"]["bool"]["filter"] == [
            {"term": {"_id": "__snapadmin_no_tenant_context__"}}
        ]
        # The mock still "returns" a hit (it doesn't know about the filter —
        # a real ES would not), so this only proves the query asked for
        # isolation; the fail-closed *result* shape is EsManager.get_queryset's
        # job (see tests/test_tenancy.py) for the .objects path, and this
        # query-body assertion is what covers the ES-native methods' half.
        assert list(result) == [result[0]]  # sanity: still an EsQuerySet

    def test_use_all_tenants_leaves_the_query_unfiltered(self, Model):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es), \
                tenancy.use_all_tenants():
            Model.es_search("hello")
        body = _body(es)
        assert body["query"] == {
            "multi_match": {
                "query": "hello", "fields": Model._es_search_fields(),
                "fuzziness": "AUTO", "lenient": True,
            }
        }


# ── _resolve_es_terms() / es_filter(), es_aggregate(), es_count(), es_scan() ──

class TestResolveEsTermsTenantScope:
    def test_es_filter_forces_the_tenant_term_into_the_query(self, Model):
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es), \
                tenancy.use_tenant("acme"):
            Model.es_filter(name="Widget")
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"term": {"tenant_id.raw": "acme"}} in filters
        assert {"term": {"name.raw": "Widget"}} in filters

    def test_es_filter_caller_cannot_override_the_tenant_term(self, Model):
        # A caller-supplied term for the same ES field is overridden, never
        # merged — tenant scoping is not something es_filter's own terms
        # widen (see _resolve_es_terms's docstring).
        es = _es_returning([])
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es), \
                tenancy.use_tenant("acme"):
            Model.es_filter(tenant_id="someone-elses-tenant")
        filters = _body(es)["query"]["bool"]["filter"]
        assert {"term": {"tenant_id.raw": "acme"}} in filters
        assert {"term": {"tenant_id.raw": "someone-elses-tenant"}} not in filters

    def test_es_aggregate_forces_the_tenant_term(self, Model):
        es = MagicMock()
        es.search.return_value = {"aggregations": {"name": {"buckets": []}}}
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es), \
                tenancy.use_tenant("acme"):
            Model.es_aggregate("name")
        filters = es.search.call_args.kwargs["body"]["query"]["bool"]["filter"]
        assert {"term": {"tenant_id.raw": "acme"}} in filters

    def test_es_count_forces_the_tenant_term(self, Model):
        es = MagicMock()
        es.count.return_value = {"count": 0}
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es), \
                tenancy.use_tenant("acme"):
            Model.es_count()
        filters = es.count.call_args.kwargs["body"]["query"]["bool"]["filter"]
        assert {"term": {"tenant_id.raw": "acme"}} in filters

    def test_es_scan_forces_the_tenant_term(self, Model):
        es = MagicMock()
        es.search.return_value = {"hits": {"hits": []}}
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Model, "get_es_client", return_value=es), \
                tenancy.use_tenant("acme"):
            list(Model.es_scan())
        filters = es.search.call_args.kwargs["body"]["query"]["bool"]["filter"]
        assert {"term": {"tenant_id.raw": "acme"}} in filters
