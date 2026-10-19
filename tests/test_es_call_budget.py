"""
tests/test_es_call_budget.py — how many Elasticsearch round-trips a save costs (#EXT3k).

``index_in_es()`` ran ``indices.exists`` + ``put_mapping`` (a cluster-state update
the master serialises) before **every** document, and ``get_es_client()`` built a
new client — a new connection pool, a new TCP/TLS handshake — on every call. An
adopter measured ~44 ms per save against ~1 ms for a plain call. The suite never
saw it: Elasticsearch is a ``MagicMock`` there, which takes any number of calls for
free, and the tests asserted ``put_mapping`` once per *ensure*, never how often a
save ensures.

The class guard is a call budget, the Elasticsearch twin of
``tests/test_query_counts.py``: a counting fake client (#QA2d) and exact numbers.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest
from django.test import override_settings

CALLS: Counter = Counter()


class _CountingIndices:
    def exists(self, **kwargs):
        CALLS["indices.exists"] += 1
        return True

    def create(self, **kwargs):
        CALLS["indices.create"] += 1

    def put_mapping(self, **kwargs):
        CALLS["indices.put_mapping"] += 1


class CountingElasticsearch:
    """Records every call this budget cares about; answers like a healthy cluster."""

    def __init__(self, *args, **kwargs):
        CALLS["client.constructed"] += 1
        self.indices = _CountingIndices()

    def index(self, **kwargs):
        CALLS["index"] += 1

    def delete(self, **kwargs):
        CALLS["delete"] += 1


@pytest.fixture
def counting_es(monkeypatch):
    import elasticsearch

    CALLS.clear()
    monkeypatch.setattr(elasticsearch, "Elasticsearch", CountingElasticsearch)
    with override_settings(ELASTICSEARCH_ENABLED=True):
        yield CALLS


@pytest.mark.django_db
class TestEveryRoundTripASaveCosts:
    def test_three_saves_build_one_client_and_ensure_the_index_once(self, counting_es):
        from demo.apps.shop.models import Product

        product = Product.objects.create(name="Budget", price=Decimal("1.00"))
        product.price = Decimal("2.00")
        product.save()
        product.save()

        assert dict(counting_es) == {
            "client.constructed": 1,
            "indices.exists": 1,
            "indices.put_mapping": 1,
            "index": 3,
        }, (
            "review: an extra Elasticsearch round-trip per save — a client built or the index "
            "ensured again for each document"
        )

    def test_a_failed_index_call_makes_the_next_save_ensure_again(self, counting_es, monkeypatch):
        from demo.apps.shop.models import Product

        product = Product.objects.create(name="Flaky", price=Decimal("1.00"))
        monkeypatch.setattr(CountingElasticsearch, "index", _refuse)
        product.save()
        monkeypatch.undo()
        import elasticsearch

        monkeypatch.setattr(elasticsearch, "Elasticsearch", CountingElasticsearch)
        CALLS.clear()

        product.save()

        assert counting_es["indices.exists"] == 1, "a failure must forget the ensured index"
        assert counting_es["index"] == 1

    def test_a_changed_cluster_setting_builds_a_new_client(self, counting_es):
        from demo.apps.shop.models import Product

        Product.objects.create(name="One", price=Decimal("1.00"))
        with override_settings(ELASTICSEARCH_URL="http://other-cluster:9200"):
            Product.objects.create(name="Two", price=Decimal("1.00"))

        assert counting_es["client.constructed"] == 2
        assert counting_es["indices.exists"] == 2

    def test_deletes_reuse_the_client_too(self, counting_es):
        from demo.apps.shop.models import Product

        rows = [Product.objects.create(name=f"D{i}", price=Decimal("1.00")) for i in range(3)]
        for row in rows:
            row.delete()

        assert counting_es["client.constructed"] == 1
        assert counting_es["delete"] == 3


def _refuse(self, **kwargs):
    raise ConnectionError("cluster unreachable")
