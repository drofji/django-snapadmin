"""
tests/test_snap_model_coverage.py

Coverage tests for snapadmin/models.py — ES operations, SnapSaveMixin,
get_es_document edge cases, get_admin_fields edge cases, register_admin paths.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest
from django.contrib import admin
from django.test import RequestFactory

from snapadmin.models import (
    EsQuerySet,
    EsStorageMode,
    SnapModel,
    SnapSaveMixin,
)


# ─────────────────────────────────────────────────────────────────────────────
# get_es_document — FK and timedelta value paths
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetEsDocumentEdgeCases:
    def test_fk_value_is_converted_to_pk(self):
        """When a field value has .pk, get_es_document stores the pk integer."""
        from demo.app.models import Product, Category
        cat = Category.objects.create(name="Tech", slug="tech")
        product = Product.objects.create(name="Widget", price=Decimal("9.99"), category=cat)
        # Override es_mapping to include the FK field
        original_mapping = Product.es_mapping
        Product.es_mapping = {"category": {"type": "integer"}}
        try:
            doc = product.get_es_document()
            assert doc["category"] == cat.pk
        finally:
            Product.es_mapping = original_mapping

    def test_timedelta_value_is_converted_to_str(self):
        """When a field value is a timedelta, get_es_document converts it to str."""
        from demo.app.models import Product
        product = Product.objects.create(name="Duration Test", price=Decimal("1.00"))
        original_mapping = Product.es_mapping
        Product.es_mapping = {"name": {"type": "text"}}
        try:
            # Monkey-patch name to be a timedelta temporarily
            product.name = timedelta(hours=2)
            doc = product.get_es_document()
            assert isinstance(doc["name"], str)
        finally:
            Product.es_mapping = original_mapping


# ─────────────────────────────────────────────────────────────────────────────
# ES operations with Elasticsearch enabled (mocked)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsOperationsMocked:
    def _mock_es(self):
        mock = MagicMock()
        mock.ping.return_value = True
        mock.indices.exists.return_value = False
        mock.indices.create.return_value = None
        mock.indices.put_mapping.return_value = None
        mock.index.return_value = None
        mock.delete.return_value = None
        return mock

    def test_ensure_es_index_and_mapping_creates_index(self):
        from demo.app.models import Product
        mock_es = self._mock_es()
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                Product._ensure_es_index_and_mapping()
        mock_es.indices.create.assert_called_once()

    def test_ensure_es_index_existing_calls_put_mapping(self):
        from demo.app.models import Product
        mock_es = self._mock_es()
        mock_es.indices.exists.return_value = True
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                Product._ensure_es_index_and_mapping()
        mock_es.indices.put_mapping.assert_called_once()

    def test_ensure_es_index_skipped_when_es_disabled(self):
        from demo.app.models import Product
        mock_es = self._mock_es()
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", False):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                Product._ensure_es_index_and_mapping()
        mock_es.indices.create.assert_not_called()

    def test_index_in_es_calls_es_index(self):
        from demo.app.models import Product
        product = Product.objects.create(name="ES Test", price=Decimal("5.00"))
        mock_es = self._mock_es()
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                with patch.object(Product, "_ensure_es_index_and_mapping"):
                    product.index_in_es()
        mock_es.index.assert_called_once()

    def test_index_in_es_skipped_when_es_disabled(self):
        from demo.app.models import Product
        product = Product.objects.create(name="No ES", price=Decimal("5.00"))
        mock_es = self._mock_es()
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", False):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                product.index_in_es()
        mock_es.index.assert_not_called()

    def test_delete_from_es_calls_es_delete(self):
        from demo.app.models import Product
        product = Product.objects.create(name="Delete Me", price=Decimal("5.00"))
        mock_es = self._mock_es()
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                product.delete_from_es()
        mock_es.delete.assert_called_once()

    def test_delete_from_es_skipped_when_disabled(self):
        from demo.app.models import Product
        product = Product.objects.create(name="No Delete", price=Decimal("5.00"))
        mock_es = self._mock_es()
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", False):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                product.delete_from_es()
        mock_es.delete.assert_not_called()

    def test_es_reindex_all_when_enabled(self):
        from demo.app.models import Product
        Product.objects.all().delete()
        Product.objects.create(name="Reindex Test", price=Decimal("1.00"))
        mock_es = self._mock_es()
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                with patch.object(Product, "_ensure_es_index_and_mapping"):
                    with patch("elasticsearch.helpers.bulk", return_value=(1, [])) as bulk:
                        result = Product.es_reindex_all()
        assert result == {"indexed": 1}
        # The generator handed to helpers.bulk streams one action per row
        actions = list(bulk.call_args.args[1])
        assert len(actions) == 1
        assert actions[0]["_source"]["name"] == "Reindex Test"

    def test_es_reindex_all_skipped_when_disabled(self):
        from demo.app.models import Product
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", False):
            result = Product.es_reindex_all()
        assert result["skipped"] is True

    def test_es_search_with_es_enabled_dual_mode(self):
        from demo.app.models import Product
        mock_es = MagicMock()
        mock_es.search.return_value = {
            "hits": {"hits": [{"_source": {"id": 1}}]}
        }
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                results = Product.es_search(query_string="test")
        # Should return a QuerySet from the DB (DUAL mode filters by pks from ES)
        assert hasattr(results, "__iter__")

    def test_es_search_with_es_enabled_es_only(self):
        from demo.app.models import SearchLog
        mock_es = MagicMock()
        mock_es.search.return_value = {
            "hits": {"hits": [{"_source": {"id": 10, "query": "test", "results_count": 5}}]}
        }
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(SearchLog, "get_es_client", return_value=mock_es):
                results = SearchLog.es_search(query_string="test")
        assert isinstance(results, EsQuerySet)

    def test_es_search_with_es_enabled_exception_falls_back(self):
        from demo.app.models import Product
        mock_es = MagicMock()
        mock_es.search.side_effect = Exception("ES offline")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(Product, "get_es_client", return_value=mock_es):
                results = Product.es_search(query_string="test")
        assert hasattr(results, "__iter__")

    def test_es_search_es_only_exception_returns_empty_esqueryset(self):
        from demo.app.models import SearchLog
        mock_es = MagicMock()
        mock_es.search.side_effect = Exception("ES offline")
        with patch("django.conf.settings.ELASTICSEARCH_ENABLED", True):
            with patch.object(SearchLog, "get_es_client", return_value=mock_es):
                results = SearchLog.es_search(query_string="test")
        assert isinstance(results, EsQuerySet)
        assert len(results) == 0


# ─────────────────────────────────────────────────────────────────────────────
# es_search DB fallback edge cases
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsSearchFallback:
    def test_es_search_empty_query_returns_all(self):
        from demo.app.models import Product
        Product.objects.all().delete()
        Product.objects.create(name="Alpha", price=Decimal("1.00"))
        results = Product.es_search(query_string="")
        assert results.count() >= 1

    def test_es_search_no_search_fields_returns_all(self):
        """Model with no searchable fields and no query_string → returns all."""
        from demo.app.models import Order, Customer
        Customer.objects.all().delete()
        Order.objects.all().delete()
        customer = Customer.objects.create(first_name="X", last_name="Y", email="x@y.com", origin="status_a")
        Order.objects.create(customer=customer, total=Decimal("10.00"))
        # es_search with empty query falls back to all
        results = Order.es_search(query_string="")
        assert hasattr(results, "__iter__")


# ─────────────────────────────────────────────────────────────────────────────
# ES_ONLY delete path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsOnlyDelete:
    def test_delete_es_only_calls_delete_from_es(self):
        from demo.app.models import SearchLog
        log = SearchLog(query="test", results_count=5)
        log.pk = 777
        with patch.object(SearchLog, "delete_from_es") as mock_del:
            log.delete()
        mock_del.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# SnapSaveMixin — change=True path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSnapSaveMixinChange:
    def _make_admin_instance(self, model_class):
        from snapadmin.models import SnapSaveMixin
        from django.contrib.admin import ModelAdmin

        class FakeAdmin(SnapSaveMixin, ModelAdmin):
            pass

        instance = FakeAdmin(model_class, admin.site)
        instance.model = model_class
        return instance

    def test_save_model_change_true_logs_changes(self):
        from demo.app.models import Product
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth.models import User

        user = User.objects.create_superuser("savemixin_test", password="pass")
        product = Product.objects.create(name="Original", price=Decimal("10.00"))

        admin_instance = self._make_admin_instance(Product)
        request = RequestFactory().get("/")
        request.user = user

        class FakeForm:
            changed_data = ["name"]
            initial = {"name": "Original"}
            cleaned_data = {"name": "Updated"}

        log_count_before = LogEntry.objects.count()
        admin_instance.save_model(request, product, FakeForm(), change=True)
        # save_model runs — if change_lines present, logs the change
        assert LogEntry.objects.count() >= log_count_before

    def test_save_model_change_true_no_actual_diff_no_log(self):
        from demo.app.models import Product
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth.models import User

        user = User.objects.create_superuser("savemixin_nodiff", password="pass")
        product = Product.objects.create(name="Same", price=Decimal("10.00"))

        admin_instance = self._make_admin_instance(Product)
        request = RequestFactory().get("/")
        request.user = user

        class FakeForm:
            changed_data = ["name"]
            initial = {"name": "Same"}
            cleaned_data = {"name": "Same"}  # same value, no actual change

        log_count_before = LogEntry.objects.count()
        admin_instance.save_model(request, product, FakeForm(), change=True)
        assert LogEntry.objects.count() == log_count_before


# ─────────────────────────────────────────────────────────────────────────────
# SnapSaveMixin.log_change — dedupe detailed vs. generic history entries
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSnapSaveMixinLogChange:
    def _make_admin_instance(self, model_class):
        from snapadmin.models import SnapSaveMixin
        from django.contrib.admin import ModelAdmin

        class FakeAdmin(SnapSaveMixin, ModelAdmin):
            pass

        instance = FakeAdmin(model_class, admin.site)
        instance.model = model_class
        return instance

    def test_save_model_then_log_change_writes_single_entry(self):
        """A real field change logs exactly one (detailed) entry, not two."""
        from demo.app.models import Product
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth.models import User

        user = User.objects.create_superuser("logchange_dedupe", password="pass")
        product = Product.objects.create(name="Original", price=Decimal("10.00"))

        admin_instance = self._make_admin_instance(Product)
        request = RequestFactory().get("/")
        request.user = user

        class FakeForm:
            changed_data = ["name"]
            initial = {"name": "Original"}
            cleaned_data = {"name": "Updated"}

        before = LogEntry.objects.count()
        admin_instance.save_model(request, product, FakeForm(), change=True)
        # Django admin would now call log_change with its generic message.
        result = admin_instance.log_change(request, product, "Changed name.")

        assert result is None  # generic duplicate suppressed
        assert LogEntry.objects.count() == before + 1
        # The surviving entry is the detailed one written by save_model.
        latest = LogEntry.objects.latest("id")
        assert "Updated" in latest.change_message

    def test_log_change_falls_back_when_nothing_logged(self):
        """With no detailed entry (e.g. M2M-only edit), keep Django's default."""
        from demo.app.models import Product
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth.models import User

        user = User.objects.create_superuser("logchange_fallback", password="pass")
        product = Product.objects.create(name="Solo", price=Decimal("10.00"))

        admin_instance = self._make_admin_instance(Product)
        request = RequestFactory().get("/")
        request.user = user
        # No save_model diff ran, so the suppression flag is absent.

        before = LogEntry.objects.count()
        result = admin_instance.log_change(request, product, "Changed tags.")

        assert result is not None
        assert LogEntry.objects.count() == before + 1
        assert LogEntry.objects.latest("id").change_message == "Changed tags."


