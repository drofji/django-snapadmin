"""
tests/test_api_strict_query_params.py

Strict query parameters on the generated REST API (#EXT3a).

A query parameter the API does not understand used to be dropped without a
word: ``?url__iendswith=.pdf`` ran unfiltered and answered ``200`` with every
row, which is the worst possible failure for a client about to export or
delete "the matching rows". With ``SNAPADMIN_API_STRICT_QUERY_PARAMS`` on (the
default), every parameter is either a declared filter, a parameter the API
reads itself, or a ``400`` that names it.

Three properties are pinned here and nowhere else:

* **No silent parameter, on any model** — generated over every registered,
  listable model: each declared filter is accepted, and a garbage lookup or a
  non-existent field is refused (the class guard; a new model or field type
  joins it automatically).
* **No oracle** — a parameter on a field the caller may not see is refused with
  exactly the message a non-existent field gets, whatever its value, so the
  refusal cannot be used to learn anything about the hidden value.
* **No unregistered reader** — every query parameter the viewset reads itself is
  in the reserved set strict mode accepts (an ``ast`` scan), so adding a new one
  cannot quietly start answering ``400``.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from decimal import Decimal

import django_filters
import pytest
from django.apps import apps
from django.contrib.auth.models import Permission
from django.test import override_settings
from rest_framework.test import APIClient

from snapadmin.models import EsStorageMode
from snapadmin.registry import get_model_meta, is_registered

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
NO_SUCH_FIELD = "snapadmin_no_such_field"


def _list_url(model) -> str:
    return f"/api/models/{model._meta.app_label}/{model.__name__}/"


def _sample_value(filter_instance: django_filters.Filter) -> str:
    """A value each generated filter type parses without complaint."""
    if isinstance(filter_instance, django_filters.BooleanFilter):
        return "true"
    if isinstance(filter_instance, django_filters.UUIDFilter):
        return str(uuid.UUID(int=0))
    if isinstance(filter_instance, django_filters.BaseInFilter):
        return "1,2"
    if isinstance(filter_instance, django_filters.DateTimeFilter):
        return "2020-01-01T00:00:00"
    if isinstance(filter_instance, django_filters.DateFilter):
        return "2020-01-01"
    if isinstance(filter_instance, django_filters.NumberFilter):
        return "1"
    return "x"


def _db_backed_registered_models() -> list:
    return [
        model
        for model in apps.get_models()
        if is_registered(model)
        and get_model_meta(model, "es_storage_mode", EsStorageMode.DB_ONLY) != EsStorageMode.ES_ONLY
    ]


def _filters_error(response) -> str:
    assert response.status_code == 400, (response.status_code, response.content[:300])
    body = response.json()
    assert set(body) == {"filters"}, body
    return " ".join(body["filters"])


@pytest.mark.django_db
class TestNoSilentParameterOnAnyModel:
    """The class guard: generated over every registered, DB-backed model."""

    def test_every_declared_filter_is_accepted_and_every_unknown_one_refused(self, auth_client):
        from snapadmin.api.filters import build_filterset_for_model

        failures: list[str] = []
        covered = 0
        for model in _db_backed_registered_models():
            url = _list_url(model)
            if auth_client.get(url).status_code != 200:
                continue
            covered += 1
            declared = build_filterset_for_model(model).base_filters
            for name, filter_instance in declared.items():
                response = auth_client.get(url, {name: _sample_value(filter_instance)})
                if response.status_code != 200:
                    failures.append(
                        f"review: declared filter {model._meta.label}?{name} answered "
                        f"{response.status_code}: {response.content[:200]!r}"
                    )
            garbage = [f"{NO_SUCH_FIELD}"]
            if declared:
                garbage.append(f"{next(iter(declared))}__snapadmin_no_such_lookup")
            for name in garbage:
                response = auth_client.get(url, {name: "x"})
                if response.status_code != 400 or name not in str(response.json()):
                    failures.append(
                        f"review: unknown parameter {model._meta.label}?{name} answered "
                        f"{response.status_code} instead of a 400 naming it — a silent "
                        "parameter returns rows the caller did not ask for"
                    )

        assert covered >= 8, f"only {covered} model(s) were listable — the guard is not looking"
        assert not failures, "\n".join(failures)


@pytest.mark.django_db
class TestUnknownParameterIsRefused:
    def test_the_reported_case_an_undeclared_text_lookup(self, auth_client):
        from demo.apps.shop.models import Customer

        Customer.objects.create(first_name="A", last_name="B", email="a@example.com", origin="status_a")

        response = auth_client.get("/api/models/demo/Customer/", {"email__iendswith": ".com"})

        message = _filters_error(response)
        assert "'email__iendswith'" in message
        assert "Allowed for 'email': email, email__icontains, email__in, email__startswith." in message

    def test_a_non_existent_field_gets_no_field_hint(self, auth_client):
        response = auth_client.get("/api/models/demo/Customer/", {NO_SUCH_FIELD: "x"})

        message = _filters_error(response)
        assert message == f"Unknown or disallowed filter parameter(s): '{NO_SUCH_FIELD}'."

    @pytest.mark.parametrize("endpoint", ["", "count/", "export/"])
    def test_every_collection_endpoint_refuses_it_the_same_way(self, auth_client, endpoint):
        response = auth_client.get(f"/api/models/demo/Product/{endpoint}", {NO_SUCH_FIELD: "x"})

        assert _filters_error(response) == (
            f"Unknown or disallowed filter parameter(s): '{NO_SUCH_FIELD}'."
        )

    def test_fetch_by_refuses_an_unknown_query_parameter(self, auth_client):
        from demo.apps.shop.models import LegacyStockLevel

        LegacyStockLevel.objects.create(sku="SKU-1", warehouse="North")

        response = auth_client.post(
            f"/api/models/demo/LegacyStockLevel/fetch-by/?{NO_SUCH_FIELD}=x",
            {"field": "sku", "values": ["SKU-1"]},
            format="json",
        )

        assert _filters_error(response) == (
            f"Unknown or disallowed filter parameter(s): '{NO_SUCH_FIELD}'."
        )

    def test_a_detail_route_refuses_it_too(self, auth_client):
        from demo.apps.shop.models import Product

        product = Product.objects.create(name="P", price=Decimal("1"))

        response = auth_client.get(f"/api/models/demo/Product/{product.pk}/", {NO_SUCH_FIELD: "x"})

        assert _filters_error(response) == (
            f"Unknown or disallowed filter parameter(s): '{NO_SUCH_FIELD}'."
        )

    def test_a_snap_action_owns_its_query_string(self, auth_client, order):
        response = auth_client.post(
            f"/api/models/demo/Order/{order.pk}/recalculate_total/?{NO_SUCH_FIELD}=x"
        )

        assert response.status_code == 200
        assert response.json() == {"total": "0"}

    def test_several_unknown_parameters_are_named_together_in_sorted_order(self, auth_client):
        response = auth_client.get("/api/models/demo/Product/", {"zzz": "1", "aaa": "2"})

        assert _filters_error(response) == "Unknown or disallowed filter parameter(s): 'aaa', 'zzz'."


@pytest.mark.django_db
class TestReservedParameters:
    @pytest.mark.parametrize(
        "params",
        [{"page": "1"}, {"page_size": "5"}, {"format": "json"}, {"ordering": "-price"}, {"search": "x"}],
    )
    def test_parameters_the_api_reads_itself_are_accepted(self, auth_client, params):
        response = auth_client.get("/api/models/demo/Product/", params)

        assert response.status_code == 200

    def test_limit_is_accepted_by_export(self, auth_client):
        response = auth_client.get("/api/models/demo/Product/export/", {"limit": "1"})

        assert response.status_code == 200

    def test_limit_is_refused_where_nothing_reads_it(self, auth_client):
        response = auth_client.get("/api/models/demo/Product/", {"limit": "1"})

        assert _filters_error(response) == "Unknown or disallowed filter parameter(s): 'limit'."

    def test_format_is_reserved_only_while_drf_reads_it(self, auth_client, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "URL_FORMAT_OVERRIDE": None}

        response = auth_client.get("/api/models/demo/Product/", {"format": "json"})

        assert _filters_error(response) == "Unknown or disallowed filter parameter(s): 'format'."

    @override_settings(SNAPADMIN_API_EXTRA_QUERY_PARAMS=["_", "trace"])
    def test_a_project_can_accept_its_own_extra_parameters(self, auth_client):
        response = auth_client.get("/api/models/demo/Product/", {"_": "1700000000", "trace": "abc"})

        assert response.status_code == 200


@pytest.fixture
def masked_profile_client(db, settings, django_user_model):
    from demo.apps.shop.models import Customer, CustomerProfile
    from snapadmin.models import APIToken

    settings.SNAPADMIN_MASKING_RULES = {
        "demo.CustomerProfile": {
            "bio": {"replacement": "[redacted]", "permission": "demo.unlock_customerprofile_bio"},
        },
    }
    for i in range(3):
        customer = Customer.objects.create(
            first_name=f"M{i}", last_name="S", email=f"m{i}@example.com", origin="status_a"
        )
        CustomerProfile.objects.create(customer=customer, bio=f"raw-bio-{i}", tax_id=f"TAX{i}")
    viewer = django_user_model.objects.create_user("masked_viewer", "v@example.com", "unused")
    viewer.user_permissions.set(
        Permission.objects.filter(codename__in=["view_customerprofile", "view_customer"])
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {APIToken.create_for_user(viewer, 'm').token_key}")
    return client


PROFILE_URL = "/api/models/demo/CustomerProfile/"


class TestNoOracleOnAHiddenField:
    @pytest.mark.parametrize("hidden", ["bio", "bio__icontains", "tax_id", "tax_id_bi"])
    def test_a_hidden_field_is_refused_exactly_like_a_field_that_does_not_exist(
        self, masked_profile_client, hidden
    ):
        hidden_response = masked_profile_client.get(PROFILE_URL, {hidden: "raw-bio-0"})
        absent_response = masked_profile_client.get(PROFILE_URL, {NO_SUCH_FIELD: "raw-bio-0"})

        hidden_message = _filters_error(hidden_response)
        absent_message = _filters_error(absent_response)
        assert hidden_message.replace(repr(hidden), repr(NO_SUCH_FIELD)) == absent_message

    def test_a_correct_guess_and_a_wrong_one_get_byte_identical_answers(self, masked_profile_client):
        right = masked_profile_client.get(PROFILE_URL, {"bio": "raw-bio-1"})
        wrong = masked_profile_client.get(PROFILE_URL, {"bio": "nope"})

        assert right.status_code == wrong.status_code == 400
        assert right.content == wrong.content

    def test_a_masked_foreign_key_cannot_be_filtered_through_its_column(self, db, settings, regular_user):
        from demo.apps.shop.models import Customer, CustomerProfile
        from snapadmin.models import APIToken

        settings.SNAPADMIN_MASKED_FIELDS = {"demo.CustomerProfile": ["customer"]}
        customer = Customer.objects.create(first_name="F", last_name="K", email="fk@example.com", origin="status_a")
        CustomerProfile.objects.create(customer=customer, bio="b")
        regular_user.user_permissions.set(Permission.objects.filter(codename="view_customerprofile"))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {APIToken.create_for_user(regular_user, 'r').token_key}")

        response = client.get(PROFILE_URL, {"customer_id": str(customer.pk)})

        assert _filters_error(response) == "Unknown or disallowed filter parameter(s): 'customer_id'."

    def test_a_read_guard_on_a_name_that_is_no_model_field_is_hidden_as_written(
        self, monkeypatch, regular_user
    ):
        from demo.apps.shop.models import Product

        from snapadmin.api.filters import _hidden_filter_fields

        monkeypatch.setattr(
            Product, "api_field_permissions", {"display_price": {"read": "demo.change_product"}},
            raising=False,
        )

        assert _hidden_filter_fields(regular_user, Product) == {"display_price"}

    def test_the_privileged_caller_still_filters_on_the_field(self, auth_client, masked_profile_client):
        response = auth_client.get(PROFILE_URL, {"bio": "raw-bio-2"})

        assert response.status_code == 200
        assert [row["bio"] for row in response.json()["results"]] == ["raw-bio-2"]


@pytest.mark.django_db
class TestOrdering:
    def test_an_unknown_ordering_field_is_refused(self, auth_client):
        response = auth_client.get("/api/models/demo/Product/", {"ordering": "price,-nonexistent"})

        assert response.status_code == 400
        body = response.json()
        assert set(body) == {"ordering"}
        assert body["ordering"][0].startswith("Unknown ordering field(s): '-nonexistent'.")

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_a_masked_ordering_field_is_refused_like_an_unknown_one(self, db, regular_user):
        from snapadmin.models import APIToken

        regular_user.user_permissions.set(Permission.objects.filter(codename="view_product"))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {APIToken.create_for_user(regular_user, 'o').token_key}")

        masked = client.get("/api/models/demo/Product/", {"ordering": "name"})
        absent = client.get("/api/models/demo/Product/", {"ordering": "nonexistent"})

        assert masked.status_code == absent.status_code == 400
        assert masked.json()["ordering"][0].replace("'name'", "'nonexistent'") == absent.json()["ordering"][0]
        assert "name" not in masked.json()["ordering"][0].split("Order by one of:", 1)[1]


@pytest.mark.django_db
class TestSearch:
    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.LegacyStockLevel": ["sku", "warehouse"]})
    def test_search_with_no_searchable_field_left_is_refused(self, db, regular_user):
        from snapadmin.models import APIToken

        regular_user.user_permissions.set(Permission.objects.filter(codename="view_legacystocklevel"))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {APIToken.create_for_user(regular_user, 's').token_key}")

        response = client.get("/api/models/demo/LegacyStockLevel/", {"search": "anything"})

        assert response.status_code == 400
        assert response.json() == {"search": ["?search= is not available on this model."]}

    def test_search_on_a_model_with_searchable_fields_still_works(self, auth_client):
        from demo.apps.shop.models import LegacyStockLevel

        LegacyStockLevel.objects.create(sku="SKU-SEARCH-1", warehouse="North")
        LegacyStockLevel.objects.create(sku="SKU-OTHER", warehouse="South")

        response = auth_client.get("/api/models/demo/LegacyStockLevel/", {"search": "SEARCH"})

        assert response.status_code == 200
        assert [row["sku"] for row in response.json()["results"]] == ["SKU-SEARCH-1"]


@pytest.mark.django_db
class TestEsOnlyModels:
    def test_a_filter_on_an_es_only_model_is_refused_instead_of_ignored(self, auth_client):
        response = auth_client.get("/api/models/demo/SearchLog/", {"query": "x"})

        message = _filters_error(response)
        assert message == (
            "demo.SearchLog is stored in Elasticsearch only (ES_ONLY) and cannot be filtered by "
            "query parameters: 'query'. Use ?search= instead."
        )


@pytest.mark.django_db
class TestStrictModeOff:
    """``SNAPADMIN_API_STRICT_QUERY_PARAMS = False`` keeps the pre-0.1.0b11 behaviour."""

    @override_settings(SNAPADMIN_API_STRICT_QUERY_PARAMS=False)
    def test_an_unknown_parameter_is_ignored(self, auth_client):
        from demo.apps.shop.models import Product

        Product.objects.create(name="One", price=Decimal("1"))
        Product.objects.create(name="Two", price=Decimal("2"))

        response = auth_client.get("/api/models/demo/Product/", {NO_SUCH_FIELD: "x"})

        assert response.status_code == 200
        assert response.json()["count"] == 2

    @override_settings(SNAPADMIN_API_STRICT_QUERY_PARAMS=False)
    def test_an_unknown_ordering_field_is_ignored(self, auth_client):
        response = auth_client.get("/api/models/demo/Product/", {"ordering": "nonexistent"})

        assert response.status_code == 200


@pytest.fixture
def showcase_without_its_lookup_override(monkeypatch):
    """The demo ``Showcase`` narrows every text field with ``api_default_text_lookups``,
    which (by design) beats the URL default — take that override away for one test."""
    from demo.apps.shop.models import Showcase

    from snapadmin.api import filters

    monkeypatch.setattr(Showcase, "api_default_text_lookups", None)
    monkeypatch.setattr(filters, "_filterset_cache", {})
    return Showcase


@pytest.mark.django_db
class TestUrlFieldLookups:
    def test_a_url_field_can_be_filtered_by_its_ending(
        self, auth_client, showcase_without_its_lookup_override
    ):
        Showcase = showcase_without_its_lookup_override
        Showcase.objects.create(url_field="https://example.com/report.pdf")
        Showcase.objects.create(url_field="https://example.com/page.html")

        response = auth_client.get("/api/models/demo/Showcase/", {"url_field__iendswith": ".PDF"})

        assert response.status_code == 200
        assert [row["url_field"] for row in response.json()["results"]] == [
            "https://example.com/report.pdf"
        ]

    def test_url_fields_get_the_ending_lookups_and_plain_text_fields_do_not(
        self, showcase_without_its_lookup_override
    ):
        from demo.apps.shop.models import Customer

        from snapadmin.api.filters import build_filterset_for_model

        url_filters = set(build_filterset_for_model(showcase_without_its_lookup_override).base_filters)
        text_filters = set(build_filterset_for_model(Customer).base_filters)

        assert {"url_field__endswith", "url_field__iendswith"} <= url_filters
        assert not {"email__endswith", "email__iendswith"} & text_filters

    @override_settings(SNAPADMIN_API_TEXT_LOOKUPS=["exact"])
    def test_a_project_wide_lookup_set_still_wins_over_the_url_default(
        self, showcase_without_its_lookup_override
    ):
        from snapadmin.api.filters import _build_filters_for_model

        names = set(_build_filters_for_model(showcase_without_its_lookup_override))

        assert "url_field" in names
        assert not [name for name in names if name.startswith("url_field__")]

    def test_the_model_wide_default_still_wins_over_the_url_default(self):
        from demo.apps.shop.models import Showcase

        from snapadmin.api.filters import _build_filters_for_model

        names = set(_build_filters_for_model(Showcase))

        assert {"url_field", "url_field__startswith", "url_field__in"} <= names
        assert "url_field__iendswith" not in names


class TestEveryParameterTheViewsetReadsIsReserved:
    """The ast guard: a ``request.query_params.get("<name>")`` added to the
    viewset without registering ``<name>`` would be refused by strict mode on
    every request that sends it."""

    def test_query_params_read_by_the_viewset_are_all_reserved(self):
        from snapadmin.api.filters import _RESERVED_QUERY_PARAMS, _RESERVED_QUERY_PARAMS_BY_ACTION

        source = (REPO_ROOT / "snapadmin" / "api" / "views.py").read_text(encoding="utf-8")
        read_keys = {
            node.args[0].value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in {"query_params", "GET"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        reserved = set(_RESERVED_QUERY_PARAMS).union(*_RESERVED_QUERY_PARAMS_BY_ACTION.values())

        assert read_keys, "the scan found no query_params.get() call — it is not looking"
        assert read_keys <= reserved, (
            f"review: snapadmin/api/views.py reads {sorted(read_keys - reserved)} from the query "
            "string, but strict mode does not know them — add each to _RESERVED_QUERY_PARAMS "
            "(or _RESERVED_QUERY_PARAMS_BY_ACTION for one action) in snapadmin/api/filters.py"
        )
