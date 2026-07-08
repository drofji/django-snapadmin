"""
snapadmin/exporting.py

Asynchronous, fault-tolerant background export of model rows.

Large synchronous exports time out; this module streams a model's rows to a
CSV or JSON file in chunks, tracking progress on a :class:`SnapExportJob` so an
API consumer can poll status / ETA, cancel, and download the result.

Design notes
------------
* **Chunked** — rows are pulled ``SNAPADMIN_EXPORT_CHUNK_SIZE`` at a time
  (default 1000), ordered by pk for a stable window.
* **Resumable** — after each chunk ``processed_rows`` is persisted and the file
  is appended to. If the worker dies and the task re-runs, it reopens the file
  in append mode and continues from ``processed_rows`` — no work is repeated and
  nothing already written is lost.
* **Cancellable** — before each chunk the job's status is re-read; once it flips
  to ``cancelled`` the writer stops and leaves the partial file in place.
"""

from __future__ import annotations

import csv
import json
import os

from django.conf import settings
from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)


def export_enabled() -> bool:
    return bool(getattr(settings, "SNAPADMIN_EXPORT_ENABLED", True))


def export_chunk_size() -> int:
    return max(1, int(getattr(settings, "SNAPADMIN_EXPORT_CHUNK_SIZE", 1000)))


def export_dir() -> str:
    """Directory the export files are written to (created if missing)."""
    configured = getattr(settings, "SNAPADMIN_EXPORT_DIR", "")
    if not configured:
        base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
        configured = os.path.join(str(base), "snapadmin_exports")
    os.makedirs(configured, exist_ok=True)
    return configured


def output_path(job) -> str:
    """Absolute path of the file for ``job`` (``export_<id>.<ext>``)."""
    return os.path.join(export_dir(), job.file_name or f"export_{job.pk}.{job.export_format}")


def _export_fields(model) -> list[str]:
    """Concrete, non-relational-reverse field names to include in the export."""
    return [f.name for f in model._meta.fields]


def run_export_job(job_id) -> None:
    """Execute (or resume) the export for ``job_id``.

    Fail-safe on the job: any error is captured onto the job as ``failed`` with
    the message, never raised out of the worker.
    """
    from snapadmin.models import SnapExportJob

    job = SnapExportJob.objects.get(pk=job_id)
    if job.status == SnapExportJob.Status.CANCELLED:
        return
    try:
        _run(job)
    except Exception as exc:  # pragma: no cover - defensive; exercised via monkeypatch
        logger.exception("snapadmin.export.failed", job=str(job.pk))
        job.status = SnapExportJob.Status.FAILED
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])


def _run(job) -> None:
    from snapadmin.models import SnapExportJob

    model = job.target_model()
    fields = _export_fields(model)
    qs = model.objects.all()
    if job.filters:
        qs = qs.filter(**job.filters)
    qs = qs.order_by("pk")

    job.total_rows = qs.count()
    if not job.file_name:
        job.file_name = f"export_{job.pk}.{job.export_format}"
    job.status = SnapExportJob.Status.PROCESSING
    if job.started_at is None:
        job.started_at = timezone.now()
    job.save(update_fields=["total_rows", "file_name", "status", "started_at"])

    path = output_path(job)
    resuming = job.processed_rows > 0 and os.path.exists(path)
    chunk = export_chunk_size()
    is_csv = job.export_format == SnapExportJob.Format.CSV

    handle = open(path, "a", newline="") if resuming else open(path, "w", newline="")
    try:
        writer = csv.DictWriter(handle, fieldnames=fields) if is_csv else None
        if is_csv and not resuming:
            writer.writeheader()

        offset = job.processed_rows
        while offset < job.total_rows:
            # Cancellation checkpoint — re-read just the status.
            job.refresh_from_db(fields=["status"])
            if job.status == SnapExportJob.Status.CANCELLED:
                return

            batch = list(qs[offset:offset + chunk].values(*fields))
            if not batch:
                break
            for row in batch:
                if is_csv:
                    writer.writerow(row)
                else:
                    handle.write(json.dumps(row, default=str) + "\n")
            handle.flush()

            offset += len(batch)
            job.processed_rows = offset
            job.save(update_fields=["processed_rows"])
    finally:
        handle.close()

    job.status = SnapExportJob.Status.COMPLETED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at"])
    logger.info("snapadmin.export.completed", job=str(job.pk), rows=job.processed_rows)
