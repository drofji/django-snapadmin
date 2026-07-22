"""
snapadmin/reindexing.py

Resumable, progress-tracking bulk reindex of a model's rows into Elasticsearch.

``SnapModel.es_reindex_all`` (in :mod:`snapadmin.models`) is the simple one-shot
path — a single ``helpers.bulk`` over the whole table with no feedback, no
resume, and no load tuning. For a multi-million-row table that means you can't
tell "running" from "hung", a crash restarts from zero, and the index refreshes
on every write. This module reuses the async-export job pattern
(:class:`~snapadmin.models.SnapExportJob`) for reindexing instead:

* **Chunked, pk-cursor paging** — DB-backed models are paged by ``pk__gt=<last
  pk>`` (ordered by primary key), not ``LIMIT/OFFSET``, so a concurrent
  insert/delete can never shift the window and skip a row.
* **Crash-safe resume** — the last indexed pk is checkpointed on the job as
  ``cursor_pk`` after each chunk. ``--resume`` continues from ``pk__gt=cursor_pk``
  rather than restarting the table. Reindexing writes each document under
  ``_id = pk``, so a resumed (or fully restarted) run only ever overwrites,
  never duplicates — resume is a speed optimisation, not a correctness crutch.
* **Single-flight** — :func:`run_reindex_job` claims a job with an atomic
  compare-and-set (``pending``/``failed`` → ``processing``); a second runner that
  finds it already ``processing`` bails out.
* **Cancellable** — the job's status is re-read before each chunk; flipping it to
  ``cancelled`` stops the runner and leaves the partial progress in place.
* **Load tuning** (``--tune``) — before the load the index's ``refresh_interval``
  is set to ``-1`` and ``number_of_replicas`` to ``0``; both are restored to their
  captured values in a ``finally`` when the run ends (or crashes).
* **Parallelism** (``--parallel N``) — each chunk is indexed with
  ``helpers.parallel_bulk`` (``thread_count=N``) instead of ``helpers.bulk``.
  Parallelism is bounded *within* a chunk and the pk cursor only advances once a
  chunk fully completes, so out-of-order completions never corrupt the checkpoint.
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator

from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_CHUNK_SIZE = 500

# A callback invoked with the live SnapReindexJob after each chunk (and once on
# completion) so callers — chiefly the management command — can print progress.
ProgressCallback = Callable[["object"], None]


class _IndexTuner:
    """Relax an index for bulk loading and restore its settings afterwards.

    ``refresh_interval`` is disabled (``-1``) and ``number_of_replicas`` dropped
    to ``0`` for the duration of the load, then both are put back to the values
    captured at :meth:`relax` time. Every ES call is defensive: a cluster that
    rejects the settings change must not fail the reindex itself.
    """

    def __init__(self, es, index_name: str) -> None:
        self._es = es
        self._index = index_name
        self._saved: dict | None = None

    def relax(self) -> None:
        try:
            current = self._es.indices.get_settings(index=self._index)
            idx = current.get(self._index, {}).get("settings", {}).get("index", {})
            self._saved = {
                "refresh_interval": idx.get("refresh_interval"),
                "number_of_replicas": idx.get("number_of_replicas"),
            }
            self._es.indices.put_settings(
                index=self._index,
                body={"index": {"refresh_interval": "-1", "number_of_replicas": 0}},
            )
        except Exception as exc:
            logger.warning("snapadmin.reindex.tune_failed", index=self._index, error=str(exc))
            self._saved = None

    def restore(self) -> None:
        if self._saved is None:
            return
        # A missing captured refresh_interval means the index used the ES default;
        # "1s" is that default, so we put it back explicitly rather than leaving
        # the index stuck at "-1".
        body = {"index": {"refresh_interval": self._saved["refresh_interval"] or "1s"}}
        if self._saved["number_of_replicas"] is not None:
            body["index"]["number_of_replicas"] = self._saved["number_of_replicas"]
        try:
            self._es.indices.put_settings(index=self._index, body=body)
        except Exception as exc:
            logger.warning("snapadmin.reindex.restore_failed", index=self._index, error=str(exc))


def _bulk_index(es, actions, *, parallel: int, chunk_size: int) -> tuple[int, list]:
    """Index ``actions`` and return ``(indexed_count, errors)``.

    Uses ``helpers.parallel_bulk`` when ``parallel > 1``, else ``helpers.bulk``.
    Both run with ``raise_on_error=False`` so a rejected document is reported,
    not raised — the caller records the count and the job still completes.
    """
    from elasticsearch import helpers

    if parallel and parallel > 1:
        indexed, errors = 0, []
        for success, info in helpers.parallel_bulk(
            es, actions, thread_count=parallel, chunk_size=chunk_size, raise_on_error=False
        ):
            if success:
                indexed += 1
            else:
                errors.append(info)
        return indexed, errors
    return helpers.bulk(es, actions, chunk_size=chunk_size, raise_on_error=False)


def _iter_chunks(iterable: Iterable, size: int) -> Iterator[list]:
    """Yield successive ``size``-length lists from ``iterable``."""
    batch: list = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def start_reindex(model, *, resume: bool = False):
    """Create (or, with ``resume``, reuse) a :class:`SnapReindexJob` for ``model``.

    With ``resume=True`` the most recent unfinished-or-failed job for the model is
    reset to ``pending`` and returned, so its ``cursor_pk`` drives a
    continue-from-checkpoint run. When there is no such job (or ``resume`` is
    false) a fresh ``pending`` job is created.
    """
    from snapadmin.models import SnapReindexJob

    app_label = model._meta.app_label
    model_name = model.__name__
    if resume:
        existing = (
            SnapReindexJob.objects
            .filter(app_label=app_label, model=model_name)
            .exclude(status=SnapReindexJob.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            existing.status = SnapReindexJob.Status.PENDING
            existing.error = ""
            existing.save(update_fields=["status", "error"])
            return existing
    return SnapReindexJob.objects.create(app_label=app_label, model=model_name)


def run_reindex_job(
    job,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    parallel: int = 0,
    tune: bool = False,
    limit: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Execute (or resume) the reindex for ``job``.

    Single-flight: the job is claimed with an atomic compare-and-set that only a
    ``pending`` or ``failed`` job wins; an already-``processing`` job (another
    runner holds it) or a finished one is left untouched. Any error is captured
    onto the job as ``failed`` with the message and returned, never raised out.
    Returns a summary dict: ``{"indexed": int, "errors": int}`` on success,
    ``{"skipped": True, ...}`` when not claimed, ``{"cancelled": True, ...}`` when
    cancelled mid-run, or ``{"errors": [...], "indexed": int}`` on failure.

    ``limit`` bounds the run to the first ``N`` rows (a probe / canary run);
    ``None`` (the default) reindexes the whole table.
    """
    from snapadmin.models import SnapReindexJob

    Status = SnapReindexJob.Status
    claimed = (
        SnapReindexJob.objects
        .filter(pk=job.pk, status__in=[Status.PENDING, Status.FAILED])
        .update(status=Status.PROCESSING)
    )
    if not claimed:
        logger.info("snapadmin.reindex.skipped", job=str(job.pk))
        return {"skipped": True, "reason": "already processing or finished"}

    job.refresh_from_db()
    try:
        return _run(
            job, chunk_size=chunk_size, parallel=parallel, tune=tune, limit=limit,
            on_progress=on_progress,
        )
    except Exception as exc:
        logger.exception("snapadmin.reindex.failed", job=str(job.pk))
        job.status = Status.FAILED
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        return {"errors": [str(exc)], "indexed": job.processed_rows}


