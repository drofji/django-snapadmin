"""
tests/test_pii_masking.py — PII data masking (issue #12)

SNAPADMIN_MASKED_FIELDS + the snapadmin.view_raw_pii permission obfuscate
sensitive fields in the REST API and the admin for anyone who isn't a superuser
or an explicit PII-permission holder.

SNAPADMIN_MASKING_RULES then refines *how* each field is obfuscated (a regex
rewrite, a flat redaction) and *who* may see it raw (a per-field permission),
across every surface that masks: admin, REST, GraphQL, exports and the audit
trail.
"""

from decimal import Decimal

import pytest
from types import SimpleNamespace

from django.contrib.admin import site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import RequestFactory, override_settings

from snapadmin.masking import (
    _has_nested_quantifier,
    apply_masking_rule,
    get_masked_fields,
    get_masking_rules,
    mask_field,
    mask_value,
    user_can_view_pii,
)
from snapadmin.api.serializers import get_serializer_for_model
# Imported at module scope on purpose: snapadmin.api.graphql builds its schema —
# and wires the masked-field resolvers — at import time, so importing it from
# inside an override_settings block would bake that override into the shared
# schema and leak masking into every later GraphQL test.
from snapadmin.api.graphql import _make_masked_resolver

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
        from demo.apps.shop.models import Customer
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
        from demo.apps.shop.models import Product
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


# ── SNAPADMIN_MASKING_RULES: per-field rules (#PROP4b) ───────────────────────

#: Two shapes of rule: a regex rewrite and a flat redaction.
RULES = {
    "demo.Customer": {
        "email": {"pattern": r"[^@]", "replacement": "#"},
        "first_name": {"replacement": "[redacted]"},
    }
}


class TestGetMaskingRules:
    def test_unset(self):
        assert get_masking_rules("demo", "Customer") == {}

    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_configured(self):
        rules = get_masking_rules("demo", "Customer")
        assert set(rules) == {"email", "first_name"}
        assert rules["first_name"] == {"replacement": "[redacted]"}

    @override_settings(SNAPADMIN_MASKING_RULES={"DEMO.customer": {"email": {"replacement": "x"}}})
    def test_case_insensitive_key_match(self):
        assert get_masking_rules("demo", "Customer") == {"email": {"replacement": "x"}}

    @override_settings(SNAPADMIN_MASKING_RULES={"demo.Customer": {"email": "not-a-dict"}})
    def test_malformed_rule_is_dropped(self):
        # Ignored here, so the field falls through to the built-in masker
        # instead of being treated as configured-but-unmaskable.
        assert get_masking_rules("demo", "Customer") == {}

    @override_settings(SNAPADMIN_MASKING_RULES={"other.Model": {"x": {"replacement": "y"}}})
    def test_other_model_untouched(self):
        assert get_masking_rules("demo", "Customer") == {}


class TestRulesImplyMasking:
    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_a_rule_declares_the_field_sensitive(self):
        # No SNAPADMIN_MASKED_FIELDS at all — the rule is the declaration.
        assert sorted(get_masked_fields("demo", "Customer")) == ["email", "first_name"]

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST, SNAPADMIN_MASKING_RULES=RULES)
    def test_union_without_duplicates(self):
        assert get_masked_fields("demo", "Customer") == ["email", "first_name"]

    @override_settings(
        SNAPADMIN_MASKED_FIELDS={"demo.Customer": ["last_name"]},
        SNAPADMIN_MASKING_RULES=RULES,
    )
    def test_declared_order_first_then_rule_only_fields(self):
        assert get_masked_fields("demo", "Customer") == ["last_name", "email", "first_name"]

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_old_setting_alone_is_unchanged(self):
        assert get_masked_fields("demo", "Customer") == ["email", "first_name"]


