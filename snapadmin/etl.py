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

A recurring full-table sync usually has a *second* half: rows that vanished from
the source must be removed locally. :func:`stale_sync` does that safely — it
deletes rows whose natural key is absent from the latest sync, but refuses if
that would remove more than ``max_fraction`` of the table (the classic footgun
where a truncated or half-downloaded feed wipes almost everything):

    from snapadmin.etl import upsert_from_source, stale_sync

    seen = {row["external_id"] for row in rows}     # keys in this sync
    upsert_from_source(Company, rows, unique_fields=["external_id"])
    stale_sync(Company, seen, key_field="external_id", max_fraction=0.1)
    # {"total": 52310, "stale": 41, "deleted": 41, "fraction": 0.0008, ...}
"""

from __future__ import annotations

from typing import Iterable, Iterator

from django.conf import settings
from django.db import connections, router
from django.db.models import QuerySet

from snapadmin.logging_config import get_logger
from snapadmin.models import EsStorageMode, SnapModel, SnapPurgeError

logger = get_logger(__name__)


class StaleSyncAbort(Exception):
    """Raised by :func:`stale_sync` when the deletion would exceed ``max_fraction``.

    No rows are deleted when this is raised — it is a guard against a truncated
    or partial source sync silently wiping most of the table. Inspect
    ``.total``, ``.stale``, ``.fraction`` and ``.max_fraction`` to decide
    whether to alert, retry the fetch, or deliberately override with a higher
    ``max_fraction``.
    """

    def __init__(
        self, model: type[SnapModel], *, total: int, stale: int,
        fraction: float, max_fraction: float,
    ) -> None:
        self.model = model
        self.total = total
        self.stale = stale
        self.fraction = fraction
        self.max_fraction = max_fraction
        super().__init__(
            f"stale_sync aborted for {model._meta.label}: deleting {stale} of "
            f"{total} row(s) ({fraction:.1%}) would exceed max_fraction "
            f"({max_fraction:.1%}); no rows deleted."
        )


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
            Required on every backend. PostgreSQL/SQLite pass it as an explicit
            ``ON CONFLICT`` target; MySQL/MariaDB upsert against the matching
            unique index automatically (the target is omitted from the SQL to
            avoid ``NotSupportedError``).
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

    # MySQL/MariaDB support `update_conflicts` via ``ON DUPLICATE KEY UPDATE``,
    # which upserts against the table's existing unique/PK constraints and
    # cannot take an explicit conflict target. Passing ``unique_fields`` there
    # raises ``NotSupportedError``. PostgreSQL/SQLite use ``ON CONFLICT`` and
    # *require* the target. Branch on the backend feature so the same call works
    # on every supported database; ``unique_fields`` stays mandatory as the
    # documented conflict target (those columns must carry a unique constraint
    # on every backend — MySQL just infers it from the index).
    connection = connections[router.db_for_write(model)]
    pass_unique_fields = connection.features.supports_update_conflicts_with_target

    for batch in _batched(rows, batch_size):
        if resolved_update_fields is None:
            resolved_update_fields = [
                key for key in batch[0]
                if key not in unique_fields and key not in (pk_name, "pk")
            ]
        create_kwargs = {
            "update_conflicts": True,
            "update_fields": resolved_update_fields,
            "batch_size": batch_size,
        }
        if pass_unique_fields:
            create_kwargs["unique_fields"] = unique_fields
        model.objects.bulk_create(
            [model(**row) for row in batch],
            **create_kwargs,
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


def stale_sync(
    model: type[SnapModel],
    seen_keys: Iterable,
    *,
    key_field: str,
    max_fraction: float = 0.1,
    queryset: QuerySet | None = None,
    dry_run: bool = False,
) -> dict:
    """Delete rows whose natural key vanished from the latest source sync.

    The delete half of a recurring full-table import: after upserting the rows a
    source *does* report, remove the local rows it no longer does. A
    ``max_fraction`` ceiling guards the common footgun where a truncated or
    half-downloaded feed would otherwise wipe most of the table — if the stale
    rows exceed that fraction, nothing is deleted and :class:`StaleSyncAbort` is
    raised instead.

    Args:
        model: Target SnapModel subclass (must have a DB table — not ES_ONLY).
        seen_keys: The natural-key values present in the latest sync. Any local
            row whose ``key_field`` is *not* in this collection is stale.
            Materialised into a set, so pass an iterator freely.
        key_field: The natural-key column to match ``seen_keys`` against —
            normally the same unique field used as ``upsert_from_source``'s
            conflict target.
        max_fraction: Abort (delete nothing, raise :class:`StaleSyncAbort`) if
            the stale rows would exceed this fraction of the candidate rows.
            Must be in ``(0, 1]``; default ``0.1`` (10%). Pass ``1.0`` to allow
            an unbounded delete.
        queryset: Restrict the candidate rows (and the fraction denominator) to
            this queryset — e.g. sync only one source's slice of a shared table
            so rows owned by other sources are never treated as stale. Defaults
            to the whole table.
        dry_run: Compute and return the counts without deleting anything.

    Returns:
        Summary dict: ``total`` (candidate rows), ``stale`` (rows to delete),
        ``deleted`` (rows actually removed — ``0`` on a dry run), ``fraction``
        (``stale / total``) and ``dry_run``. The ``deleted`` count is this
        model's own rows, never the cascade-inflated ``QuerySet.delete()`` total.

    Raises:
        StaleSyncAbort: when the stale fraction exceeds ``max_fraction``.
        SnapPurgeError: for a ``DUAL``/ES-mirrored model whose database rows were
            deleted but whose Elasticsearch mirror could not be cleared (no
            two-phase commit — the DB delete has already happened, mirroring
            :meth:`SnapModel.purge_expired`).
        ValueError: for an empty ``key_field``, an out-of-range ``max_fraction``,
            or an ES_ONLY model.
    """
    if not key_field:
        raise ValueError("stale_sync requires key_field (the natural-key column).")
    if not 0 < max_fraction <= 1:
        raise ValueError(
            f"stale_sync max_fraction must be in (0, 1]; got {max_fraction!r}."
        )

    storage_mode = getattr(model, "es_storage_mode", EsStorageMode.DB_ONLY)
    if storage_mode == EsStorageMode.ES_ONLY:
        raise ValueError(
            "stale_sync targets the database table; ES_ONLY models have none."
        )

    base = model._default_manager.all() if queryset is None else queryset
    seen = seen_keys if isinstance(seen_keys, (set, frozenset)) else set(seen_keys)

    total = base.count()
    result = {"total": total, "stale": 0, "deleted": 0, "fraction": 0.0, "dry_run": dry_run}
    if total == 0:
        return result

    # Diff the existing keys against the sync in Python: a healthy sync leaves
    # few stale keys, so the follow-up delete filters on a small `IN (...)` set
    # rather than a table-sized `NOT IN (seen)`.
    existing_keys = set(base.values_list(key_field, flat=True))
    stale_keys = existing_keys - seen
    stale_qs = base.filter(**{f"{key_field}__in": stale_keys}) if stale_keys else base.none()
    stale = stale_qs.count()
    fraction = stale / total
    result["stale"] = stale
    result["fraction"] = fraction

    if stale and fraction > max_fraction:
        raise StaleSyncAbort(
            model, total=total, stale=stale, fraction=fraction, max_fraction=max_fraction
        )

    if not stale or dry_run:
        logger.info(
            "etl_stale_sync", model=model._meta.label,
            total=total, stale=stale, deleted=0, dry_run=dry_run,
        )
        return result

    # For a mirrored model, capture the pks before the bulk SQL DELETE (which
    # never fires Model.delete()) so the ES mirror can be cleared in one bulk
    # call afterwards — same no-2PC contract as SnapModel.purge_expired().
    mirrors_to_es = (
        storage_mode == EsStorageMode.DUAL or getattr(model, "es_index_enabled", False)
    )
    pks = list(stale_qs.values_list("pk", flat=True)) if mirrors_to_es else []

    _, per_model = stale_qs.delete()
    deleted = per_model.get(model._meta.label, stale)
    result["deleted"] = deleted

    if mirrors_to_es and not model._delete_pks_from_es(pks):
        raise SnapPurgeError(
            f"{model._meta.label}: {deleted} row(s) deleted from the database, "
            "but the Elasticsearch mirror could not be cleared; stale documents "
            "may still be searchable via ES."
        )

    logger.info(
        "etl_stale_sync", model=model._meta.label,
        total=total, stale=stale, deleted=deleted, dry_run=False,
    )
    return result
