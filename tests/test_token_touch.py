"""
tests/test_token_touch.py — an API token's "last used" is written at most once a
minute, not on every request (#EXT3l).

Every token-authenticated request ran ``UPDATE snapadmin_apitoken SET
last_used_at = …``: one more database round-trip per call, and parallel requests
with one token all writing the same row. The query-count pins measured session-
or force-authenticated requests, so the token path's ``UPDATE`` was never counted.

``SNAPADMIN_API_TOKEN_TOUCH_INTERVAL`` (seconds, default 60; ``0`` = every
request) bounds it, and ``APITokenAuthentication.touch_token()`` is the step a
project overrides. The class guard is the query-count pin below.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from snapadmin.models import APIToken


def _token_updates(queries) -> list[str]:
    return [q["sql"] for q in queries if q["sql"].upper().startswith("UPDATE") and "apitoken" in q["sql"].lower()]


@pytest.fixture
def token_client(db, admin_user):
    from demo.apps.shop.models import Product

    Product.objects.create(name="Touch", price=Decimal("1.00"))
    token = APIToken.create_for_user(admin_user, "touch")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
    return client, token


class TestTheTouchInterval:
    def test_the_first_request_records_the_use(self, token_client):
        client, token = token_client

        with CaptureQueriesContext(connection) as queries:
            assert client.get("/api/models/demo/Product/").status_code == 200

        assert len(_token_updates(queries.captured_queries)) == 1
        token.refresh_from_db()
        assert token.last_used_at is not None

    def test_a_second_request_inside_the_interval_writes_nothing(self, token_client):
        client, _token = token_client
        client.get("/api/models/demo/Product/")

        with CaptureQueriesContext(connection) as queries:
            assert client.get("/api/models/demo/Product/").status_code == 200

        assert _token_updates(queries.captured_queries) == [], (
            "review: a token-authenticated request wrote last_used_at inside the touch interval"
        )

    def test_a_use_older_than_the_interval_is_written_again(self, token_client):
        client, token = token_client
        an_hour_ago = timezone.now() - timedelta(hours=1)
        APIToken.objects.filter(pk=token.pk).update(last_used_at=an_hour_ago)

        client.get("/api/models/demo/Product/")

        token.refresh_from_db()
        assert token.last_used_at > an_hour_ago

    @override_settings(SNAPADMIN_API_TOKEN_TOUCH_INTERVAL=0)
    def test_zero_writes_on_every_request(self, token_client):
        client, _token = token_client
        client.get("/api/models/demo/Product/")

        with CaptureQueriesContext(connection) as queries:
            client.get("/api/models/demo/Product/")

        assert len(_token_updates(queries.captured_queries)) == 1

    @override_settings(SNAPADMIN_API_TOKEN_TOUCH_INTERVAL=3600)
    def test_a_concurrent_writer_that_already_touched_it_is_not_overwritten(self, token_client):
        # The UPDATE is conditional: a worker holding a stale copy of the token
        # does not rewrite a value another request already refreshed.
        from snapadmin.api.authentication import APITokenAuthentication

        _client, token = token_client
        stale_copy = APIToken.objects.get(pk=token.pk)
        fresh = timezone.now()
        APIToken.objects.filter(pk=token.pk).update(last_used_at=fresh)

        APITokenAuthentication().touch_token(stale_copy)

        token.refresh_from_db()
        assert token.last_used_at == fresh


class TestTheOverridableStep:
    def test_a_project_can_replace_the_touch_step(self, token_client, monkeypatch):
        from snapadmin.api.authentication import APITokenAuthentication

        client, token = token_client
        seen = []
        monkeypatch.setattr(APITokenAuthentication, "touch_token", lambda self, t: seen.append(t.pk))

        assert client.get("/api/models/demo/Product/").status_code == 200

        assert seen == [token.pk]
        token.refresh_from_db()
        assert token.last_used_at is None
