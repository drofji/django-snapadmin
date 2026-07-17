"""
tests/test_alpha4_features.py  –  alpha-4 security hardening + ES capabilities

Covers: GraphQL authentication/permissions + token support + pagination/search,
api_exclude_fields across REST/GraphQL/schema, es_auto_mapping derivation, bulk
re-indexing, the X-Snap-Query-Backend toggle and its honest DB-fallback value,
and the seed_demo insecure-admin refusal.
"""

import json
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.db import models as dj_models
from django.test import override_settings

from demo.app.models import AuditLog, Customer, Product, SearchLog, Showcase
from snapadmin.models import SnapModel


def _ctx(user, auth=None):
    return SimpleNamespace(user=user, auth=auth)


# ── GraphQL: authentication & permissions ─────────────────────────────────────

@pytest.mark.django_db
class TestGraphQLSecurity:
    QUERY = "{ allDemoProducts { id name } }"

    def test_anonymous_execution_denied(self):
        from snapadmin.api.graphql import schema
        result = schema.execute(self.QUERY)
        assert result.errors
        assert "Authentication required" in str(result.errors[0])

    def test_user_without_permission_denied(self, regular_user):
        from snapadmin.api.graphql import schema
        result = schema.execute(self.QUERY, context_value=_ctx(regular_user))
        assert result.errors
        assert "Permission denied" in str(result.errors[0])

    def test_token_scope_restricts_models(self, restricted_token):
        # restricted_token allows demo.Product only — even for its superuser owner
        from snapadmin.api.graphql import schema
        ctx = _ctx(restricted_token.user, auth=restricted_token)
        allowed = schema.execute(self.QUERY, context_value=ctx)
        assert allowed.errors is None
        denied = schema.execute("{ allDemoCustomers { id } }", context_value=ctx)
        assert denied.errors
        assert "Permission denied" in str(denied.errors[0])

    @override_settings(SNAPADMIN_GRAPHQL_REQUIRE_AUTH=False)
    def test_auth_can_be_disabled_explicitly(self):
        from snapadmin.api.graphql import schema
        result = schema.execute(self.QUERY)
        assert result.errors is None

    def test_view_accepts_api_token(self, client, api_token, product):
        r = client.post(
            "/api/graphql/",
            data=json.dumps({"query": self.QUERY}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {api_token.token_key}",
        )
        body = r.json()
        assert r.status_code == 200
        assert body.get("errors") is None
        assert any(p["name"] == product.name for p in body["data"]["allDemoProducts"])

    def test_view_rejects_anonymous(self, client, product):
        r = client.post(
            "/api/graphql/",
            data=json.dumps({"query": self.QUERY}),
            content_type="application/json",
        )
        assert "Authentication required" in str(r.json().get("errors"))

    def test_view_invalid_token_stays_anonymous(self, client, product):
        r = client.post(
            "/api/graphql/",
            data=json.dumps({"query": self.QUERY}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Token wrong-token-key",
        )
        assert "Authentication required" in str(r.json().get("errors"))

    def test_view_session_user_passes_through(self, admin_client, product):
        r = admin_client.post(
            "/api/graphql/",
            data=json.dumps({"query": self.QUERY}),
            content_type="application/json",
        )
        assert r.json().get("errors") is None


# ── GraphQL: pagination + search ──────────────────────────────────────────────

@pytest.mark.django_db
class TestGraphQLPaginationAndSearch:
    def test_first_and_offset_slice_results(self, admin_user):
        from snapadmin.api.graphql import schema
        for name in ("P1", "P2", "P3"):
            Product.objects.create(name=name, price=Decimal("1.00"))
        ctx = _ctx(admin_user)
        page = schema.execute("{ allDemoProducts(first: 2) { name } }", context_value=ctx)
        assert len(page.data["allDemoProducts"]) == 2
        offset = schema.execute(
            "{ allDemoProducts(first: 1, offset: 1) { name } }", context_value=ctx
        )
        assert len(offset.data["allDemoProducts"]) == 1

    def test_offset_without_first(self, admin_user):
        from snapadmin.api.graphql import schema
        for name in ("Q1", "Q2"):
            Product.objects.create(name=name, price=Decimal("1.00"))
        result = schema.execute(
            "{ allDemoProducts(offset: 1) { name } }", context_value=_ctx(admin_user)
        )
        assert result.errors is None

    def test_search_argument_filters(self, admin_user):
        from snapadmin.api.graphql import schema
        Product.objects.create(name="GQLUniqueWidget", price=Decimal("1.00"))
        Product.objects.create(name="Other", price=Decimal("1.00"))
        result = schema.execute(
            '{ allDemoProducts(search: "GQLUniqueWidget") { name } }',
            context_value=_ctx(admin_user),
        )
        names = [p["name"] for p in result.data["allDemoProducts"]]
        assert names == ["GQLUniqueWidget"]


# ── api_exclude_fields ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestApiExcludeFields:
    def test_rest_list_hides_excluded_field(self, auth_client):
        AuditLog.objects.create(action="login", user_email="pii@example.com")
        r = auth_client.get("/api/models/demo/AuditLog/")
        rows = r.json()["results"]
        assert rows
        assert "user_email" not in rows[0]
        assert "action" in rows[0]

    def test_schema_view_hides_excluded_field(self, auth_client):
        r = auth_client.get("/api/models/schema/")
        entry = next(m for m in r.json()["models"] if m["model_name"] == "AuditLog")
        names = [f["name"] for f in entry["fields"]]
        assert "user_email" not in names
        assert "action" in names

    def test_graphql_type_has_no_excluded_field(self, admin_user):
        from snapadmin.api.graphql import schema
        result = schema.execute(
            "{ allDemoAuditlogs { id userEmail } }", context_value=_ctx(admin_user)
        )
        assert result.errors  # userEmail is not part of the type at all


# ── es_auto_mapping ───────────────────────────────────────────────────────────

class TestEsAutoMapping:
    def test_searchlog_auto_mapping_derived(self):
        mapping = SearchLog.get_es_mapping()
        assert mapping["query"]["type"] == "text"
        assert mapping["query"]["fields"]["raw"]["type"] == "keyword"
        assert mapping["results_count"] == {"type": "long"}
        assert mapping["timestamp"] == {"type": "date"}
        assert "id" not in mapping  # pk mapped explicitly as the document id

    def test_explicit_es_mapping_overrides_derived(self):
        with patch.object(SearchLog, "es_mapping", {"query": {"type": "keyword"}}):
            mapping = SearchLog.get_es_mapping()
        assert mapping["query"] == {"type": "keyword"}
        assert mapping["results_count"] == {"type": "long"}  # derived entries kept

    def test_auto_mapping_off_returns_explicit_mapping(self):
        assert Product.get_es_mapping() == Product.es_mapping

    def test_showcase_field_type_coverage(self):
        with patch.object(Showcase, "es_auto_mapping", True, create=True):
            mapping = Showcase.get_es_mapping()
        assert mapping["char_field"]["type"] == "text"
        assert mapping["email_field"] == {"type": "keyword"}
        assert mapping["uuid_field"] == {"type": "keyword"}
        assert mapping["file_field"] == {"type": "keyword"}
        assert mapping["duration_field"] == {"type": "keyword"}
        assert mapping["time_field"] == {"type": "keyword"}
        assert mapping["integer_field"] == {"type": "long"}
        assert mapping["float_field"] == {"type": "double"}
        assert mapping["decimal_field"] == {"type": "scaled_float", "scaling_factor": 100}
        assert mapping["date_field"] == {"type": "date"}
        assert mapping["datetime_field"] == {"type": "date"}
        assert mapping["boolean_field"] == {"type": "boolean"}
        assert mapping["json_field"] == {"type": "object"}

    def test_foreign_key_maps_to_long(self):
        with patch.object(Customer, "es_auto_mapping", True, create=True):
            mapping = Customer.get_es_mapping()
        assert mapping["email"] == {"type": "keyword"}
        assert mapping["first_name"]["type"] == "text"

    def test_fk_and_unknown_field_branches(self):
        fk = Product._meta.get_field("category")
        assert SnapModel._derive_es_field_mapping(fk) == {"type": "long"}
        assert SnapModel._derive_es_field_mapping(dj_models.BinaryField()) is None

    def test_auto_mapped_search_fields_are_text_only(self):
        assert SearchLog._es_search_fields() == ["query"]


# ── Bulk re-indexing ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBulkReindex:
    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_partial_errors_reported(self, product):
        with patch.object(Product, "get_es_client", return_value=MagicMock()), \
                patch.object(Product, "_ensure_es_index_and_mapping"), \
                patch("elasticsearch.helpers.bulk", return_value=(1, ["boom"])):
            result = Product.es_reindex_all()
        assert result == {"indexed": 1, "errors": ["boom"]}

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_bulk_exception_logged(self, product):
        with patch.object(Product, "get_es_client", return_value=MagicMock()), \
                patch.object(Product, "_ensure_es_index_and_mapping"), \
                patch("elasticsearch.helpers.bulk", side_effect=Exception("down")), \
                patch("snapadmin.models.logger") as log:
            result = Product.es_reindex_all()
        assert result["indexed"] == 0
        assert log.warning.call_args.args[0] == "es_bulk_reindex_failed"

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_es_only_queryset_is_iterated_directly(self):
        # EsQuerySet has no .iterator() — the reindex must not crash on ES_ONLY
        with patch.object(SearchLog, "get_es_client", return_value=MagicMock()), \
                patch.object(SearchLog, "_ensure_es_index_and_mapping"), \
                patch("elasticsearch.helpers.bulk", return_value=(0, [])):
            result = SearchLog.es_reindex_all()
        assert result == {"indexed": 0}


# ── X-Snap-Query-Backend: toggle + honest fallback ────────────────────────────

@pytest.mark.django_db
class TestQueryBackendHeader:
    @override_settings(SNAPADMIN_QUERY_BACKEND_HEADER=False)
    def test_header_suppressed_by_setting(self, auth_client, product):
        r = auth_client.get("/api/models/demo/Product/")
        assert "X-Snap-Query-Backend" not in r

    def test_header_reports_db_when_es_fallback_ran(self, auth_client, product):
        # Routing chooses ES, but the client blows up → es_search falls back to
        # the DB internally; the header must say "database", not "elasticsearch".
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", side_effect=Exception("es down")):
            r = auth_client.get(f"/api/models/demo/Product/?search={product.name}")
        assert r["X-Snap-Query-Backend"] == "database"
        assert any(p["id"] == product.pk for p in r.json()["results"])


# ── seed_demo: insecure-admin refusal ─────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestSeedAdminPassword:
    def test_refuses_default_password_when_not_debug(self, monkeypatch):
        from django.contrib.auth.models import User
        monkeypatch.delenv("SNAPADMIN_SEED_ADMIN_PASSWORD", raising=False)
        out = StringIO()
        call_command("seed_demo", count=1, no_index=True, stdout=out)
        assert "Refusing" in out.getvalue()
        assert not User.objects.filter(is_superuser=True).exists()

    @override_settings(DEBUG=True)
    def test_debug_default_password_allowed_with_warning(self, monkeypatch):
        from django.contrib.auth.models import User
        monkeypatch.delenv("SNAPADMIN_SEED_ADMIN_PASSWORD", raising=False)
        out = StringIO()
        call_command("seed_demo", count=1, no_index=True, stdout=out)
        assert "admin/admin" in out.getvalue()
        assert User.objects.filter(is_superuser=True).exists()