def _run(job, *, chunk_size, parallel, tune, limit, on_progress) -> dict:
    from snapadmin.models import EsQuerySet, SnapReindexJob

    Status = SnapReindexJob.Status
    model = job.target_model()
    es = model.get_es_client()
    model._ensure_es_index_and_mapping()
    index_name = model.get_es_index_name()
    pk_attname = model._meta.pk.attname

    qs = model.objects.all()
    es_only = isinstance(qs, EsQuerySet)

    if job.started_at is None:
        job.started_at = timezone.now()

    if es_only:
        # ES_ONLY models have no DB table to pk-cursor over; index in one pass.
        # Always restart clean — there is no resume here, so a re-run (e.g. of a
        # failed job via --resume) must reset the counter or it would double-count.
        rows = list(qs)
        if limit is not None:
            rows = rows[:limit]
        job.total_rows = len(rows)
        job.cursor_pk = ""
        job.processed_rows = 0
        job.save(update_fields=["total_rows", "started_at", "cursor_pk", "processed_rows"])
        return _index_es_only(
            job, rows, es, index_name, chunk_size=chunk_size, parallel=parallel, on_progress=on_progress
        )

    qs = qs.order_by("pk")
    # Fetch only the ES-mapped columns (+ pk) where that can be proven safe — a
    # wide table's unmapped TEXT bodies never reach get_es_document() anyway.
    only_fields = model.es_reindex_only_fields()
    if only_fields is not None:
        qs = qs.only(*only_fields)
    total = qs.count()
    job.total_rows = min(total, limit) if limit is not None else total
    resuming = bool(job.cursor_pk)
    if not resuming and job.processed_rows:
        # A stale counter with no cursor to resume from — restart clean so the
        # fresh pass doesn't double-count.
        job.processed_rows = 0
    job.save(update_fields=["total_rows", "started_at", "processed_rows"])

    cursor = job.cursor_pk if resuming else None
    errors_total = 0
    tuner = _IndexTuner(es, index_name) if tune else None
    if tuner:
        tuner.relax()
    try:
        while True:
            job.refresh_from_db(fields=["status"])
            if job.status == Status.CANCELLED:
                logger.info("snapadmin.reindex.cancelled", job=str(job.pk), rows=job.processed_rows)
                return {"cancelled": True, "indexed": job.processed_rows}

            take = chunk_size
            if limit is not None:
                remaining = limit - job.processed_rows
                if remaining <= 0:
                    break
                take = min(chunk_size, remaining)
            chunk_qs = qs.filter(pk__gt=cursor) if cursor is not None else qs
            batch = list(chunk_qs[:take])
            if not batch:
                break

            actions = [
                {"_index": index_name, "_id": obj.pk, "_source": obj.get_es_document()}
                for obj in batch
            ]
            _, errors = _bulk_index(es, actions, parallel=parallel, chunk_size=chunk_size)
            errors_total += len(errors)

            cursor = str(getattr(batch[-1], pk_attname))
            job.cursor_pk = cursor
            job.processed_rows += len(batch)
            job.save(update_fields=["cursor_pk", "processed_rows"])
            if on_progress:
                on_progress(job)
    finally:
        if tuner:
            tuner.restore()

    return _finish(job, errors_total, on_progress)


