"""
tests/test_graphql_security.py — GraphQL relation-permission + PII masking (#SEC3)

The dynamically-built GraphQL schema must enforce the same access-control
contract as the REST API on *every* relation it exposes, not just the two
top-level query fields, and it must mask configured PII the same way the REST
serializer does. These two guarantees were previously missing: graphene_django
auto-generates resolvers for every FK/M2M/reverse relation with no permission
check, and it resolved raw model attributes with no masking.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import override_settings

from demo.models import Category, Customer, Order, OrderItem, Product


def _ctx(user, auth=None):
    return SimpleNamespace(user=user, auth=auth)


def _grant(user, *codenames):
    """Add the named permissions and return a cache-cleared copy of the user."""
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return get_user_model().objects.get(pk=user.pk)


# ── Relation permission enforcement ───────────────────────────────────────────

@pytest.mark.django_db
class TestGraphQLRelationPermission:
    def test_fk_relation_denied_without_view_perm(self, order, regular_user):
        # Caller may view Order but NOT the related Customer: the customer
        # relation must be blocked, not resolved with real data.
        from snapadmin.api.graphql import schema

        user = _grant(regular_user, "view_order")
        result = schema.execute(
            f"{{ demoOrder(id: {order.pk}) {{ id customer {{ firstName }} }} }}",
            context_value=_ctx(user),
        )
        assert result.errors
        assert "Permission denied" in str(result.errors[0])
        # The related first name must never leak into the response.
        assert "Alice" not in str(result.data)

    def test_fk_relation_allowed_with_view_perm(self, order, regular_user):
        # With view permission on both models the relation resolves normally —
        # the guard must not break legitimate traversal.
        from snapadmin.api.graphql import schema

        user = _grant(regular_user, "view_order", "view_customer")
        result = schema.execute(
            f"{{ demoOrder(id: {order.pk}) {{ customer {{ firstName }} }} }}",
            context_value=_ctx(user),
        )
        assert result.errors is None
        assert result.data["demoOrder"]["customer"]["firstName"] == "Alice"

    def test_fk_relation_allowed_for_superuser(self, order, admin_user):
        from snapadmin.api.graphql import schema

        result = schema.execute(
            f"{{ demoOrder(id: {order.pk}) {{ customer {{ firstName }} }} }}",
            context_value=_ctx(admin_user),
        )
        assert result.errors is None
        assert result.data["demoOrder"]["customer"]["firstName"] == "Alice"

    def test_reverse_fk_to_many_relation_denied(self, order, regular_user):
        # A to-many relation (reverse FK) is resolved through DjangoListField,
        # which calls get_queryset directly — it must be guarded too.
        from snapadmin.api.graphql import schema

        OrderItem.objects.create(
            order=order,
            product=Product.objects.create(name="Widget", price=Decimal("1.00")),
            quantity=1,
            price=Decimal("1.00"),
        )
        user = _grant(regular_user, "view_order")
        result = schema.execute(
            f"{{ demoOrder(id: {order.pk}) {{ items {{ id quantity }} }} }}",
            context_value=_ctx(user),
        )
        assert result.errors
        assert "Permission denied" in str(result.errors[0])

    def test_reverse_fk_to_many_relation_allowed(self, order, regular_user):
        from snapadmin.api.graphql import schema

        OrderItem.objects.create(
            order=order,
            product=Product.objects.create(name="Widget", price=Decimal("1.00")),
            quantity=7,
            price=Decimal("1.00"),
        )
        user = _grant(regular_user, "view_order", "view_orderitem")
        result = schema.execute(
            f"{{ demoOrder(id: {order.pk}) {{ items {{ quantity }} }} }}",
            context_value=_ctx(user),
        )
        assert result.errors is None
        assert result.data["demoOrder"]["items"][0]["quantity"] == 7

    def test_relation_denied_by_token_scope(self, restricted_token):
        # restricted_token is scoped to demo.Product only — traversing to the
        # related Category must be blocked even though Product itself is allowed.
        from snapadmin.api.graphql import schema

        category = Category.objects.create(name="Gadgets")
        product = Product.objects.create(
            name="Scoped", price=Decimal("2.00"), category=category
        )
        ctx = _ctx(restricted_token.user, auth=restricted_token)
        result = schema.execute(
            f"{{ demoProduct(id: {product.pk}) {{ name category {{ name }} }} }}",
            context_value=ctx,
        )
        assert result.errors
        assert "Permission denied" in str(result.errors[0])
        assert "Gadgets" not in str(result.data)


# ── PII masking ───────────────────────────────────────────────────────────────

MASK = {"demo.Customer": ["email", "first_name"]}


@pytest.mark.django_db
class TestGraphQLMasking:
    def _schema(self):
        # Masked-field resolvers are wired when the schema is built, so rebuild
        # under the overridden setting (mirrors a real deploy, where
        # SNAPADMIN_MASKED_FIELDS is fixed in settings before import).
        from snapadmin.api.graphql import get_dynamic_graphql_schema

        return get_dynamic_graphql_schema()

    @override_settings(SNAPADMIN_MASKED_FIELDS=MASK)
    def test_unprivileged_gets_masked(self, customer, regular_user):
        user = _grant(regular_user, "view_customer")
        result = self._schema().execute(
            f"{{ demoCustomer(id: {customer.pk}) {{ email firstName lastName }} }}",
            context_value=_ctx(user),
        )
        assert result.errors is None
        data = result.data["demoCustomer"]
        assert data["email"] == "a***@example.com"
        assert data["firstName"] == "*****"  # "Alice" (len 5) → fully starred
        assert data["lastName"] == "Smith"    # not masked

    @override_settings(SNAPADMIN_MASKED_FIELDS=MASK)
    def test_superuser_gets_raw(self, customer, admin_user):
        result = self._schema().execute(
            f"{{ demoCustomer(id: {customer.pk}) {{ email firstName }} }}",
            context_value=_ctx(admin_user),
        )
        assert result.errors is None
        data = result.data["demoCustomer"]
        assert data["email"] == "alice@example.com"
        assert data["firstName"] == "Alice"

    @override_settings(SNAPADMIN_MASKED_FIELDS=MASK)
    def test_pii_permission_holder_gets_raw(self, customer, regular_user):
        user = _grant(regular_user, "view_customer", "view_raw_pii")
        result = self._schema().execute(
            f"{{ demoCustomer(id: {customer.pk}) {{ email firstName }} }}",
            context_value=_ctx(user),
        )
        assert result.errors is None
        assert result.data["demoCustomer"]["email"] == "alice@example.com"
        assert result.data["demoCustomer"]["firstName"] == "Alice"

    def test_unconfigured_model_untouched(self, customer, regular_user):
        # No SNAPADMIN_MASKED_FIELDS → no masking resolvers, raw values returned.
        user = _grant(regular_user, "view_customer")
        result = self._schema().execute(
            f"{{ demoCustomer(id: {customer.pk}) {{ email }} }}",
            context_value=_ctx(user),
        )
        assert result.errors is None
        assert result.data["demoCustomer"]["email"] == "alice@example.com"
