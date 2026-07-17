"""
snapadmin/exporting.py

Asynchronous, fault-tolerant background export of model rows.

Large synchronous exports time out; this module streams a model's rows to a
CSV or JSON file in chunks, tracking progress on a :class:`SnapExportJob` so an
API consumer can poll status / ETA, cancel, and download the result.

Design notes
------------
* **Chunked** — rows are pulled ``SNAPADMIN_EXPORT_CHUNK_SIZE`` at a time
  (default 1000), ordered by primary key for a stable window.
* **Primary-key cursor (no OFFSET drift)** — paging uses ``pk__gt=<last pk>``
  rather than ``LIMIT/OFFSET``. A concurrent insert or delete elsewhere in the
  table can no longer shift the window and silently skip or duplicate a row, the
  way an ``OFFSET`` slice would. The last exported pk is persisted on the job as
  ``cursor_pk``.
* **Crash-safe resume** — each chunk is written to the (local) working file and
  ``fsync``-ed **before** the ``(cursor_pk, cursor_bytes)`` checkpoint is
  persisted, so a crash between the two can only ever leave the file with an
  *extra*, uncheckpointed tail — never a missing one. On resume the working file
  is first truncated back to ``cursor_bytes`` (the byte length confirmed at
  ``cursor_pk``), discarding that unconfirmed tail, and export continues from
  ``pk__gt=cursor_pk``. Re-processing is therefore idempotent: nothing already
  confirmed is repeated and nothing is lost.
* **Single-flight** — :func:`run_export_job` claims a job with an atomic
  compare-and-set (``pending``/``failed`` → ``processing``); a second worker that
  finds the job already ``processing`` bails out immediately, so two workers can
  never interleave writes into the same file. Tradeoff: a worker that crashes
  mid-``processing`` leaves the job stuck in ``processing`` (there is no
  heartbeat/TTL). Such a job needs an operator to reset its status (e.g. to
  ``pending`` via the admin/API) to be retried — at which point the crash-safe
  resume above continues from the last checkpoint rather than restarting.
* **Configurable storage** — the finished file is published through Django's
  storage API (``SNAPADMIN_EXPORT_STORAGE``, defaulting to a local
  ``FileSystemStorage`` rooted at :func:`export_dir`), so the download endpoint
  can serve it even when the web process and the Celery worker run on separate
  filesystems (S3, GCS, shared network storage, …).
* **Cancellable** — before each chunk the job's status is re-read; once it flips
  to ``cancelled`` the writer stops and leaves the partial file in place.
* **PII-aware** — ``SNAPADMIN_MASKED_FIELDS`` values are masked (see
  :mod:`snapadmin.masking`) unless the job's ``requested_by`` holds PII
  access, mirroring the REST serializer so an export can't be used to bypass
  masking a caller sees everywhere else in the API.
"""

from __future__ import annotations

import csv
import io
import json
import os

from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils import timezone
from django.utils.module_loading import import_string

from snapadmin.logging_config import get_logger
from snapadmin.masking import get_masked_fields, mask_value, user_can_view_pii

logger = get_logger(__name__)


def export_enabled() -> bool:
    return bool(getattr(settings, "SNAPADMIN_EXPORT_ENABLED", True))


def export_chunk_size() -> int:
    return max(1, int(getattr(settings, "SNAPADMIN_EXPORT_CHUNK_SIZE", 1000)))


def export_dir() -> str:
    """Directory the (local) working export files are written to, created if missing.

    This is also the location of the default :func:`get_export_storage` backend,
    so with no ``SNAPADMIN_EXPORT_STORAGE`` configured the working file and the
    published file are one and the same — preserving the historical local-disk
    behavior with zero configuration.
    """
    configured = getattr(settings, "SNAPADMIN_EXPORT_DIR", "")
    if not configured:
        base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
        configured = os.path.join(str(base), "snapadmin_exports")
    configured = str(configured)
    os.makedirs(configured, exist_ok=True)
    return configured