def _index_es_only(job, rows, es, index_name, *, chunk_size, parallel, on_progress) -> dict:
    from snapadmin.models import SnapReindexJob

    Status = SnapReindexJob.Status
    errors_total = 0
    for batch in _iter_chunks(rows, chunk_size):
        job.refresh_from_db(fields=["status"])
        if job.status == Status.CANCELLED:
            logger.info("snapadmin.reindex.cancelled", job=str(job.pk), rows=job.processed_rows)
            return {"cancelled": True, "indexed": job.processed_rows}
        actions = [
            {"_index": index_name, "_id": obj.pk, "_source": obj.get_es_document()}
            for obj in batch
        ]
        _, errors = _bulk_index(es, actions, parallel=parallel, chunk_size=chunk_size)
        errors_total += len(errors)
        job.processed_rows += len(batch)
        job.save(update_fields=["processed_rows"])
        if on_progress:
            on_progress(job)
    return _finish(job, errors_total, on_progress)


def _finish(job, errors_total: int, on_progress) -> dict:
    from snapadmin.models import SnapReindexJob

    job.status = SnapReindexJob.Status.COMPLETED
    job.finished_at = timezone.now()
    job.error = f"{errors_total} document(s) rejected by Elasticsearch." if errors_total else ""
    job.save(update_fields=["status", "finished_at", "error"])
    if on_progress:
        on_progress(job)
    logger.info(
        "snapadmin.reindex.completed",
        job=str(job.pk),
        rows=job.processed_rows,
        errors=errors_total,
    )
    return {"indexed": job.processed_rows, "errors": errors_total}
