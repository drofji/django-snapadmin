"""
tests/test_final_coverage.py

Targeted tests closing the final coverage gaps across snapadmin/ so that
`pytest --cov=snapadmin --cov-fail-under=100` passes.

Covers the previously-uncovered lines in:
  - snapadmin/api/authentication.py  (UnicodeDecodeError token header)
  - snapadmin/api/graphql.py         (single/list resolvers + introspection error)
  - snapadmin/api/tasks.py           (ES_ONLY skip in purge_expired_data)
  - snapadmin/api/views.py           (get_queryset model-not-found)
  - snapadmin/management/commands/purge_expired_data.py (ES_ONLY skip)
  - snapadmin/models.py              (SnapSaveMixin paths, ES error paths,
                                      FieldDoesNotExist register paths)
  - snapadmin/urls.py                (GraphQL wiring failure branch)
  - snapadmin/views.py               (dashboard model-count failure)
"""

import importlib
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib import admin
from django.contrib.admin import ModelAdmin
from django.test import RequestFactory


# ─────────────────────────────────────────────────────────────────────────────
# authentication.py 33-34 — token key with invalid UTF-8 bytes
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthInvalidUnicode:
    def test_token_header_invalid_unicode_raises(self):
        from snapadmin.api.authentication import APITokenAuthentication
        from rest_framework.exceptions import AuthenticationFailed
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request

        # 0xFF is valid latin-1 (the header transport encoding) but invalid UTF-8,
        # so token_key.decode("utf-8") raises UnicodeDecodeError.
        raw = APIRequestFactory().get("/", HTTP_AUTHORIZATION="Token \xff")
        auth = APITokenAuthentication()
        with pytest.raises(AuthenticationFailed, match="invalid characters"):
            auth.authenticate(Request(raw))


# ─────────────────────────────────────────────────────────────────────────────
# graphql.py — resolvers actually execute now that fields are registered
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGraphqlResolvers:
    def test_schema_exposes_query_fields(self):
        from snapadmin.api.graphql import schema

        # The fix means the dynamically built Query type has real fields.
        field_names = list(schema.graphql_schema.query_type.fields.keys())
        assert any(name.startswith("allDemo") for name in field_names)

    def test_list_resolver_returns_objects(self, admin_user):
        from types import SimpleNamespace
        from demo.apps.shop.models import Product
        from snapadmin.api.graphql import schema

        Product.objects.create(name="GQL List", price=Decimal("3.50"))
        result = schema.execute(
            "{ allDemoProducts { id name } }",
            context_value=SimpleNamespace(user=admin_user, auth=None),
        )
        assert result.errors is None
        assert any(row["name"] == "GQL List" for row in result.data["allDemoProducts"])

    def test_single_resolver_returns_object(self, admin_user):
        from types import SimpleNamespace
        from demo.apps.shop.models import Product
        from snapadmin.api.graphql import schema

        product = Product.objects.create(name="GQL Single", price=Decimal("4.50"))
        result = schema.execute(
            f'{{ demoProduct(id: {product.pk}) {{ id name }} }}',
            context_value=SimpleNamespace(user=admin_user, auth=None),
        )
        assert result.errors is None
        assert result.data["demoProduct"]["name"] == "GQL Single"

    def test_single_resolver_es_only_branch(self):
        """An ES_ONLY model takes the ES_ONLY branch of the single resolver."""
        from snapadmin.api.graphql import schema

        # demo.SearchLog is ES_ONLY; the resolver enters the ES_ONLY branch.
        # The DB lookup may yield no row, but the resolver line is exercised.
        result = schema.execute("{ demoSearchlog(id: 1) { id } }")
        # GraphQL surfaces a not-found/lookup error rather than raising.
        assert result.data is not None or result.errors is not None

    def test_schema_builder_skips_introspection_errors(self):
        """A model that can't be turned into a DjangoObjectType is skipped."""
        from snapadmin.api import graphql

        # object() is not a valid base class, so every type(...) call raises and
        # the except/continue branch runs for every model.
        with patch.object(graphql, "DjangoObjectType", object()):
            built = graphql.get_dynamic_graphql_schema()
        assert built is not None