class TestApplyMaskingRule:
    def test_none_is_never_masked(self):
        assert apply_masking_rule(None, {"replacement": "x"}) is None

    def test_no_rule_falls_back_to_the_builtin_masker(self):
        assert apply_masking_rule("alice@example.com", None) == "a***@example.com"
        assert apply_masking_rule("alice@example.com", "not-a-dict") == "a***@example.com"

    def test_permission_only_rule_uses_the_builtin_masker(self):
        rule = {"permission": "demo.view_customer"}
        assert apply_masking_rule("alice@example.com", rule) == "a***@example.com"

    def test_replacement_without_pattern_redacts_the_whole_value(self):
        assert apply_masking_rule("Alice", {"replacement": "[redacted]"}) == "[redacted]"
        assert apply_masking_rule(42, {"replacement": "[redacted]"}) == "[redacted]"

    def test_pattern_and_replacement(self):
        rule = {"pattern": r"\d(?=\d{4})", "replacement": "*"}
        assert apply_masking_rule("4111111111111111", rule) == "************1111"

    def test_replacement_defaults_to_a_star(self):
        assert apply_masking_rule("abc", {"pattern": "b"}) == "a*c"

    def test_group_references_work(self):
        rule = {"pattern": r"^(\w{2}).*(\w{2})$", "replacement": r"\1…\2"}
        assert apply_masking_rule("SE3550000000054910000003", rule) == "SE…03"

    def test_non_string_value_is_stringified_first(self):
        assert apply_masking_rule(12345, {"pattern": r"\d", "replacement": "*"}) == "*****"

    def test_list_and_dict_recurse(self):
        rule = {"replacement": "x"}
        assert apply_masking_rule(["a", "b"], rule) == ["x", "x"]
        assert apply_masking_rule({"k": "a", "n": None}, rule) == {"k": "x", "n": None}

    def test_invalid_pattern_falls_back_to_the_builtin_masker(self):
        rule = {"pattern": "([a-z]", "replacement": "*"}
        assert apply_masking_rule("alice@example.com", rule) == "a***@example.com"

    def test_catastrophic_pattern_is_rejected(self):
        # (a+)+ against a long non-matching subject is the classic backtracking
        # bomb; it must never reach production data.
        rule = {"pattern": r"(a+)+$", "replacement": "*"}
        bomb = "a" * 28 + "!"
        assert apply_masking_rule(bomb, rule) == mask_value(bomb)

    def test_invalid_replacement_reference_falls_back(self):
        rule = {"pattern": r"(\d)", "replacement": r"\2"}
        assert apply_masking_rule("abc123", rule) == "ab**23"

    def test_oversized_value_skips_the_regex(self):
        from snapadmin.masking import MAX_REGEX_INPUT

        value = "a" * (MAX_REGEX_INPUT + 1)
        masked = apply_masking_rule(value, {"pattern": "a", "replacement": "*"})
        assert masked == "aa" + "*" * (len(value) - 4) + "aa"

    def test_compiled_patterns_are_cached(self):
        from snapadmin.masking import _PATTERN_CACHE, _compiled

        pattern = r"cache-me-\d+"
        _PATTERN_CACHE.pop(pattern, None)
        assert _compiled(pattern) is _compiled(pattern)
        assert pattern in _PATTERN_CACHE


class TestNestedQuantifierDetection:
    @pytest.mark.parametrize("pattern", [r"(a+)+", r"(a*)*", r"([a-z]+)+$", r"(?:a+)+", r"(a(b+))+"])
    def test_risky(self, pattern):
        assert _has_nested_quantifier(pattern) is True

    @pytest.mark.parametrize("pattern", [
        r"\d(?=\d{4})", r"^(\w{4}).*(\w{4})$", r"[+*]{2}", r"(abc)+", r"a+b+",
        r"\(a+\)+", r"[(]a+[)]+", r"((a+))", r"(a|b)+",
    ])
    def test_safe(self, pattern):
        assert _has_nested_quantifier(pattern) is False


@pytest.mark.django_db
class TestMaskField:
    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_applies_the_configured_rule(self):
        assert mask_field("demo", "Customer", "email", "alice@example.com") == "#####@###########"
        assert mask_field("demo", "Customer", "first_name", "Alice") == "[redacted]"

    def test_falls_back_to_the_builtin_masker(self):
        assert mask_field("demo", "Customer", "email", "alice@example.com") == "a***@example.com"

    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_privileged_user_gets_raw(self, admin_user):
        assert mask_field("demo", "Customer", "email", "alice@example.com", admin_user) == "alice@example.com"

    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_unprivileged_user_gets_masked(self, regular_user):
        assert mask_field("demo", "Customer", "email", "alice@example.com", regular_user) == "#####@###########"


# ── Per-field permissions (#PROP4a) ─────────────────────────────────────────

FIELD_PERM_RULES = {
    "demo.Customer": {
        "email": {"replacement": "[redacted]", "permission": "demo.view_customer"},
        "first_name": {"replacement": "[redacted]"},
    }
}


def _with_perm(user, codename, app_label="demo"):
    """Grant one model permission and refetch to clear the cached perm set."""
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label=app_label, codename=codename,
    ))
    return get_user_model().objects.get(pk=user.pk)


