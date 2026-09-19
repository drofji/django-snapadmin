"""
Tests for ``POST /api/models/<app_label>/<Model>/fetch-by/`` (#FETCH2a).

D10 in ``.claude/parallel/DECISIONS.md``: ship it as a small delta on the
export streaming path — a unique/indexed field only, a hard cap on ``values``
before the route exists, and the same streaming/permissions/masking as
``list``/``export``.
"""

import json
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import override_settings
from rest_framework.test import APIClient

from demo.apps.shop.models import Category, ExchangeRate, Product, SearchLog
from snapadmin.models import APIToken


def _ndjson_rows(response):
    return [json.loads(line) for line in response.getvalue().decode().splitlines() if line]


def _client_with_permission(user, codename):
    user.user_permissions.add(Permission.objects.get(codename=codename))
    fresh = get_user_model().objects.get(pk=user.pk)
    token = APIToken.create_for_user(fresh, "t")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
    return client


@pytest.fixture
def rates(db):
    ExchangeRate.objects.all().delete()
    return [
        ExchangeRate.objects.create(code="USD", rate=Decimal("1.0")),
        ExchangeRate.objects.create(code="GBP", rate=Decimal("0.8")),
        ExchangeRate.objects.create(code="JPY", rate=Decimal("150.0")),
    ]


@pytest.fixture
def rates_client(regular_user):
    return _client_with_permission(regular_user, "view_exchangerate")


@pytest.mark.django_db
class TestFetchByHappyPath:
    def test_fetches_exactly_the_matching_rows(self, rates_client, rates):
        r = rates_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD", "JPY"]}, format="json",
        )
        assert r.status_code == 200
        assert r["Content-Type"] == "application/x-ndjson"
        codes = {row["code"] for row in _ndjson_rows(r)}
        assert codes == {"USD", "JPY"}

    def test_content_disposition_names_the_model(self, rates_client, rates):
        r = rates_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD"]}, format="json",
        )
        assert r["Content-Disposition"] == 'attachment; filename="demo_exchangerate_fetch.ndjson"'

    def test_carries_query_backend_header(self, rates_client, rates):
        r = rates_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD"]}, format="json",
        )
        assert r["X-Snap-Query-Backend"] == "database"

    def test_no_match_streams_zero_rows(self, rates_client, rates):
        r = rates_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["XXX"]}, format="json",
        )
        assert r.status_code == 200
        assert _ndjson_rows(r) == []

    def test_pk_field_qualifies_as_unique(self, rates_client, rates):
        r = rates_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "id", "values": [rates[0].pk, rates[1].pk]}, format="json",
        )
        assert r.status_code == 200
        assert {row["id"] for row in _ndjson_rows(r)} == {rates[0].pk, rates[1].pk}

    def test_db_indexed_foreign_key_field_qualifies(self, auth_client):
        # Product.category is db_index=True (Django's FK default) but not unique=True.
        cat_a = Category.objects.create(name="A", slug="a")
        cat_b = Category.objects.create(name="B", slug="b")
        Product.objects.create(name="P1", price=Decimal("1"), category=cat_a)
        Product.objects.create(name="P2", price=Decimal("2"), category=cat_b)
        Product.objects.create(name="P3", price=Decimal("3"), category=cat_b)

        r = auth_client.post(
            "/api/models/demo/Product/fetch-by/",
            {"field": "category", "values": [cat_b.pk]}, format="json",
        )
        assert r.status_code == 200
        names = {row["name"] for row in _ndjson_rows(r)}
        assert names == {"P2", "P3"}


@pytest.mark.django_db
class TestFetchByValidation:
    def test_unknown_field_400(self, auth_client, rates):
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "not_a_field", "values": ["USD"]}, format="json",
        )
        assert r.status_code == 400
        assert "not_a_field" in r.json()["detail"]

    def test_non_indexed_field_400_names_the_constraint(self, auth_client):
        Product.objects.create(name="Laptop", price=Decimal("1"))
        r = auth_client.post(
            "/api/models/demo/Product/fetch-by/",
            {"field": "name", "values": ["Laptop"]}, format="json",
        )
        assert r.status_code == 400
        assert "unique" in r.json()["detail"] and "name" in r.json()["detail"]

    def test_missing_values_400(self, auth_client, rates):
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code"}, format="json",
        )
        assert r.status_code == 400

    def test_empty_values_list_400(self, auth_client, rates):
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": []}, format="json",
        )
        assert r.status_code == 400

    def test_values_not_a_list_400(self, auth_client, rates):
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": "USD"}, format="json",
        )
        assert r.status_code == 400

    @override_settings(SNAPADMIN_FETCH_BY_MAX_VALUES=2)
    def test_over_the_cap_is_400_not_a_truncation(self, auth_client, rates):
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD", "GBP", "JPY"]}, format="json",
        )
        assert r.status_code == 400
        assert "3" in r.json()["detail"] and "2" in r.json()["detail"]

    @override_settings(SNAPADMIN_FETCH_BY_MAX_VALUES=2)
    def test_within_the_cap_succeeds(self, auth_client, rates):
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD", "GBP"]}, format="json",
        )
        assert r.status_code == 200
        assert len(_ndjson_rows(r)) == 2

    def test_default_cap_is_ten_thousand(self, auth_client, rates):
        # Sanity check on the documented default, not an exhaustive boundary test.
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD"] * 10001}, format="json",
        )
        assert r.status_code == 400

    def test_es_only_model_refused(self, auth_client):
        r = auth_client.post(
            "/api/models/demo/SearchLog/fetch-by/",
            {"field": "query", "values": ["x"]}, format="json",
        )
        assert r.status_code == 400
        assert "ES_ONLY" in r.json()["detail"]

    def test_unknown_model_404(self, auth_client):
        r = auth_client.post(
            "/api/models/demo/GhostModel/fetch-by/",
            {"field": "code", "values": ["USD"]}, format="json",
        )
        assert r.status_code == 404

    def test_unauthenticated_denied(self, anon_client):
        r = anon_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD"]}, format="json",
        )
        assert r.status_code in (401, 403)

    def test_get_method_not_allowed(self, auth_client):
        r = auth_client.get("/api/models/demo/ExchangeRate/fetch-by/")
        assert r.status_code == 405