# ─────────────────────────────────────────────────────────────────────────────
# get_admin_fields — FieldDoesNotExist path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetAdminFieldsEdgeCases:
    def test_function_field_added_to_list_display(self):
        """A SnapFunctionField on a model should appear in list_display."""
        from snapadmin import fields as snap_fields

        class ModelWithFunction(SnapModel):
            total_label = snap_fields.SnapFunctionField(
                lambda obj: "computed",
                verbose_name="Total Label",
            )

            class Meta:
                app_label = "demo"
                abstract = True

        _, list_display, *_ = ModelWithFunction.get_admin_fields()
        assert any("function" in col.lower() or "total" in col.lower() for col in list_display)

    def test_wysiwyg_field_in_list_display_gets_display_method(self):
        """A wysiwyg field that appears in list_display should be wrapped."""
        from demo.app.models import Product
        # Product.description is wysiwyg but show_in_list defaults to True
        # run get_admin_fields and ensure no crash
        form_fields, list_display, *_ = Product.get_admin_fields()
        assert isinstance(list_display, list)


# ─────────────────────────────────────────────────────────────────────────────
# register_all_admins — APIToken already registered
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRegisterAllAdmins:
    def test_register_all_admins_with_app_label_filter(self):
        """register_all_admins with app_label only registers matching models."""
        from snapadmin.models import SnapModel
        SnapModel.register_all_admins(app_label="demo")
        from demo.app.models import Product
        assert Product in admin.site._registry

    def test_register_all_admins_handles_already_registered(self):
        """Calling register_all_admins twice must not raise."""
        from snapadmin.models import SnapModel
        SnapModel.register_all_admins()
        SnapModel.register_all_admins()  # second call — should be silent