@pytest.mark.django_db
class TestPerFieldPii:
    def test_field_argument_is_optional(self, regular_user):
        # The additive signature: every existing call keeps its meaning.
        assert user_can_view_pii(regular_user) is False

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_rule_permission_unlocks_that_field(self, regular_user):
        user = _with_perm(regular_user, "view_customer")
        assert user_can_view_pii(user, "email", app_label="demo", model_name="Customer") is True

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_it_unlocks_only_that_field(self, regular_user):
        user = _with_perm(regular_user, "view_customer")
        assert user_can_view_pii(user, "first_name", app_label="demo", model_name="Customer") is False
        assert user_can_view_pii(user) is False

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_dotted_field_name_resolves_the_model(self, regular_user):
        user = _with_perm(regular_user, "view_customer")
        assert user_can_view_pii(user, "demo.Customer.email") is True

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_unqualified_field_without_a_model_is_masked(self, regular_user):
        user = _with_perm(regular_user, "view_customer")
        assert user_can_view_pii(user, "email") is False

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_without_the_permission_stays_masked(self, regular_user):
        assert user_can_view_pii(regular_user, "demo.Customer.email") is False

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_unknown_field_stays_masked(self, regular_user):
        user = _with_perm(regular_user, "view_customer")
        assert user_can_view_pii(user, "demo.Customer.nope") is False

    def test_anonymous_is_masked_even_with_a_field(self):
        assert user_can_view_pii(AnonymousUser(), "demo.Customer.email") is False

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_superuser_bypasses_the_field_check(self, admin_user):
        assert user_can_view_pii(admin_user, "demo.Customer.first_name") is True

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_mask_field_honours_the_grant(self, regular_user):
        user = _with_perm(regular_user, "view_customer")
        assert mask_field("demo", "Customer", "email", "alice@example.com", user) == "alice@example.com"
        assert mask_field("demo", "Customer", "first_name", "Alice", user) == "[redacted]"


# ── The same rules on every masking surface (#PROP4c) ───────────────────────

@pytest.mark.django_db
class TestRulesAcrossSurfaces:
    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_rest_serializer(self, customer, regular_user):
        ser = get_serializer_for_model("demo", "Customer")
        data = ser(customer, context={"request": SimpleNamespace(user=regular_user)}).data
        assert data["email"] == "#####@###########"
        assert data["first_name"] == "[redacted]"
        assert data["last_name"] == "Smith"

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_rest_serializer_honours_a_field_grant(self, customer, regular_user):
        user = _with_perm(regular_user, "view_customer")
        ser = get_serializer_for_model("demo", "Customer")
        data = ser(customer, context={"request": SimpleNamespace(user=user)}).data
        assert data["email"] == "alice@example.com"   # unlocked by the field permission
        assert data["first_name"] == "[redacted]"     # still masked

    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_graphql_resolver(self, customer, regular_user):
        resolve = _make_masked_resolver("email")
        info = SimpleNamespace(context=SimpleNamespace(user=regular_user))
        assert resolve(customer, info) == "#####@###########"

    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_admin_changelist_column(self, customer, regular_user):
        from demo.apps.shop.models import Customer

        model_admin = site._registry[Customer]
        request = RequestFactory().get("/admin/demo/customer/")
        request.user = regular_user
        rendered = [d(customer) for d in model_admin.get_list_display(request) if callable(d)]
        assert "#####@###########" in rendered

    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_background_export_rows(self, customer, regular_user):
        from demo.apps.shop.models import Customer
        from snapadmin.exporting import _DefaultOrmSource

        job = SimpleNamespace(
            target_model=lambda: Customer, requested_by=regular_user, filters=None,
        )
        batch, _cursor = next(_DefaultOrmSource(job).iter_batches(cursor=None, chunk_size=10))
        assert batch[0]["email"] == "#####@###########"
        assert batch[0]["first_name"] == "[redacted]"

    @override_settings(SNAPADMIN_MASKING_RULES=RULES)
    def test_audit_diff(self):
        from snapadmin.masking import mask_changes

        masked = mask_changes("demo", "Customer", {
            "email": {"old": "old@example.com", "new": "new@example.com"},
            "last_name": {"old": "A", "new": "B"},
        })
        assert masked["email"] == {"old": "###@###########", "new": "###@###########"}
        assert masked["last_name"] == {"old": "A", "new": "B"}

    @override_settings(SNAPADMIN_MASKING_RULES=FIELD_PERM_RULES)
    def test_audit_diff_honours_a_field_grant(self, regular_user):
        from snapadmin.masking import mask_changes

        user = _with_perm(regular_user, "view_customer")
        masked = mask_changes("demo", "Customer", {
            "email": {"old": "old@example.com", "new": "new@example.com"},
            "first_name": {"old": "A", "new": "B"},
        }, user)
        assert masked["email"] == {"old": "old@example.com", "new": "new@example.com"}
        assert masked["first_name"] == {"old": "[redacted]", "new": "[redacted]"}