def get_export_storage() -> Storage:
    """Return the storage backend export files are published to and served from.

    Defaults to a local :class:`~django.core.files.storage.FileSystemStorage`
    rooted at :func:`export_dir` (today's behavior). Set
    ``SNAPADMIN_EXPORT_STORAGE`` to the dotted path of an alternative
    ``Storage`` subclass (e.g. an S3 / GCS backend) to make the feature
    deployment-topology-agnostic; the class is instantiated with no arguments,
    so it must be configured through its own settings.
    """
    configured = getattr(settings, "SNAPADMIN_EXPORT_STORAGE", "")
    if configured:
        storage_cls = import_string(configured) if isinstance(configured, str) else configured
        return storage_cls()
    return FileSystemStorage(location=export_dir())


def export_file_name(job) -> str:
    """Storage-relative name of the export file for ``job``."""
    return job.file_name or f"export_{job.pk}.{job.export_format}"


def output_path(job) -> str:
    """Absolute path of the local working file for ``job``.

    Retained for callers that read the file directly on the worker's filesystem;
    the download endpoint reads through :func:`get_export_storage` instead.
    """
    return os.path.join(export_dir(), export_file_name(job))


def _working_path(name: str) -> str:
    """Absolute path of the local working file for storage-relative ``name``."""
    return os.path.join(export_dir(), name)


def _export_fields(model) -> list[str]:
    """Concrete, non-relational-reverse field names to include in the export."""
    return [f.name for f in model._meta.fields]


def _publish(storage: Storage, name: str, working_path: str) -> None:
    """Publish the finished working file into ``storage`` under ``name``.

    When the storage already stores files at ``working_path`` (the default local
    ``FileSystemStorage`` rooted at :func:`export_dir`), the working file *is* the
    published file and nothing needs copying. For any other backend the file is
    uploaded, replacing a stale copy from a previous run if present.
    """
    try:
        if os.path.abspath(storage.path(name)) == os.path.abspath(working_path):
            return
    except NotImplementedError:
        pass  # Remote storage (S3, GCS, …) — fall through to upload.
    if storage.exists(name):
        storage.delete(name)
    with open(working_path, "rb") as fh:
        storage.save(name, File(fh))


def run_export_job(job_id) -> None:
    """Execute (or resume) the export for ``job_id``.

    Single-flight: the job is claimed with an atomic compare-and-set that only
    a ``pending`` or ``failed`` job wins; if the job is missing, already
    ``processing`` (another worker holds it), ``completed`` or ``cancelled``,
    this returns without touching the file. Fail-safe: any error is captured
    onto the job as ``failed`` with the message, never raised out of the worker.
    """
    from snapadmin.models import SnapExportJob

    Status = SnapExportJob.Status
    claimed = (
        SnapExportJob.objects
        .filter(pk=job_id, status__in=[Status.PENDING, Status.FAILED])
        .update(status=Status.PROCESSING)
    )
    if not claimed:
        logger.info("snapadmin.export.skipped", job=str(job_id))
        return

    job = SnapExportJob.objects.get(pk=job_id)
    try:
        _run(job)
    except Exception as exc:
        logger.exception("snapadmin.export.failed", job=str(job.pk))
        job.status = Status.FAILED
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])


