"""
tests/test_es_queryset.py

Coverage for EsQuerySet methods and EsManager.get_queryset().
These tests use mock objects instead of real Elasticsearch.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from snapadmin.models import EsQuerySet, EsManager, EsStorageMode


def _make_hits(*names):
    """Create simple fake hit objects with .name and .pk attributes."""
    return [SimpleNamespace(pk=i + 1, name=n) for i, n in enumerate(names)]


class TestEsQuerySetIteration:
    def test_iter_yields_hits(self):
        hits = _make_hits("Alpha", "Beta")
        qs = EsQuerySet(None, hits)
        result = list(qs)
        assert result == hits

    def test_len_returns_hit_count(self):
        qs = EsQuerySet(None, _make_hits("A", "B", "C"))
        assert len(qs) == 3

    def test_len_empty(self):
        qs = EsQuerySet(None, [])
        assert len(qs) == 0

    def test_getitem_by_index(self):
        hits = _make_hits("X", "Y")
        qs = EsQuerySet(None, hits)
        assert qs[0] is hits[0]
        assert qs[1] is hits[1]

    def test_getitem_slice_returns_esqueryset(self):
        hits = _make_hits("A", "B", "C")
        qs = EsQuerySet(None, hits)
        sliced = qs[0:2]
        assert isinstance(sliced, EsQuerySet)
        assert len(sliced) == 2

    def test_count_returns_hit_count(self):
        qs = EsQuerySet(None, _make_hits("X", "Y"))
        assert qs.count() == 2


class TestEsQuerySetMethods:
    def test_exists_true(self):
        qs = EsQuerySet(None, _make_hits("A"))
        assert qs.exists() is True

    def test_exists_false_empty(self):
        qs = EsQuerySet(None, [])
        assert qs.exists() is False

    def test_ordered_property(self):
        qs = EsQuerySet(None, [])
        assert qs.ordered is True

    def test_all_returns_self(self):
        qs = EsQuerySet(None, _make_hits("A"))
        assert qs.all() is qs

    def test_none_returns_empty_esqueryset(self):
        qs = EsQuerySet(None, _make_hits("A", "B"))
        result = qs.none()
        assert isinstance(result, EsQuerySet)
        assert len(result) == 0

    def test_order_by_returns_self(self):
        qs = EsQuerySet(None, _make_hits("A"))
        assert qs.order_by("-pk") is qs

    def test_select_related_returns_self(self):
        qs = EsQuerySet(None, _make_hits("A"))
        assert qs.select_related("related") is qs

    def test_prefetch_related_returns_self(self):
        qs = EsQuerySet(None, _make_hits("A"))
        assert qs.prefetch_related("m2m") is qs

    def test_using_returns_self(self):
        qs = EsQuerySet(None, _make_hits("A"))
        assert qs.using("default") is qs

    def test_exclude_returns_self(self):
        qs = EsQuerySet(None, _make_hits("A"))
        assert qs.exclude(name="A") is qs


class TestEsQuerySetFilter:
    def test_filter_no_kwargs_returns_self(self):
        qs = EsQuerySet(None, _make_hits("A", "B"))
        assert qs.filter() is qs

    def test_filter_matches_attribute(self):
        hits = [
            SimpleNamespace(pk=1, name="Alpha"),
            SimpleNamespace(pk=2, name="Beta"),
        ]
        qs = EsQuerySet(None, hits)
        result = qs.filter(name="Alpha")
        assert isinstance(result, EsQuerySet)
        assert len(result) == 1
        assert result[0].name == "Alpha"

    def test_filter_no_match_returns_empty(self):
        hits = [SimpleNamespace(pk=1, name="Alpha")]
        qs = EsQuerySet(None, hits)
        result = qs.filter(name="Gamma")
        assert len(result) == 0


class TestEsQuerySetGet:
    @pytest.mark.django_db
    def test_get_without_pk_raises_does_not_exist(self):
        from demo.models import Product
        qs = EsQuerySet(Product, [])
        with pytest.raises(Product.DoesNotExist):
            qs.get()

    @pytest.mark.django_db
    def test_get_with_pk_queries_elasticsearch(self):
        from demo.models import Product
        mock_es = MagicMock()
        mock_es.get.return_value = {"_source": {"id": 99, "name": "Mock Product"}}
        qs = EsQuerySet(Product, [])
        with patch.object(Product, "get_es_client", return_value=mock_es):
            result = qs.get(pk=99)
        assert result.pk == 99

    @pytest.mark.django_db
    def test_get_with_pk_es_error_raises_does_not_exist(self):
        from demo.models import Product
        mock_es = MagicMock()
        mock_es.get.side_effect = Exception("ES unavailable")
        qs = EsQuerySet(Product, [])
        with patch.object(Product, "get_es_client", return_value=mock_es):
            with pytest.raises(Product.DoesNotExist):
                qs.get(pk=99)


class TestEsQuerySetDelete:
    @pytest.mark.django_db
    def test_delete_es_only_calls_es_client(self):
        from demo.models import SearchLog
        hit = SimpleNamespace(pk=42)
        qs = EsQuerySet(SearchLog, [hit])
        mock_es = MagicMock()
        with patch.object(SearchLog, "get_es_client", return_value=mock_es):
            with patch.object(SearchLog, "get_es_index_name", return_value="snap_demo_searchlog"):
                count, detail = qs.delete()
        assert count == 1

    @pytest.mark.django_db
    def test_delete_es_only_es_error_is_swallowed(self):
        from demo.models import SearchLog
        hit = SimpleNamespace(pk=42)
        qs = EsQuerySet(SearchLog, [hit])
        mock_es = MagicMock()
        mock_es.delete.side_effect = Exception("ES error")
        with patch.object(SearchLog, "get_es_client", return_value=mock_es):
            count, _ = qs.delete()
        assert count == 1


class TestEsManagerGetQueryset:
    @pytest.mark.django_db
    def test_db_model_returns_unordered_queryset(self):
        # The base manager must NOT inject a default order_by("-pk"): a default
        # ordering on the manager leaks into GROUP BY on .values().annotate()
        # aggregations and silently returns wrong grouped results. The newest-
        # first default lives in the admin changelist and API list layers.
        from demo.models import Customer
        qs = Customer.objects.get_queryset()
        assert not qs.ordered

    @pytest.mark.django_db
    def test_aggregation_group_by_is_not_polluted_by_pk(self):
        # Regression: with a manager-level default -pk order, this returned one
        # row per pk instead of one row per group. Now it groups correctly.
        from django.db.models import Count
        from demo.models import Customer

        Customer.objects.create(first_name="Alice", email="a@example.com", active=True)
        Customer.objects.create(first_name="Bob", email="b@example.com", active=True)
        Customer.objects.create(first_name="Carol", email="c@example.com", active=False)

        rows = {
            r["active"]: r["n"]
            for r in Customer.objects.values("active").annotate(n=Count("pk"))
        }
        # One row per distinct `active` value with real counts, not per pk.
        assert rows == {True: 2, False: 1}
        query_sql = str(
            Customer.objects.values("active").annotate(n=Count("pk")).query
        )
        assert "ORDER BY" not in query_sql

    @pytest.mark.django_db
    def test_admin_changelist_default_ordering_preserved(self):
        # The newest-first default the manager used to provide now lives on the
        # admin class so the changelist order is unchanged.
        from django.contrib import admin
        from demo.models import Customer

        assert admin.site._registry[Customer].ordering == ["-pk"]

    @pytest.mark.django_db
    def test_es_only_model_non_esqueryset_returns_empty_esqueryset(self):
        from demo.models import SearchLog
        with patch.object(SearchLog, "es_search", return_value=MagicMock(spec=[])):
            qs = SearchLog.objects.get_queryset()
        assert isinstance(qs, EsQuerySet)
        assert len(qs) == 0
