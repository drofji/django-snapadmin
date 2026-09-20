"""
tests/test_fuzz_api.py

Adversarial fuzzing of the request-facing API surfaces (#QA1d, part 4).

``hypothesis`` generates hostile query strings and payloads — unknown
parameters, every lookup suffix Django knows and several it does not, control
characters, astral Unicode, 10 KB values, empty values, duplicated keys — and
each test asserts an **invariant**, not merely "it did not crash":

* the REST list endpoint answers ``200`` or a ``400`` carrying a JSON error,
  never a ``5xx``; ``404`` only for a page number past the end;
* every row it returns is a row the caller could list anyway (a filter
  narrows, it never widens or reaches another table);
* a masked field is never served raw, whatever the query;
* a query parameter on a field the caller cannot see changes **nothing** — the
  same rows come back as without it, so it cannot be used as a match/no-match
  oracle for the hidden value;
* ``POST /api/exports/`` answers ``201`` or ``400`` for any JSON ``filters``,
  and a ``201`` only ever carries allowlisted own-field keys.

Each example runs inside a savepoint that is rolled back, so the examples are
independent of one another and of the order ``pytest-randomly`` picks.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.db import transaction
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rest_framework.test import APIClient

from snapadmin.api.exports import _allowed_filters_for_model

#: The rows are built once per test, before hypothesis starts, and every
#: example only reads them (or writes inside a rolled-back savepoint) — so
#: sharing the function-scoped fixtures across examples is safe, which is the
#: one thing this health check exists to warn about.
REUSES_READ_ONLY_FIXTURES = settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)

HOSTILE_TEXT = st.one_of(
    st.text(max_size=40),
    st.text(alphabet=st.characters(codec="utf-8", categories=["Cc", "Cf", "Zs", "So"]), max_size=20),
    st.sampled_from(["", " ", "0", "-1", "1e309", "NaN", "null", "true", "[]", "{}", "%00",
                     "' OR 1=1 --", "<script>", "../../etc/passwd", "a" * 10_000, "‮", "💥"]),
)
LOOKUPS = st.sampled_from(
    ["", "__exact", "__icontains", "__in", "__gte", "__lte", "__isnull", "__regex", "__iregex",
     "__startswith", "__contains", "__year", "__range", "__search", "__nonsense", "__", "__0"]
)

MASKING_RULES_NOBODY_UNLOCKS = {
    "demo.CustomerProfile": {
        "bio": {"replacement": "[redacted]", "permission": "demo.unlock_customerprofile_bio"},
    },
}


@st.composite
def query_params(draw, field_names: list[str]) -> list[tuple[str, str]]:
    keys = st.one_of(
        st.builds(lambda f, lookup: f + lookup, st.sampled_from(field_names), LOOKUPS),
        st.sampled_from(["search", "ordering", "page_size", "limit", "offset", "fields"]),
        st.text(max_size=20),
    )
    return draw(st.lists(st.tuples(keys, HOSTILE_TEXT), max_size=5))


@pytest.fixture
def masked_profiles(db, settings):
    from demo.apps.shop.models import Customer, CustomerProfile

    settings.SNAPADMIN_MASKING_RULES = MASKING_RULES_NOBODY_UNLOCKS
    for i in range(6):
        customer = Customer.objects.create(
            first_name=f"Fuzz{i}", last_name="Subject", email=f"fuzz{i}@example.com",
            origin="status_a", active=True,
        )
        CustomerProfile.objects.create(
            customer=customer, newsletter=bool(i % 2), bio=f"raw-bio-{i}", tax_id=f"TAX{i}"
        )
    return set(CustomerProfile.objects.values_list("pk", flat=True))


@pytest.fixture
def staff_client(django_user_model):
    from snapadmin.models import APIToken

    staff = django_user_model.objects.create_user(
        "fuzz_staff", "staff@example.com", "unused-password", is_staff=True
    )
    staff.user_permissions.set(
        Permission.objects.filter(codename__in=["view_customerprofile", "view_customer"])
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {APIToken.create_for_user(staff, 'fuzz').token_key}")
    return client


def _ids(response) -> set[int]:
    return {row["id"] for row in response.json()["results"]}


PROFILE_FIELDS = ["id", "newsletter", "bio", "customer", "tax_id", "tax_id_bi"]
LIST_URL = "/api/models/demo/CustomerProfile/"


class TestRestListUnderHostileQueries:
    @REUSES_READ_ONLY_FIXTURES
    @given(params=query_params(PROFILE_FIELDS))
    def test_any_query_is_answered_without_a_server_error_and_never_widens(
        self, masked_profiles, staff_client, params
    ):
        response = staff_client.get(LIST_URL, params)

        if response.status_code == 200:
            body = response.json()
            assert _ids(response) <= masked_profiles
            assert body["count"] >= len(body["results"])
            for row in body["results"]:
                assert row["bio"] == "[redacted]"
                assert not str(row.get("tax_id", "")).startswith("TAX")
        elif response.status_code == 404:
            assert any(key in ("page", "offset") for key, _ in params)
        else:
            assert response.status_code == 400, (params, response.status_code, response.content[:300])
            assert isinstance(response.json(), (dict, list))

    @REUSES_READ_ONLY_FIXTURES
    @given(
        base=query_params(["newsletter", "id"]),
        hidden_field=st.sampled_from(["bio", "tax_id", "tax_id_bi"]),
        lookup=LOOKUPS,
        value=HOSTILE_TEXT,
    )
    def test_a_parameter_on_a_hidden_field_changes_nothing(
        self, masked_profiles, staff_client, base, hidden_field, lookup, value
    ):
        without = staff_client.get(LIST_URL, base)
        with_hidden = staff_client.get(LIST_URL, [*base, (hidden_field + lookup, value)])

        assert with_hidden.status_code == without.status_code
        if without.status_code == 200:
            assert _ids(with_hidden) == _ids(without)
            assert with_hidden.json()["count"] == without.json()["count"]

    @REUSES_READ_ONLY_FIXTURES
    @given(secret_guess=st.sampled_from([f"raw-bio-{i}" for i in range(6)] + ["nope"]))
    def test_a_correct_guess_of_a_hidden_value_is_indistinguishable_from_a_wrong_one(
        self, masked_profiles, staff_client, secret_guess
    ):
        response = staff_client.get(LIST_URL, {"bio": secret_guess})

        assert response.status_code == 200
        assert _ids(response) == masked_profiles


@pytest.fixture
def export_client(db, admin_user):
    from demo.apps.shop.models import Product

    for i in range(3):
        Product.objects.create(name=f"Fuzz{i}", price=Decimal(i))
    client = APIClient()
    client.force_authenticate(admin_user)
    return client


JSON_VALUES = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20)),
    lambda children: st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=8), children, max_size=3),
    max_leaves=6,
)
PRODUCT_KEYS = st.one_of(
    st.builds(
        lambda f, lookup: f + lookup,
        st.sampled_from(["name", "price", "available", "category_id", "category", "tags", "id", "pk", "description"]),
        LOOKUPS,
    ),
    st.text(max_size=20),
)


class TestExportApiUnderHostileFilters:
    @REUSES_READ_ONLY_FIXTURES
    @given(
        filters=st.one_of(
            st.dictionaries(PRODUCT_KEYS, JSON_VALUES, max_size=4),
            JSON_VALUES,
        )
    )
    def test_any_filters_payload_is_a_201_with_allowlisted_keys_or_a_400(
        self, export_client, filters
    ):
        from demo.apps.shop.models import Product

        allowed = _allowed_filters_for_model(Product)
        with transaction.atomic():
            response = export_client.post(
                "/api/exports/",
                json.dumps({"app_label": "demo", "model": "Product", "export_format": "json", "filters": filters}),
                content_type="application/json",
            )
            transaction.set_rollback(True)

        if response.status_code == 201:
            assert isinstance(filters, dict)
            for key in filters:
                field, _, lookup = key.partition("__")
                assert field in allowed and (lookup or "exact") in allowed[field], key
        else:
            assert response.status_code == 400, (filters, response.status_code, response.content[:300])
            assert "filters" in response.json()