@pytest.mark.django_db
class TestFetchByMasking:
    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.ExchangeRate": ["code"]})
    def test_masked_field_refused_for_unprivileged(self, rates_client, rates):
        r = rates_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD"]}, format="json",
        )
        assert r.status_code == 400
        assert "masked" in r.json()["detail"].lower()

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.ExchangeRate": ["code"]})
    def test_masked_field_allowed_for_privileged(self, auth_client, rates):
        # auth_client is bound to admin_user (superuser) — always PII-privileged.
        r = auth_client.post(
            "/api/models/demo/ExchangeRate/fetch-by/",
            {"field": "code", "values": ["USD"]}, format="json",
        )
        assert r.status_code == 200
        assert {row["code"] for row in _ndjson_rows(r)} == {"USD"}


@pytest.mark.django_db
@override_settings(SNAPADMIN_QUERY_BACKEND_HEADER=False)
def test_the_backend_header_can_be_switched_off_for_fetch_by(rates_client, rates):
    """#QA1d — the header toggle applies to fetch-by as it does to list."""
    r = rates_client.post(
        "/api/models/demo/ExchangeRate/fetch-by/",
        {"field": "code", "values": ["USD"]},
        format="json",
    )
    assert r.status_code == 200
    assert "X-Snap-Query-Backend" not in r


@pytest.mark.django_db
def test_a_masked_model_with_no_search_fields_still_lists(regular_user):
    """#QA1d — masked fields strip themselves out of ``search_fields`` and
    ``ordering``; a model with no searchable field has nothing to strip, and
    ordering by the masked column is still refused as a sort key."""
    from demo.apps.shop.models import Customer, CustomerProfile

    first = CustomerProfile.objects.create(customer=Customer.objects.create(
        first_name="A", last_name="A", email="a@example.com"), tax_id="ZZZ")
    second = CustomerProfile.objects.create(customer=Customer.objects.create(
        first_name="B", last_name="B", email="b@example.com"), tax_id="AAA")
    client = _client_with_permission(regular_user, "view_customerprofile")

    unordered = client.get("/api/models/demo/CustomerProfile/")
    by_masked = client.get("/api/models/demo/CustomerProfile/?ordering=tax_id")
    by_masked_desc = client.get("/api/models/demo/CustomerProfile/?ordering=-tax_id")

    assert by_masked.status_code == 200
    default_ids = [row["id"] for row in unordered.json()["results"]]
    assert sorted(default_ids) == sorted([first.pk, second.pk])
    # The masked column is ignored as a sort key in both directions.
    assert [row["id"] for row in by_masked.json()["results"]] == default_ids
    assert [row["id"] for row in by_masked_desc.json()["results"]] == default_ids


@pytest.mark.django_db
@override_settings(SNAPADMIN_MASKED_FIELDS={"demo.SearchLog": ["query"]})
def test_an_es_only_list_with_a_masked_field_has_no_db_search_fields_to_strip(regular_user):
    """#QA1d — on the ES_ONLY path ES already ran the search, so there are no DB
    ``search_fields`` to strip the masked field from; the listing still works and
    the masked column is still refused as a sort key."""
    from unittest.mock import patch

    from demo.apps.shop.models import SearchLog
    from snapadmin.models import EsQuerySet

    client = _client_with_permission(regular_user, "view_searchlog")
    with patch.object(SearchLog, "es_search", return_value=EsQuerySet(SearchLog, [])) as search:
        r = client.get("/api/models/demo/SearchLog/?ordering=query")

    assert r.status_code == 200
    assert r.json()["results"] == []
    search.assert_called_once()
