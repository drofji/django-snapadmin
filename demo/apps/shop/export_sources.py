"""
Custom async-export row sources for the demo, registered via
``SNAPADMIN_EXPORT_SOURCES`` (see demo/core/settings.py).

A source demonstrates SnapAdmin's pluggable export: it owns *what* rows to export
and *how* each row is shaped, while the runner keeps its crash-safe chunking,
progress, cancellation, resume and storage. Set ``SnapExportJob.source`` to the
registered name to use one instead of the built-in ORM source.

``product_catalog`` is the "custom document shape" case — instead of raw column
rows it emits one compact catalogue line per product, proving an export isn't
limited to ``QuerySet.values()``. It still pages by the primary-key cursor, so the
runner's resume works unchanged.
"""

from __future__ import annotations

from typing import Iterator


class ProductCatalogSource:
    """Emit ``{"id", "catalog_line"}`` rows — a custom export document shape."""

    def __init__(self, job) -> None:
        from demo.apps.shop.models import Product

        qs = Product.objects.all()
        if job.filters:
            qs = qs.filter(**job.filters)
        self._qs = qs.order_by("pk")

    def field_names(self) -> list[str]:
        return ["id", "catalog_line"]

    def count(self) -> int:
        return self._qs.count()

    def iter_batches(self, *, cursor: str | None, chunk_size: int) -> Iterator[tuple[list[dict], str]]:
        while True:
            chunk_qs = self._qs.filter(pk__gt=cursor) if cursor is not None else self._qs
            rows = list(chunk_qs[:chunk_size].values("id", "name", "price"))
            if not rows:
                return
            batch = [
                {"id": row["id"], "catalog_line": f'{row["name"]} (${row["price"]})'}
                for row in rows
            ]
            cursor = str(rows[-1]["id"])
            yield batch, cursor


def product_catalog_source(job) -> ProductCatalogSource:
    """Factory referenced by ``SNAPADMIN_EXPORT_SOURCES["product_catalog"]``."""
    return ProductCatalogSource(job)
