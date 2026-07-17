"""
tests/test_pii_masking.py — PII data masking (issue #12)

SNAPADMIN_MASKED_FIELDS + the snapadmin.view_raw_pii permission obfuscate
sensitive fields in the REST API and the admin for anyone who isn't a superuser
or an explicit PII-permission holder.
"""

from decimal import Decimal

import pytest
from types import SimpleNamespace

from django.contrib.admin import site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import RequestFactory, override_settings

from snapadmin.masking import get_masked_fields, mask_value, user_can_view_pii
from snapadmin.api.serializers import get_serializer_for_model

CUST = {"demo.Customer": ["email", "first_name"]}


# ── mask_value() ─────────────────────────────────────────────────────────────

class TestMaskValue:
    def test_none_and_empty_pass_through(self):
        assert mask_value(None) is None
        assert mask_value("") == ""

    def test_email(self):
        assert mask_value("alice@example.com") == "a***@example.com"

    def test_email_without_local_part(self):
        assert mask_value("@example.com") == "***@example.com"

    def test_email_without_domain(self):
        assert mask_value("alice@") == "a***@"

    def test_short_value_fully_masked(self):
        assert mask_value("ab") == "**"
        assert mask_value("x") == "*"

    @pytest.mark.parametrize("s", ["abc", "abcd", "abcde"])
    def test_under_six_chars_fully_masked(self, s):
        # 3-5 char strings used to leak head/tail (e.g. "abc" -> "a*c"); now
        # they're fully starred like 1-2 char strings, so no real character
        # from a short code/PIN survives.
        assert mask_value(s) == "*" * len(s)

    def test_long_value_reveals_two_each_end(self):
        assert mask_value("+33123456778") == "+3********78"

    def test_six_char_boundary_reveals_two_each_end(self):
        assert mask_value("abcdef") == "ab**ef"

    @pytest.mark.parametrize(
        "value",
        [1234567, 12, -7, 3.14159, 0.0, Decimal("1234.56")],
    )
    def test_numeric_values_return_sentinel(self, value):
        # int/float/Decimal must never be coerced to str and star-masked by
        # digit count: that leaks length/magnitude. A fixed sentinel reveals
        # nothing.
        assert mask_value(value) == "***"

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_returns_sentinel(self, value):
        # bool is a subclass of int in Python, so this must be checked first
        # or it silently falls into (and passes) the int branch too - the
        # result is the same sentinel either way, but the ordering matters
        # for correctness if the int branch ever changes.
        assert mask_value(value) == "***"

    def test_list_masks_each_element(self):
        assert mask_value(["alice@example.com", 42, "ab"]) == [
            "a***@example.com",
            "***",
            "**",
        ]

    def test_nested_list_masks_recursively(self):
        assert mask_value([["abcdef", 1], [True]]) == [["ab**ef", "***"], ["***"]]

    def test_dict_masks_values_not_keys(self):
        assert mask_value({"email": "alice@example.com", "age": 30}) == {
            "email": "a***@example.com",
            "age": "***",
        }

    def test_nested_dict_masks_recursively(self):
        assert mask_value({"contact": {"email": "alice@example.com"}, "codes": [1, 2]}) == {
            "contact": {"email": "a***@example.com"},
            "codes": ["***", "***"],
        }

    def test_unrecognized_type_falls_back_to_str_masking(self):
        import datetime

        value = datetime.date(2026, 7, 14)
        assert mask_value(value) == "20******14"


# ── get_masked_fields() ──────────────────────────────────────────────────────

class TestGetMaskedFields:
    def test_unset(self):
        assert get_masked_fields("demo", "Customer") == []

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_configured(self):
        assert get_masked_fields("demo", "Customer") == ["email", "first_name"]

    @override_settings(SNAPADMIN_MASKED_FIELDS={"DEMO.customer": ["email"]})
    def test_case_insensitive_key_match(self):
        assert get_masked_fields("demo", "Customer") == ["email"]


