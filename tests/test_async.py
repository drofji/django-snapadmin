"""
tests/test_async.py

#PROP1b — the async surface: ``asave``/``adelete``/``arefresh_from_db`` on
``SnapModel`` (native since Django 5.2). ``aget``/``afirst``/``alast`` on
``EsManager``/``EsQuerySet`` are covered in ``tests/test_es_queryset.py``,
next to the rest of that class's tests.

Django's own ``Model.asave()``/``adelete()``/``arefresh_from_db()`` are thin
``sync_to_async()`` wrappers around ``self.save()``/``self.delete()``/
``self.refresh_from_db()`` (see ``django.db.models.Model``). Because Python
resolves ``self.save`` through the instance's actual class, they already
reach ``SnapModel``'s own overrides — the Elasticsearch mirror in
``save()``/``delete()``, and a wysiwyg field's ``pre_save()`` sanitizer,
which Django's own ``Model.save_base()`` invokes regardless of whether the
caller was sync or async — with zero SnapAdmin-specific code. The deliverable
here is proving that stays true, not adding new methods: a future change that
shadowed ``asave()`` with something bypassing ``self.save()`` (e.g. calling
``super().save()`` directly) would silently turn async writes into a hole in
the sanitize-on-write and Elasticsearch-mirror guarantees, with nothing else
in the suite positioned to catch it.

Run via ``asgiref``'s ``async_to_sync`` rather than an async test runner: the
project pins no ``pytest-asyncio`` dependency, and this is the same primitive
Django's own async model/queryset methods use internally.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync

XSS = '<script>alert(1)</script><b>ok</b>'


@pytest.mark.django_db
class TestAsyncSaveReachesSyncSaveOverrides:
    def _product(self, **kwargs):
        from demo.apps.shop.models import Product
        kwargs.setdefault("name", "Async Product")
        kwargs.setdefault("price", Decimal("1.00"))
        return Product(**kwargs)

    def test_asave_dispatches_through_the_instance_s_own_save(self):
        """Pins the mechanism, not just an outcome: asave() must resolve
        self.save at call time (reaching whatever the instance's class
        defines), never a hardcoded reference to Model.save."""
        from demo.apps.shop.models import Product
        product = self._product()
        with patch.object(Product, "save", autospec=True) as mock_save:
            async_to_sync(product.asave)()
        mock_save.assert_called_once()

    def test_asave_mirrors_to_elasticsearch_like_save_does(self):
        """Product is es_storage_mode=DUAL — SnapModel.save()'s own
        index_in_es() call must still run through the async path."""
        from demo.apps.shop.models import Product
        product = self._product()
        with patch.object(Product, "index_in_es") as mock_index:
            async_to_sync(product.asave)()
        mock_index.assert_called_once()

    def test_asave_sanitizes_wysiwyg_fields_like_save_does(self):
        """Sanitize-on-write runs from Field.pre_save(), invoked by the plain
        super().save() call inside SnapModel.save() — reached identically
        whether save() was called directly or through asave()."""
        product = self._product(description=XSS)
        async_to_sync(product.asave)()
        product.refresh_from_db()
        assert "<script" not in product.description
        assert "<b>ok</b>" in product.description

    def test_adelete_mirrors_the_elasticsearch_delete(self):
        """SnapModel.delete()'s delete_from_es() call must still run."""
        from demo.apps.shop.models import Product
        product = self._product()
        product.save()
        with patch.object(Product, "delete_from_es") as mock_delete:
            async_to_sync(product.adelete)()
        mock_delete.assert_called_once()

    def test_arefresh_from_db_reloads_the_instance(self):
        from demo.apps.shop.models import Product
        product = self._product(name="Original")
        product.save()
        Product.objects.filter(pk=product.pk).update(name="Changed elsewhere")
        async_to_sync(product.arefresh_from_db)()
        assert product.name == "Changed elsewhere"
