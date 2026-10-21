"""
tests/test_graphql_search.py — GraphQL ``search`` obeys the same rules as REST ``?search=`` (#EXT3a sweep).

REST answers ``?search=`` with ``400`` when a model has nothing a caller may search
in, and never searches a field hidden from that caller (a match/no-match on it is
an oracle for the hidden value). The GraphQL list field passed ``search`` straight
to ``snap_search``, whose database fallback ignored a term with no field to match
it against — returning **every row** — and searched every admin search field,
masked ones included. The sweep item had been left open.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission


def _ctx(user):
    return SimpleNamespace(user=user, auth=None)


def _grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return get_user_model().objects.get(pk=user.pk)


def _execute(query, user):
    from snapadmin.api.graphql import schema

    return schema.execute(query, context_value=_ctx(user))


@pytest.mark.django_db
class TestGraphQLSearch:
    def test_a_term_on_a_model_with_no_searchable_field_matches_its_key_only(
        self, admin_user, customer, customer_inactive
    ):
        """Exactly REST's ``?search=`` there: the key is the one search field."""
        result = _execute('{ allDemoCustomers(search: "zzz") { id } }', admin_user)

        assert result.errors is None
        assert result.data == {"allDemoCustomers": []}

    def test_a_searchable_field_still_searches(self, admin_user):
        from demo.apps.shop.models import Tag

        Tag.objects.create(name="sale")
        Tag.objects.create(name="new")

        result = _execute('{ allDemoTags(search: "sal") { name } }', admin_user)

        assert result.errors is None
        assert result.data == {"allDemoTags": [{"name": "sale"}]}

    def test_a_masked_search_field_is_not_searched_for_a_caller_without_pii_access(
        self, regular_user, settings
    ):
        from demo.apps.shop.models import Tag

        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Tag": ["name"]}
        Tag.objects.create(name="secret-project")
        user = _grant(regular_user, "view_tag")

        result = _execute('{ allDemoTags(search: "secret") { id } }', user)

        assert result.errors is None
        assert result.data == {"allDemoTags": []}  # the masked name was not searched

    def test_with_pii_access_the_masked_field_is_searched(self, admin_user, settings):
        from demo.apps.shop.models import Tag

        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Tag": ["name"]}
        tag = Tag.objects.create(name="secret-project")

        result = _execute('{ allDemoTags(search: "secret") { id } }', admin_user)

        assert result.data == {"allDemoTags": [{"id": str(tag.pk)}]}

    def test_a_model_with_every_search_field_hidden_is_an_error(self, regular_user, settings, monkeypatch):
        from demo.apps.shop.models import Tag

        from snapadmin.api.views import DynamicModelViewSet

        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Tag": ["name"]}
        monkeypatch.setattr(DynamicModelViewSet, "_db_search_fields", staticmethod(lambda model: ("name",)))
        user = _grant(regular_user, "view_tag")

        result = _execute('{ allDemoTags(search: "x") { id } }', user)

        assert result.data == {"allDemoTags": None}
        assert [error.message for error in result.errors] == ["search is not available on this model."]

    def test_without_a_term_the_list_is_unaffected(self, admin_user, customer):
        result = _execute("{ allDemoCustomers { id } }", admin_user)

        assert result.errors is None
        assert result.data == {"allDemoCustomers": [{"id": str(customer.pk)}]}


@pytest.mark.django_db
class TestSnapSearchDatabaseFallback:
    def test_a_term_with_no_field_to_match_returns_nothing_not_everything(self, customer):
        from demo.apps.shop.models import Customer

        assert list(Customer.snap_search("zzz")) == []

    def test_no_term_returns_every_row(self, customer, customer_inactive):
        from demo.apps.shop.models import Customer

        assert Customer.snap_search(None).count() == 2


@pytest.mark.django_db
class TestTheTermIsSplitLikeRestSplitsIt:
    """DRF splits ``?search=`` on spaces and commas — every word must match some field."""

    @pytest.mark.parametrize("term", ["Laptop Pro", "Pro,Laptop"])
    def test_a_multi_word_term_matches_like_rest(self, admin_user, term):
        from demo.apps.shop.models import Product

        laptop = Product.objects.create(name="Laptop Pro 14", price=1)
        Product.objects.create(name="Laptop Air", price=1)

        result = _execute(f'{{ allDemoProducts(search: "{term}") {{ id }} }}', admin_user)

        assert result.errors is None
        assert result.data == {"allDemoProducts": [{"id": str(laptop.pk)}]}


@pytest.mark.django_db
def test_an_es_only_model_is_searched_where_rest_searches_it(admin_user, monkeypatch):
    """ES_ONLY has no table: the term goes to Elasticsearch, as on REST."""
    from unittest.mock import MagicMock

    from demo.apps.shop.models import SearchLog

    search = MagicMock(return_value=[])
    monkeypatch.setattr(SearchLog, "snap_search", search)

    result = _execute('{ allDemoSearchlogs(search: "laptop", first: 5) { id } }', admin_user)

    assert result.errors is None
    search.assert_called_once_with("laptop", limit=5)


@pytest.mark.django_db
class TestASearchPathIsCheckedWhereItEnds:
    """``^name`` and ``customer__email`` were compared with the hidden names as
    written, so both kept searching a masked field — on REST and on GraphQL."""

    @pytest.fixture
    def viewer(self, regular_user):
        return _grant(regular_user, "view_tag", "view_order", "view_customer")

    @pytest.mark.parametrize("declared", [("^name",), ("=name",), ("name",)])
    def test_a_prefixed_masked_field_is_not_searched(self, viewer, settings, monkeypatch, declared):
        from demo.apps.shop.models import Tag

        from snapadmin.api.views import DynamicModelViewSet

        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Tag": ["name"]}
        monkeypatch.setattr(DynamicModelViewSet, "_db_search_fields", staticmethod(lambda model: declared))
        Tag.objects.create(name="secret")

        graphql = _execute('{ allDemoTags(search: "secret") { id } }', viewer)

        assert [error.message for error in graphql.errors] == ["search is not available on this model."]

    def test_a_masked_field_of_a_related_model_is_not_searched_over_rest(
        self, viewer, settings, monkeypatch, order
    ):
        from rest_framework.test import APIClient

        from snapadmin.api.views import DynamicModelViewSet

        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": ["email"]}
        monkeypatch.setattr(
            DynamicModelViewSet, "_db_search_fields", staticmethod(lambda model: ("customer__email",))
        )
        client = APIClient()
        client.force_authenticate(viewer)

        response = client.get("/api/models/demo/Order/", {"search": order.customer.email[:5]})

        assert response.status_code == 400, response.content
        assert "search" in response.json()


@pytest.mark.django_db
def test_the_path_check_walks_relations_and_ignores_lookups(regular_user, settings):
    from demo.apps.shop.models import Order

    from snapadmin.api.filters import _visible_search_fields

    settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": ["email"]}

    assert _visible_search_fields(
        regular_user,
        Order,
        ("customer__email", "customer__first_name", "total__exact", "id", "customer", "customer__xyz"),
    ) == ("customer__first_name", "total__exact", "id", "customer", "customer__xyz")