# ── user_can_view_pii() ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUserCanViewPii:
    def test_anonymous_masked(self):
        assert user_can_view_pii(AnonymousUser()) is False

    def test_none_masked(self):
        assert user_can_view_pii(None) is False

    def test_superuser_sees_raw(self, admin_user):
        assert user_can_view_pii(admin_user) is True

    def test_regular_user_masked(self, regular_user):
        assert user_can_view_pii(regular_user) is False

    def test_inactive_user_masked(self, admin_user):
        admin_user.is_active = False
        assert user_can_view_pii(admin_user) is False

    def test_permission_holder_sees_raw(self, regular_user):
        perm = Permission.objects.get(
            content_type__app_label="snapadmin", codename="view_raw_pii"
        )
        regular_user.user_permissions.add(perm)
        # Refetch to clear the cached permission set.
        fresh = get_user_model().objects.get(pk=regular_user.pk)
        assert user_can_view_pii(fresh) is True


# ── API serializer masking ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestApiSerializerMasking:
    def _serialize(self, customer, user):
        ser = get_serializer_for_model("demo", "Customer")
        request = SimpleNamespace(user=user)
        return ser(customer, context={"request": request}).data

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_unprivileged_gets_masked(self, customer, regular_user):
        data = self._serialize(customer, regular_user)
        assert data["email"] == "a***@example.com"
        assert data["first_name"] == "*****"  # "Alice" (len 5) → fully starred
        assert data["last_name"] == "Smith"    # not masked

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_superuser_gets_raw(self, customer, admin_user):
        data = self._serialize(customer, admin_user)
        assert data["email"] == "alice@example.com"
        assert data["first_name"] == "Alice"

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_no_request_context_masks(self, customer):
        # Fail-closed: an internal serialization with no request masks.
        ser = get_serializer_for_model("demo", "Customer")
        data = ser(customer).data
        assert data["email"] == "a***@example.com"

    def test_unconfigured_model_untouched(self, customer, regular_user):
        data = self._serialize(customer, regular_user)
        assert data["email"] == "alice@example.com"


# ── Admin masking ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminMasking:
    def _admin_and_request(self, user):
        from demo.app.models import Customer
        model_admin = site._registry[Customer]
        request = RequestFactory().get("/admin/demo/customer/")
        request.user = user
        return model_admin, request

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_changelist_masks_for_unprivileged(self, customer, regular_user):
        model_admin, request = self._admin_and_request(regular_user)
        display = model_admin.get_list_display(request)
        # A masked field that appears in list_display becomes a callable that
        # returns the obfuscated value.
        callables = [d for d in display if callable(d)]
        assert callables, "expected at least one masked column"
        rendered = [c(customer) for c in callables]
        assert "a***@example.com" in rendered or "A***e" in rendered

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_changelist_raw_for_superuser(self, admin_user):
        model_admin, request = self._admin_and_request(admin_user)
        display = model_admin.get_list_display(request)
        assert all(isinstance(d, str) for d in display)

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_change_form_drops_pii_for_unprivileged(self, regular_user):
        model_admin, request = self._admin_and_request(regular_user)
        fieldsets = model_admin.get_fieldsets(request)
        shown = set()
        for _name, opts in fieldsets:
            for f in opts.get("fields", []):
                shown.update(f if isinstance(f, tuple) else [f])
        assert "email" not in shown
        assert "first_name" not in shown
        assert "last_name" in shown  # non-PII stays

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_change_form_keeps_pii_for_superuser(self, admin_user):
        model_admin, request = self._admin_and_request(admin_user)
        fieldsets = model_admin.get_fieldsets(request)
        shown = set()
        for _name, opts in fieldsets:
            for f in opts.get("fields", []):
                shown.update(f if isinstance(f, tuple) else [f])
        assert "email" in shown

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_change_form_drops_scalar_pii_field(self, regular_user):
        # Product.name is a plain (non-row) form field — exercises the scalar
        # branch of the fieldset filter, distinct from Customer's row tuples.
        from demo.app.models import Product
        model_admin = site._registry[Product]
        request = RequestFactory().get("/admin/demo/product/")
        request.user = regular_user
        fieldsets = model_admin.get_fieldsets(request)
        shown = set()
        for _name, opts in fieldsets:
            for f in opts.get("fields", []):
                shown.update(f if isinstance(f, tuple) else [f])
        assert "name" not in shown
        assert "description" in shown  # non-PII scalar stays
