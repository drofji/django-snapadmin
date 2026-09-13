"""
tests/test_es_delete_sync.py

``QuerySet.delete()`` must clear the Elasticsearch mirror too.

``SnapModel.delete()`` has always synced ES, but a bulk ``QuerySet.delete()``
is a single SQL ``DELETE`` that never calls ``Model.delete()`` — so every row
removed that way (and every row removed by a cascade) used to leave its
document behind, and the index kept serving rows that no longer exist. The fix
is a ``post_delete`` receiver, connected at startup for the registered models
that actually mirror to ES, plus the public bulk escape hatch for callers
deleting more rows than one ES round trip per row is worth:
``suppress_es_delete_receiver()`` around the delete, then one
``SnapModel.delete_pks_from_es(pks)`` afterwards.

These tests use a ``MagicMock`` Elasticsearch client throughout — nothing here
needs (or may assume) a live ES server.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.db.models.signals import post_delete
from django.test import override_settings

from demo.apps.shop.models import Customer, ExchangeRate, Product
from snapadmin.models import (
    _clear_es_mirror_on_delete,
    connect_es_delete_receivers,
    suppress_es_delete_receiver,
)


def _product(name="Bulk Delete Me"):
    return Product.objects.create(name=name, price=Decimal("5.00"))


def _receivers_for(model) -> list:
    """The live ``post_delete`` receivers Django would call for ``model``.

    The demo project connects unrelated global ``post_delete`` listeners (the
    django-extra-settings cache invalidator), so "is anything listening?" is
    the wrong question — these tests ask whether *this* receiver is attached.
    """
    sync_receivers, _async_receivers = post_delete._live_receivers(model)
    return list(sync_receivers)


# ─────────────────────────────────────────────────────────────────────────────
# The hole itself: a bulk delete must not leave the document behind
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestQuerySetDeleteClearsTheMirror:
    def test_bulk_delete_removes_every_document(self):
        first, second = _product("One"), _product("Two")
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.objects.filter(pk__in=[first.pk, second.pk]).delete()

        assert not Product.objects.filter(pk__in=[first.pk, second.pk]).exists()
        deleted_ids = {call.kwargs["id"] for call in es.delete.call_args_list}
        assert deleted_ids == {first.pk, second.pk}
        assert all(
            call.kwargs["index"] == Product.get_es_index_name()
            for call in es.delete.call_args_list
        )

    def test_cascade_delete_removes_the_child_document(self):
        """A row deleted by ``on_delete=CASCADE`` never sees ``Model.delete()``
        either — the receiver is what covers it.

        No demo model is both DUAL and a cascade child, so this borrows
        ``OrderItem`` for the duration: mirrored for the length of the test,
        connected by the same public entry point a project would use for a
        model configured after startup, and disconnected again afterwards.
        """
        from demo.apps.shop.models import Order, OrderItem
        from snapadmin.models import EsStorageMode

        customer = Customer.objects.create(
            first_name="C", last_name="D", email="c@d.e", origin="status_a"
        )
        order = Order.objects.create(customer=customer, total=Decimal("9.00"))
        item = OrderItem.objects.create(
            order=order, product=_product("Ordered"), quantity=1, price=Decimal("9.00")
        )

        es = MagicMock()
        with patch.object(OrderItem, "es_storage_mode", EsStorageMode.DUAL):
            connect_es_delete_receivers()
            try:
                with override_settings(ELASTICSEARCH_ENABLED=True), \
                        patch.object(OrderItem, "get_es_client", return_value=es):
                    order.delete()
            finally:
                post_delete.disconnect(
                    sender=OrderItem, dispatch_uid="snapadmin.es_mirror_delete"
                )

        assert not OrderItem.objects.filter(pk=item.pk).exists()
        es.delete.assert_called_once()
        assert es.delete.call_args.kwargs["id"] == item.pk

    def test_instance_delete_still_makes_exactly_one_call(self):
        """``SnapModel.delete()`` already cleared the mirror; the receiver must
        not clear it a second time."""
        product = _product()
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            product.delete()
        es.delete.assert_called_once()

    def test_no_es_call_when_elasticsearch_is_disabled(self):
        product = _product()
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=False), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.objects.filter(pk=product.pk).delete()
        es.delete.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# What must stay untouched
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNonMirroredModelsAreLeftAlone:
    def test_db_only_model_has_no_receiver_connected(self):
        """A DB_ONLY model must keep Django's fast-delete path: attaching this
        receiver to it would disable that optimisation for every bulk delete,
        for no benefit at all."""
        assert _clear_es_mirror_on_delete not in _receivers_for(Customer)

    def test_db_only_bulk_delete_touches_no_es_client(self):
        Customer.objects.create(
            first_name="A", last_name="B", email="a@b.c", origin="status_a"
        )
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Customer, "get_es_client", return_value=es):
            Customer.objects.all().delete()
        es.delete.assert_not_called()

    def test_mirrored_model_has_the_receiver_connected(self):
        """Wired at startup by ``SnapAdminConfig.ready()``, for both flavours of
        mirroring: DUAL storage and a plain ``es_index_enabled`` index."""
        assert _clear_es_mirror_on_delete in _receivers_for(Product)
        assert _clear_es_mirror_on_delete in _receivers_for(ExchangeRate)

    def test_connecting_twice_does_not_duplicate_the_receiver(self):
        before = _receivers_for(Product)
        connect_es_delete_receivers()
        assert _receivers_for(Product) == before


# ─────────────────────────────────────────────────────────────────────────────
# ES being down must never break the database delete
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestElasticsearchFailureIsNonFatal:
    def test_delete_succeeds_and_logs_when_es_is_unreachable(self):
        """Same policy as every other ES write path in the package: log a
        structured warning, never let the store's outage break the caller."""
        product = _product()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", side_effect=RuntimeError("es down")), \
                patch("snapadmin.models.logger") as log:
            Product.objects.filter(pk=product.pk).delete()

        assert not Product.objects.filter(pk=product.pk).exists()
        assert log.warning.call_args.args[0] == "es_delete_document_failed"


