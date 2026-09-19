"""SnapAdmin's serializer mixins on a hand-built serializer (F8 / #RM1a).

``PIIMaskingSerializerMixin`` and ``FieldPermissionSerializerMixin`` are public
building blocks: a project writing its own ``ModelSerializer`` for a custom view
mixes them in. ``build_model_serializer`` sets ``_snap_model`` on the classes it
generates; a hand-built class never did, and the mixins then returned the data
untouched — masking and the field-permission gate silently **off**, behind a
``# pragma: no cover`` that called the path unreachable. They now read the
model from ``Meta.model`` as well.
"""
from __future__ import annotations

import pytest
from django.test import RequestFactory, override_settings
from rest_framework import serializers

from snapadmin.api.serializers import FieldPermissionSerializerMixin, PIIMaskingSerializerMixin


def _context(user):
    request = RequestFactory().get("/")
    request.user = user
    return {"request": request}


@pytest.mark.django_db
class TestMaskingOnAHandBuiltSerializer:
    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Customer": ["email"]})
    def test_masked_field_is_masked(self, regular_user):
        from demo.apps.shop.models import Customer

        class CustomerSerializer(PIIMaskingSerializerMixin, serializers.ModelSerializer):
            class Meta:
                model = Customer
                fields = ["first_name", "email"]

        customer = Customer.objects.create(first_name="Alice", last_name="L", email="alice@example.com")

        data = CustomerSerializer(customer, context=_context(regular_user)).data

        assert data["email"] == "a***@example.com"
        assert data["first_name"] == "Alice"

    def test_a_serializer_with_no_model_at_all_passes_data_through(self):
        class Plain(PIIMaskingSerializerMixin, serializers.Serializer):
            value = serializers.CharField()

        assert Plain({"value": "x"}).data == {"value": "x"}


@pytest.mark.django_db
class TestFieldPermissionsOnAHandBuiltSerializer:
    def _serializer(self):
        from demo.apps.shop.models import Customer

        class CustomerSerializer(FieldPermissionSerializerMixin, serializers.ModelSerializer):
            class Meta:
                model = Customer
                fields = ["first_name", "email"]

        return Customer, CustomerSerializer

    def test_a_gated_field_is_absent_on_read(self, regular_user, monkeypatch):
        Customer, CustomerSerializer = self._serializer()
        monkeypatch.setattr(Customer, "api_field_permissions", {"email": {"read": "demo.view_customer_email", "write": "demo.change_customer_email"}},
                            raising=False)
        customer = Customer.objects.create(first_name="Alice", last_name="L", email="alice@example.com")

        data = CustomerSerializer(customer, context=_context(regular_user)).data

        assert "email" not in data
        assert data["first_name"] == "Alice"

    def test_a_gated_field_is_refused_on_write(self, regular_user, monkeypatch):
        Customer, CustomerSerializer = self._serializer()
        monkeypatch.setattr(Customer, "api_field_permissions", {"email": {"read": "demo.view_customer_email", "write": "demo.change_customer_email"}},
                            raising=False)

        serializer = CustomerSerializer(
            data={"first_name": "Bob", "email": "bob@example.com"}, context=_context(regular_user)
        )

        assert serializer.is_valid() is False
        assert "email" in serializer.errors

    def test_a_serializer_with_no_model_at_all_is_left_alone(self):
        class Plain(FieldPermissionSerializerMixin, serializers.Serializer):
            value = serializers.CharField()

        serializer = Plain(data={"value": "x"})
        assert serializer.is_valid() is True
        assert Plain({"value": "x"}).data == {"value": "x"}


@pytest.mark.django_db
@override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Customer": ["email"]})
def test_a_masked_field_the_serializer_does_not_render_is_passed_over(regular_user):
    """#QA1d — masking walks the configured names; one the serializer does not
    output (not in ``Meta.fields``) is skipped, never added back."""
    from demo.apps.shop.models import Customer

    class NameOnly(PIIMaskingSerializerMixin, serializers.ModelSerializer):
        class Meta:
            model = Customer
            fields = ["first_name"]

    customer = Customer.objects.create(first_name="Alice", last_name="L", email="alice@example.com")

    assert NameOnly(customer, context=_context(regular_user)).data == {"first_name": "Alice"}