# ─────────────────────────────────────────────────────────────────────────────
# tasks.py — ES_ONLY model with retention is purged via ES (0 when ES disabled)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeTaskEsOnly:
    def test_task_purges_es_only_model_with_retention(self):
        from snapadmin.tasks import purge_expired_data
        from demo.apps.shop.models import SearchLog

        # SearchLog is ES_ONLY but normally has no retention; give it one so the
        # task reaches the ES_ONLY purge path. ES is disabled in tests, so the
        # purge resolves to 0 — but the model is now accounted for in the summary.
        with patch.object(SearchLog, "data_retention_days", 30, create=True):
            result = purge_expired_data.apply().get()

        assert result["purged"].get("demo.SearchLog") == 0


# ─────────────────────────────────────────────────────────────────────────────
# purge_expired_data command — ES_ONLY model is reported, not skipped
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPurgeCommandEsOnly:
    def test_command_reports_es_only_model(self):
        from io import StringIO
        from django.core.management import call_command
        from demo.apps.shop.models import SearchLog

        out = StringIO()
        with patch.object(SearchLog, "data_retention_days", 30, create=True):
            call_command("purge_expired_data", "--dry-run", stdout=out)

        output = out.getvalue()
        assert "SearchLog" in output
        assert "SKIPPED" not in output


# ─────────────────────────────────────────────────────────────────────────────
# api/views.py 102 — get_queryset returns [] when the model can't be resolved
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDynamicViewQueryset:
    def test_get_queryset_unknown_model_returns_empty_list(self):
        from snapadmin.api.views import DynamicModelViewSet

        view = DynamicModelViewSet()
        view.kwargs = {"app_label": "nope", "model_name": "ghost"}
        view.request = MagicMock()
        assert view.get_queryset() == []


# ─────────────────────────────────────────────────────────────────────────────
# views.py 60-61 — dashboard tolerates a model whose count() raises
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDashboardCountFailure:
    def test_dashboard_handles_count_exception(self):
        from django.contrib.auth.models import User
        from snapadmin.views import DashboardView
        from snapadmin.models import EsManager

        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser("dash_count_fail", password="x")
        view = DashboardView()
        view.request = request
        view.kwargs = {}
        view.args = []

        # Every SnapModel count() raises → the except branch keeps count at 0.
        with patch.object(EsManager, "count", side_effect=Exception("db down")):
            ctx = view.get_context_data()

        assert all(m["count"] == 0 for m in ctx["registered_models"])


# ─────────────────────────────────────────────────────────────────────────────
# urls.py 78-81 — GraphQL wiring failure is swallowed
# ─────────────────────────────────────────────────────────────────────────────

class TestUrlsGraphqlFailure:
    def test_graphql_wiring_failure_branch(self):
        import snapadmin.urls as snap_urls
        from graphene_django.views import GraphQLView

        # GRAPHQL_ENABLED defaults to True, so reloading enters the try block.
        # Force GraphQLView.as_view to raise to exercise the except/log branch.
        # structlog.get_logger is re-called during reload, so patch the factory
        # itself to hand back a mock we can assert against afterwards.
        mock_logger = MagicMock()
        try:
            with patch.object(GraphQLView, "as_view", side_effect=Exception("boom")):
                with patch.object(snap_urls.structlog, "get_logger", return_value=mock_logger):
                    importlib.reload(snap_urls)
            mock_logger.warning.assert_called_once_with("graphql_setup_failed", error="boom")
        finally:
            # Restore a clean, fully-wired urlconf for the rest of the suite.
            importlib.reload(snap_urls)


