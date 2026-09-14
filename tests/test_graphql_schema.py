"""
tests/test_graphql_schema.py

The dynamically built GraphQL schema: what ends up on the ``Query`` type, and
what the generated single/list resolvers return.

Access control over the same schema lives in ``test_graphql_security.py``; this
file is about the schema being built at all and its resolvers answering. (#QA1b:
these tests came from a suite named for the coverage metric, where one of them
asserted ``result.data is not None or result.errors is not None`` — a condition
no outcome can fail — and another never reached the branch it was named after,
because it queried without a context and stopped at the authentication guard.)
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _context(user):
    return SimpleNamespace(user=user, auth=None)


@pytest.mark.django_db
class TestQueryTypeIsPopulated:
    def test_every_registered_model_gets_a_single_and_a_list_field(self):
        from django.apps import apps

        from snapadmin.api.graphql import schema
        from snapadmin.registry import is_registered

        field_names = set(schema.graphql_schema.query_type.fields)

        assert "demoProduct" in field_names
        assert "allDemoProducts" in field_names
        # Not just "some field starts with allDemo": every registered, managed
        # demo model must have both of its fields.
        for model in apps.get_app_config("demo").get_models():
            if not is_registered(model) or not model._meta.managed:
                continue
            singular = f"demo{model.__name__.capitalize()}"
            if singular in field_names:
                assert f"allDemo{model.__name__.capitalize()}s" in field_names


@pytest.mark.django_db
class TestGeneratedResolvers:
    def test_the_list_resolver_returns_the_rows(self, admin_user):
        from demo.apps.shop.models import Product
        from snapadmin.api.graphql import schema

        Product.objects.create(name="GQL List", price=Decimal("3.50"))

        result = schema.execute(
            "{ allDemoProducts { id name } }", context_value=_context(admin_user)
        )

        assert result.errors is None
        assert "GQL List" in [row["name"] for row in result.data["allDemoProducts"]]

    def test_the_single_resolver_returns_the_row_by_id(self, admin_user):
        from demo.apps.shop.models import Product
        from snapadmin.api.graphql import schema

        product = Product.objects.create(name="GQL Single", price=Decimal("4.50"))

        result = schema.execute(
            f"{{ demoProduct(id: {product.pk}) {{ id name }} }}",
            context_value=_context(admin_user),
        )

        assert result.errors is None
        assert result.data["demoProduct"] == {"id": str(product.pk), "name": "GQL Single"}

    def test_an_es_only_model_with_no_matching_row_resolves_to_null(self, admin_user):
        """``demo.SearchLog`` is ES_ONLY, so the single resolver goes through
        ``EsManager.get`` rather than the database.

        With Elasticsearch disabled in the suite there is nothing to find, and
        the documented answer is a null field plus a ``DoesNotExist`` error —
        **with an authenticated context**, or the query never reaches the
        resolver at all and stops at the access guard instead.
        """
        from demo.apps.shop.models import SearchLog
        from snapadmin.api.graphql import schema

        result = schema.execute(
            "{ demoSearchlog(id: 1) { id } }", context_value=_context(admin_user)
        )

        assert result.data == {"demoSearchlog": None}
        assert len(result.errors) == 1
        assert isinstance(result.errors[0].original_error, SearchLog.DoesNotExist)
        # Note: Django raises DoesNotExist with no message here, so the error
        # reaches the client empty. Worth a product decision one day; pinned as
        # the current behaviour rather than guessed at.
        assert result.errors[0].path == ["demoSearchlog"]

    def test_the_list_resolver_on_an_es_only_model_returns_nothing_without_es(
        self, admin_user
    ):
        from snapadmin.api.graphql import schema

        result = schema.execute(
            "{ allDemoSearchlogs { id } }", context_value=_context(admin_user)
        )

        assert result.errors is None
        assert result.data == {"allDemoSearchlogs": []}


class TestModelsThatCannotBeIntrospected:
    def test_a_model_that_fails_to_build_is_skipped_and_the_schema_still_builds(self):
        """One unbuildable model must not take the whole schema down."""
        from snapadmin.api import graphql

        # object() is not usable as a base class, so *every* type(...) call
        # raises and every model takes the skip branch.
        with patch.object(graphql, "DjangoObjectType", object()):
            built = graphql.get_dynamic_graphql_schema()

        # A schema, not an exception — and an empty one, since nothing survived.
        assert list(built.graphql_schema.query_type.fields) == []
