"""
snapadmin/etl.py

Generic "external source → SnapModel" bulk-upsert helper.

Covers the classic import pipeline (remote MySQL/CSV/API → local table → ES)
without per-row overhead: rows are written with ``bulk_create`` in batches
(``update_conflicts=True`` turns it into an upsert), which bypasses
``SnapModel.save()`` — so **no per-row Elasticsearch writes** happen. When the
model mirrors to ES, one bulk reindex runs at the end instead.

    from snapadmin.etl import upsert_from_source

    rows = stream_from_remote_mysql()          # any iterable of dicts
    summary = upsert_from_source(
        Company, rows,
        unique_fields=["external_id"],         # must carry a unique constraint
        batch_size=1000,
    )
    # {"processed": 52310, "batches": 53, "reindex": {"indexed": 52310}}
"""

from __future__ import annotations

from typing import Iterable, Iterator

from django.conf import settings

from snapadmin.logging_config import get_logger
from snapadmin.models import EsStorageMode, SnapModel

logger = get_logger(__name__)


def _batched(rows: Iterable[dict], size: int) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_from_source(
    model: type[SnapModel],
    rows: Iterable[dict],
    *,
    unique_fields: list[str],
    update_fields: list[str] | None = None,
    batch_size: int = 500,
    reindex: bool = True,
) -> dict:
    """Bulk-upsert an iterable of row dicts into a SnapModel.

    Args:
        model: The target SnapModel subclass (must have a DB table — not ES_ONLY).
        rows: Iterable of dicts, keys = model field names. Streamed; never
            materialised in full, so remote cursors of any size work.
        unique_fields: Conflict target — these fields must carry a unique
            constraint; matching rows are updated instead of duplicated.
        update_fields: Fields to overwrite on conflict. Default: every key of
            the first row except ``unique_fields`` and the primary key.
        batch_size: Rows per ``bulk_create`` round-trip.
        reindex: Bulk-reindex the model into Elasticsearch afterwards (only
            when the model actually mirrors to ES and ES is enabled).

    Returns:
        Summary dict: ``processed``, ``batches``, and ``reindex`` (the
        ``es_reindex_all()`` summary, or ``None`` when skipped).
    """
    if not unique_fields:
        raise ValueError(
            "upsert_from_source requires unique_fields (the conflict target); "
            "these fields must carry a unique constraint."
        )

    storage_mode = getattr(model, "es_storage_mode", EsStorageMode.DB_ONLY)
    if storage_mode == EsStorageMode.ES_ONLY:
        raise ValueError(
            "upsert_from_source targets the database table; "
            "ES_ONLY models have none. Use index_in_es()/es_reindex_all() instead."
        )

    processed = 0
    batches = 0
    resolved_update_fields = list(update_fields) if update_fields else None
    pk_name = model._meta.pk.name

    for batch in _batched(rows, batch_size):
        if resolved_update_fields is None:
            resolved_update_fields = [
                key for key in batch[0]
                if key not in unique_fields and key not in (pk_name, "pk")
            ]
        model.objects.bulk_create(
            [model(**row) for row in batch],
            update_conflicts=True,
            unique_fields=unique_fields,
            update_fields=resolved_update_fields,
            batch_size=batch_size,
        )
        processed += len(batch)
        batches += 1

    logger.info(
        "etl_upsert_finished",
        model=f"{model._meta.app_label}.{model.__name__}",
        processed=processed,
        batches=batches,
    )

    reindex_summary = None
    mirrors_to_es = (
        storage_mode == EsStorageMode.DUAL
        or getattr(model, "es_index_enabled", False)
    )
    if reindex and processed and mirrors_to_es and getattr(settings, "ELASTICSEARCH_ENABLED", False):
        reindex_summary = model.es_reindex_all(chunk_size=batch_size)

    return {"processed": processed, "batches": batches, "reindex": reindex_summary}