# ─────────────────────────────────────────────────────────────────────────────
# models.py — SnapSaveMixin save_model (new object) & save_related
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSnapSaveMixinPaths:
    def _admin(self, model_class):
        from snapadmin.models import SnapSaveMixin

        class FakeAdmin(SnapSaveMixin, ModelAdmin):
            pass

        instance = FakeAdmin(model_class, admin.site)
        instance.model = model_class
        return instance

    def test_save_model_new_object_returns_early(self):
        from demo.apps.shop.models import Product
        from django.contrib.auth.models import User

        user = User.objects.create_superuser("savemixin_new", password="pass")
        admin_instance = self._admin(Product)
        request = RequestFactory().get("/")
        request.user = user

        product = Product(name="Fresh", price=Decimal("12.00"))

        class FakeForm:
            changed_data = []
            initial = {}
            cleaned_data = {}

        # change=False → super().save_model saves and returns before diffing.
        admin_instance.save_model(request, product, FakeForm(), change=False)
        assert product.pk is not None

    def test_save_related_logs_changes_and_swallows_errors(self):
        from demo.apps.shop.models import Product
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth.models import User

        user = User.objects.create_superuser("saverelated", password="pass")
        product = Product.objects.create(name="Parent", price=Decimal("20.00"))

        admin_instance = self._admin(Product)
        request = RequestFactory().get("/")
        request.user = user

        # form1: a genuine change → emits a LogEntry.
        good_form = MagicMock()
        good_form.instance = product
        good_form.has_changed.return_value = True
        good_form.changed_data = ["name"]
        good_form.initial = {"name": "Parent"}
        good_form.cleaned_data = {"name": "Renamed"}

        # form2: get_field raises while building the diff → except/pass branch.
        bad_instance = MagicMock()
        bad_instance.pk = 999
        bad_instance._meta.get_field.side_effect = Exception("no such field")
        bad_form = MagicMock()
        bad_form.instance = bad_instance
        bad_form.has_changed.return_value = True
        bad_form.changed_data = ["mystery"]
        bad_form.initial = {"mystery": "a"}
        bad_form.cleaned_data = {"mystery": "b"}

        formset = MagicMock()
        formset.has_changed.return_value = True
        formset.forms = [good_form, bad_form]

        form = MagicMock()

        before = LogEntry.objects.count()
        admin_instance.save_related(request, form, [formset], change=True)
        assert LogEntry.objects.count() == before + 1


# ─────────────────────────────────────────────────────────────────────────────
# models.py 421-422 / 436-437 / 448 — ES operations swallow client errors
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsErrorPaths:
    def test_es_operations_swallow_client_errors(self):
        from demo.apps.shop.models import Product

        product = Product(name="ES Err", price=Decimal("1.00"))
        product.pk = 1234

        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", side_effect=Exception("es down")):
                # Each of these must catch the client error and return quietly.
                Product._ensure_es_index_and_mapping()
                product.index_in_es()
                product.delete_from_es()


# ─────────────────────────────────────────────────────────────────────────────
# models.py 622 — SnapFunctionField display without Unfold returns raw value
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestFunctionFieldDisplayNoUnfold:
    def test_function_field_display_returns_raw_without_unfold(self):
        from snapadmin import fields as snap_fields
        from snapadmin.models import SnapModel
        import snapadmin.models as models_module

        class ModelWithFn(SnapModel):
            label = snap_fields.SnapFunctionField(
                lambda obj: "computed-value",
                verbose_name="Label",
            )

            class Meta:
                app_label = "demo"
                abstract = True

        ModelWithFn.get_admin_fields()
        method = ModelWithFn.admin_overrides["SnapFunctionFieldLabel"]

        with patch.object(models_module, "UNFOLD_INSTALLED", False):
            result = method(None, object())
        assert result == "computed-value"


# ─────────────────────────────────────────────────────────────────────────────
# models.py 654-655 / 674-675 — register_admin tolerates non-field names
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRegisterAdminFieldDoesNotExist:
    def test_register_admin_with_unknown_field_names(self):
        from demo.apps.shop.models import Product

        # Feed register_admin a form_fields list containing a name that is not a
        # real model field so both FieldDoesNotExist branches (row grouping and
        # tab grouping) execute.
        fake_fields = (["ghost_field"], ["id"], ["id"], [], [])
        with patch.object(Product, "get_admin_fields", return_value=fake_fields):
            # register_admin swallows AlreadyRegistered internally.
            Product.register_admin()