def _run(job) -> None:
    from snapadmin.models import SnapExportJob

    model = job.target_model()
    fields = _export_fields(model)
    pk_attname = model._meta.pk.attname
    # Fail-closed, like the REST serializer and GraphQL: an export is masked
    # unless the requester (job.requested_by, which survives a worker process
    # boundary better than "the current request") holds PII access. A purged
    # requester (SET_NULL -> None) is treated as unprivileged.
    masked_fields = (
        set()
        if user_can_view_pii(job.requested_by)
        else set(get_masked_fields(model._meta.app_label, model._meta.model_name))
    )
    qs = model.objects.all()
    if job.filters:
        qs = qs.filter(**job.filters)
    qs = qs.order_by("pk")

    job.total_rows = qs.count()
    if not job.file_name:
        job.file_name = f"export_{job.pk}.{job.export_format}"
    if job.started_at is None:
        job.started_at = timezone.now()
    # Status is already PROCESSING (claimed by run_export_job); persist the rest.
    job.save(update_fields=["total_rows", "file_name", "started_at"])

    name = export_file_name(job)
    working_path = _working_path(name)
    chunk = export_chunk_size()
    is_csv = job.export_format == SnapExportJob.Format.CSV
    resuming = bool(job.cursor_pk) and os.path.exists(working_path)

    if resuming:
        # Discard any flushed-but-uncheckpointed tail (crash between fsync and
        # the checkpoint save) so re-processing from cursor_pk cannot duplicate.
        with open(working_path, "r+b") as truncator:
            truncator.truncate(job.cursor_bytes)
    else:
        if os.path.exists(working_path):
            # A stale partial with no cursor to resume from — start clean.
            os.remove(working_path)
        if job.cursor_pk:
            # cursor_pk was set (by this job's own prior attempt) but the local
            # working file it refers to isn't here — a different worker node,
            # or an ephemeral volume that didn't survive a restart. The cursor
            # is meaningless without the file it was checkpointed against:
            # trusting it while opening a fresh file would silently skip every
            # row up to that pk. Clear it and restart the export from scratch,
            # including the progress counter (it will double-count against the
            # rows this fresh pass re-writes otherwise).
            job.cursor_pk = ""
            job.cursor_bytes = 0
            job.processed_rows = 0

    byte_len = job.cursor_bytes if resuming else 0
    cursor = job.cursor_pk if resuming else None
    handle = open(working_path, "ab" if resuming else "wb")
    try:
        if is_csv and not resuming:
            byte_len += _write_bytes(handle, _csv_header_bytes(fields))

        while True:
            # Cancellation checkpoint — re-read just the status.
            job.refresh_from_db(fields=["status"])
            if job.status == SnapExportJob.Status.CANCELLED:
                return

            chunk_qs = qs.filter(pk__gt=cursor) if cursor is not None else qs
            batch = list(chunk_qs[:chunk].values(*fields))
            if not batch:
                break

            if masked_fields:
                for row in batch:
                    for name in masked_fields:
                        if name in row:
                            row[name] = mask_value(row[name])

            byte_len += _write_bytes(handle, _rows_bytes(batch, fields, is_csv))
            cursor = str(batch[-1][pk_attname])

            # Persist the checkpoint *after* the bytes are durable, so a crash
            # can only under-count (a safe, idempotent re-process of the tail).
            job.cursor_pk = cursor
            job.cursor_bytes = byte_len
            job.processed_rows += len(batch)
            job.save(update_fields=["cursor_pk", "cursor_bytes", "processed_rows"])
    finally:
        handle.close()

    _publish(get_export_storage(), name, working_path)
    job.status = SnapExportJob.Status.COMPLETED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at"])
    logger.info("snapadmin.export.completed", job=str(job.pk), rows=job.processed_rows)


def _write_bytes(handle, data: bytes) -> int:
    """Write ``data`` to ``handle``, force it to disk, and return its length."""
    handle.write(data)
    handle.flush()
    os.fsync(handle.fileno())
    return len(data)


def _csv_header_bytes(fields: list[str]) -> bytes:
    buffer = io.StringIO()
    csv.DictWriter(buffer, fieldnames=fields).writeheader()
    return buffer.getvalue().encode("utf-8")


def _rows_bytes(batch: list[dict], fields: list[str], is_csv: bool) -> bytes:
    buffer = io.StringIO()
    if is_csv:
        writer = csv.DictWriter(buffer, fieldnames=fields)
        for row in batch:
            writer.writerow(row)
    else:
        for row in batch:
            buffer.write(json.dumps(row, default=str) + "\n")
    return buffer.getvalue().encode("utf-8")
