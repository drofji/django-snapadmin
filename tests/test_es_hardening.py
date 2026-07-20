"""
tests/test_es_hardening.py  –  es_index_settings, targeted multi_match, ES error logging

Alpha-4 ES hardening: index-level settings on the model, full-text queries
restricted to text-capable mapping fields with lenient parsing, and structlog
warnings replacing silently-swallowed ES errors.
"""

import pytest
from unittest.mock import MagicMock, patch

from django.test import override_settings

from demo.apps.shop.models import Customer, Product, SearchLog


# ── es_index_settings ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEsIndexSettings:
    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_settings_sent_on_index_creation(self):
        es = MagicMock()
        es.indices.exists.return_value = False
        index_settings = {"analysis": {"analyzer": {"de": {"type": "german"}}}}
        with patch.object(Product, "get_es_client", return_value=es), \
                patch.object(Product, "es_index_settings", index_settings):
            Product._ensure_es_index_and_mapping()
        body = es.indices.create.call_args.kwargs["body"]
        assert body["settings"] == index_settings
        assert "name" in body["mappings"]["properties"]

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_no_settings_key_when_unset(self):
        es = MagicMock()
        es.indices.exists.return_value = False
        with patch.object(Product, "get_es_client", return_value=es), \
                patch.object(Product, "es_index_settings", None):
            Product._ensure_es_index_and_mapping()
        assert "settings" not in es.indices.create.call_args.kwargs["body"]

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_existing_index_only_updates_mapping(self):
        es = MagicMock()
        es.indices.exists.return_value = True
        with patch.object(Product, "get_es_client", return_value=es), \
                patch.object(Product, "es_index_settings", {"number_of_shards": 3}):
            Product._ensure_es_index_and_mapping()
        es.indices.create.assert_not_called()
        es.indices.put_mapping.assert_called_once()


# ── Targeted multi_match fields + lenient ─────────────────────────────────────

class TestEsSearchFields:
    def test_text_fields_from_mapping(self):
        # Product mapping: name=text, price=float, available=boolean
        assert Product._es_search_fields() == ["name"]

    def test_fallback_to_star_without_mapping(self):
        # Customer declares no es_mapping at all
        assert Customer._es_search_fields() == ["*"]

    def test_fallback_to_star_without_text_fields(self):
        with patch.object(Product, "es_mapping", {"price": {"type": "float"}}):
            assert Product._es_search_fields() == ["*"]


@pytest.mark.django_db
class TestEsSearchQueryShape:
    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_multi_match_targets_text_fields_and_is_lenient(self, product):
        es = MagicMock()
        es.search.return_value = {"hits": {"hits": [{"_source": {"id": product.pk}}]}}
        with patch.object(Product, "get_es_client", return_value=es):
            Product.es_search("laptop")
        mm = es.search.call_args.kwargs["body"]["query"]["multi_match"]
        assert mm["fields"] == ["name"]
        assert mm["lenient"] is True


# ── ES errors are logged instead of silently swallowed ───────────────────────

@pytest.mark.django_db
class TestEsErrorLogging:
    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_ensure_index_failure_logged(self):
        with patch.object(Product, "get_es_client", side_effect=Exception("es down")), \
                patch("snapadmin.models.logger") as log:
            Product._ensure_es_index_and_mapping()
        assert log.warning.call_args.args[0] == "es_ensure_index_failed"

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_index_document_failure_logged(self, product):
        with patch.object(Product, "get_es_client", side_effect=Exception("es down")), \
                patch("snapadmin.models.logger") as log:
            product.index_in_es()
        assert log.warning.call_args.args[0] == "es_index_document_failed"

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_delete_document_failure_logged(self, product):
        with patch.object(Product, "get_es_client", side_effect=Exception("es down")), \
                patch("snapadmin.models.logger") as log:
            product.delete_from_es()
        assert log.warning.call_args.args[0] == "es_delete_document_failed"

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_search_failure_logged_with_db_fallback(self, product):
        with patch.object(Product, "get_es_client", side_effect=Exception("es down")), \
                patch("snapadmin.models.logger") as log:
            Product.es_search("anything")
        assert log.warning.call_args.args[0] == "es_search_failed"
        assert log.warning.call_args.kwargs["fallback"] == "db"

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_search_failure_logged_with_empty_fallback_for_es_only(self):
        with patch.object(SearchLog, "get_es_client", side_effect=Exception("es down")), \
                patch("snapadmin.models.logger") as log:
            SearchLog.es_search("anything")
        assert log.warning.call_args.kwargs["fallback"] == "empty"