# ─────────────────────────────────────────────────────────────────────────────
# The bulk escape hatch, and the paths that use it instead of the receiver
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBulkHelper:
    def test_public_helper_issues_one_bulk_call(self):
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            assert Product.delete_pks_from_es([1, 2, 3]) is True
        es.delete_by_query.assert_called_once()
        es.delete.assert_not_called()
        assert es.delete_by_query.call_args.kwargs["body"] == {
            "query": {"ids": {"values": [1, 2, 3]}}
        }

    def test_private_alias_still_works(self):
        """``_delete_pks_from_es`` predates the public name; keep it working."""
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            assert Product._delete_pks_from_es([4]) is True
        es.delete_by_query.assert_called_once()

    def test_suppressing_the_receiver_leaves_the_bulk_call_to_the_caller(self):
        """The public pairing: suppress the per-row net, delete, clear in bulk."""
        first, second = _product("Bulk A"), _product("Bulk B")
        pks = [first.pk, second.pk]
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            with suppress_es_delete_receiver():
                Product.objects.filter(pk__in=pks).delete()
            es.delete.assert_not_called()
            assert Product.delete_pks_from_es(pks) is True

        es.delete_by_query.assert_called_once()
        # …and the suppression is over: an ordinary delete syncs again.
        third = _product("Bulk C")
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.objects.filter(pk=third.pk).delete()
        es.delete.assert_called_once()

    def test_suppression_is_restored_when_the_block_raises(self):
        product = _product()
        with pytest.raises(RuntimeError):
            with suppress_es_delete_receiver():
                raise RuntimeError("boom")

        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(Product, "get_es_client", return_value=es):
            Product.objects.filter(pk=product.pk).delete()
        es.delete.assert_called_once()

    def test_retention_purge_uses_one_bulk_call_not_one_per_row(self):
        """The purge clears the mirror itself (it has to, to be able to report
        failure), so the per-row receiver must stand down inside it."""
        from django.utils import timezone
        from datetime import timedelta

        rate = ExchangeRate.objects.create(code="XTS", rate=Decimal("1.5"))
        ExchangeRate.objects.filter(pk=rate.pk).update(
            synced_at=timezone.now() - timedelta(days=400)
        )
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(ExchangeRate, "data_retention_days", 30), \
                patch.object(ExchangeRate, "data_retention_field", "synced_at"), \
                patch.object(ExchangeRate, "get_es_client", return_value=es):
            assert ExchangeRate.purge_expired() == 1

        assert not ExchangeRate.objects.filter(pk=rate.pk).exists()
        es.delete_by_query.assert_called_once()
        es.delete.assert_not_called()

    def test_stale_sync_uses_one_bulk_call_not_one_per_row(self):
        from snapadmin.etl import stale_sync

        for code in ("AAA", "BBB", "CCC"):
            ExchangeRate.objects.create(code=code, rate=Decimal("1.0"))
        es = MagicMock()
        with override_settings(ELASTICSEARCH_ENABLED=True), \
                patch.object(ExchangeRate, "get_es_client", return_value=es):
            stale_sync(ExchangeRate, ["AAA"], key_field="code", max_fraction=1.0)

        assert set(ExchangeRate.objects.values_list("code", flat=True)) == {"AAA"}
        es.delete_by_query.assert_called_once()
        es.delete.assert_not_called()
