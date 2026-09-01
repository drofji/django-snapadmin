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
