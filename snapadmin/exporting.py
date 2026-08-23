"""
snapadmin/exporting.py

Asynchronous, fault-tolerant background export of model rows.

Large synchronous exports time out; this module streams a model's rows to a
CSV, JSON or XLSX file in chunks, tracking progress on a :class:`SnapExportJob`
so an API consumer can poll status / ETA, cancel, and download the result.

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
* **Line-based vs container formats** — everything above describes ``csv`` and
  ``json`` (newline-delimited), which are appended chunk by chunk. ``xlsx`` is a
  *container*: a workbook is a zip archive that only becomes readable once it is
  closed, so it cannot be appended to mid-stream and a half-written one is not a
  smaller export, it is a corrupt file. It is therefore written by
  :class:`_WorkbookWriter`, which streams the chunks into ``openpyxl``'s
  write-only workbook (spooled to a temporary file, so memory stays at one chunk)
  and moves the finished workbook into place in a single ``os.replace``. Two
  deliberate consequences: an ``xlsx`` job **does not resume** — a re-dispatched
  one re-exports from the first row — and a cancelled or failed ``xlsx`` job
  leaves **no** partial file to download, where a cancelled ``csv`` leaves the
  rows it managed to write. ``openpyxl`` is an optional dependency (the
  ``[xlsx]`` extra); requesting the format without it fails the job with a
  pointed ``ImproperlyConfigured`` rather than a ``ModuleNotFoundError``.
"""

from __future__ import annotations

import csv
import datetime
import importlib.util
import io
import json
import os
import re
from decimal import Decimal
from typing import Iterator, Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils import timezone
from django.utils.module_loading import import_string

from snapadmin.logging_config import get_logger
from snapadmin.masking import get_masked_fields, mask_field, user_can_view_pii

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


class ExportRowSource(Protocol):
    """The row-source contract the export writer drives.

    A source owns *what* rows to export and *how* each row looks; the writer owns
    everything else — chunking, progress, cancellation, crash-safe resume and
    storage. This lets a project export a set defined by a structured Elasticsearch
    query, an explicit key list, or a custom document shape without subclassing the
    job or its runner. Register one under a name in ``SNAPADMIN_EXPORT_SOURCES`` (a
    ``{name: "dotted.path.to.factory"}`` map, where the factory is
    ``factory(job) -> ExportRowSource``) and set ``SnapExportJob.source`` to that
    name. Blank ``source`` (the default) uses the built-in ORM source.
    """

    def field_names(self) -> list[str]:
        """Column order — the CSV header and the keys written from each row dict."""

    def count(self) -> int:
        """Total row count, for progress/ETA (may be an estimate)."""

    def iter_batches(self, *, cursor: str | None, chunk_size: int) -> Iterator[tuple[list[dict], str]]:
        """Yield ``(rows, next_cursor)`` starting *after* ``cursor``.

        ``rows`` is a list of dicts keyed by :meth:`field_names`; ``next_cursor`` is
        an opaque string the writer checkpoints and passes back as ``cursor`` on a
        resume, so a source must be able to continue deterministically from it.
        ``cursor`` is ``None`` on a fresh run. Apply any PII masking here — the
        writer serializes the rows verbatim.
        """


class _DefaultOrmSource:
    """Built-in source: ``model.objects.filter(**job.filters)`` as raw column rows,
    paged by a primary-key cursor and PII-masked per the job's requester. This is
    the behaviour a blank ``SnapExportJob.source`` keeps, byte-for-byte."""

    def __init__(self, job) -> None:
        model = job.target_model()
        self._pk_attname = model._meta.pk.attname
        self._model_key = (model._meta.app_label, model._meta.model_name)
        self._requested_by = job.requested_by
        self._fields = _export_fields(model)
        self._masked = (
            set()
            if user_can_view_pii(job.requested_by)
            else set(get_masked_fields(model._meta.app_label, model._meta.model_name))
        )
        qs = model.objects.all()
        if job.filters:
            qs = qs.filter(**job.filters)
        self._qs = qs.order_by("pk")

    def field_names(self) -> list[str]:
        return self._fields

    def count(self) -> int:
        return self._qs.count()

    def iter_batches(self, *, cursor: str | None, chunk_size: int) -> Iterator[tuple[list[dict], str]]:
        while True:
            chunk_qs = self._qs.filter(pk__gt=cursor) if cursor is not None else self._qs
            batch = list(chunk_qs[:chunk_size].values(*self._fields))
            if not batch:
                return
            if self._masked:
                for row in batch:
                    for name in self._masked:
                        if name in row:
                            row[name] = mask_field(*self._model_key, name, row[name],
                                                   self._requested_by)
            cursor = str(batch[-1][self._pk_attname])
            yield batch, cursor


def get_export_source(job) -> ExportRowSource:
    """Resolve the row source for ``job``.

    Blank ``job.source`` -> the built-in :class:`_DefaultOrmSource`. Otherwise the
    name is looked up in ``SNAPADMIN_EXPORT_SOURCES`` and its dotted-path factory is
    called with the job. An unknown name raises ``ImproperlyConfigured`` (surfaced
    as a failed job, never a crashed worker).
    """
    if not job.source:
        return _DefaultOrmSource(job)
    registry = getattr(settings, "SNAPADMIN_EXPORT_SOURCES", None) or {}
    dotted = registry.get(job.source)
    if dotted is None:
        raise ImproperlyConfigured(
            f"Export source {job.source!r} is not registered in SNAPADMIN_EXPORT_SOURCES."
        )
    factory = import_string(dotted) if isinstance(dotted, str) else dotted
    return factory(job)


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


# ─────────────────────────────────────────────────────────────────────────────
# Format writers
#
# The chunk loop in _run owns rows, progress, cancellation and checkpoints; a
# writer owns the bytes. Splitting them is what lets a container format (XLSX)
# share the loop with the line-based ones without teaching the loop about zip
# archives — it only has to know whether the format can be resumed.
# ─────────────────────────────────────────────────────────────────────────────


class _ExportWriter(Protocol):
    """The sink contract :func:`_run` drives, one implementation per format."""

    #: Whether a re-dispatched job may continue from ``(cursor_pk, cursor_bytes)``
    #: instead of re-exporting from the first row.
    resumable: bool

    #: Bytes confirmed on disk, checkpointed as ``cursor_bytes``. Meaningful only
    #: for a resumable writer; a container writer leaves it at 0.
    byte_len: int

    def start(self, *, resuming: bool, byte_len: int) -> None:
        """Open the sink, writing any header the format needs on a fresh run."""

    def write(self, batch: list[dict]) -> None:
        """Write one chunk, updating :attr:`byte_len`."""

    def finish(self) -> None:
        """Called once all rows are written — the point a container is closed."""

    def close(self) -> None:
        """Release resources, on the completed *and* the cancelled/failed path."""


class _LineFileWriter:
    """Append-only writer for the line-based formats (CSV and NDJSON).

    Every chunk is ``fsync``-ed before :func:`_run` checkpoints it, so the file
    can always be truncated back to a confirmed byte length on resume — the
    crash-safety property described in the module docstring.
    """

    resumable = True

    def __init__(self, path: str, fields: list[str], *, is_csv: bool) -> None:
        self._path = path
        self._fields = fields
        self._is_csv = is_csv
        self._handle: io.BufferedWriter | None = None
        self.byte_len = 0

    def start(self, *, resuming: bool, byte_len: int) -> None:
        self.byte_len = byte_len
        self._handle = open(self._path, "ab" if resuming else "wb")
        if self._is_csv and not resuming:
            self.byte_len += _write_bytes(self._handle, _csv_header_bytes(self._fields))

    def write(self, batch: list[dict]) -> None:
        self.byte_len += _write_bytes(self._handle, _rows_bytes(batch, self._fields, self._is_csv))

    def finish(self) -> None:
        """Nothing to finalise — every chunk is already durable on disk."""

    def close(self) -> None:
        self._handle.close()


class _WorkbookWriter:
    """XLSX writer, backed by the optional ``openpyxl`` dependency.

    Rows are streamed into a write-only workbook, which spools them to a
    temporary file as they arrive rather than holding the sheet in memory, so the
    ceiling is one chunk (``SNAPADMIN_EXPORT_CHUNK_SIZE`` rows) regardless of how
    large the export is. The workbook is assembled on :meth:`finish` next to the
    working file and moved into place with a single ``os.replace``, so the path
    the download endpoint reads either holds a complete workbook or nothing.

    ``resumable`` is ``False``: the spool is not a workbook until it is closed, so
    there is nothing to resume into and a re-dispatched job starts over.
    """

    resumable = False

    #: Sheet name of the exported rows. Excel caps sheet names at 31 characters
    #: and forbids []:*?/\\ — a constant sidesteps both.
    sheet_title = "Export"

    def __init__(self, path: str, fields: list[str]) -> None:
        self._path = path
        self._fields = fields
        self._workbook = None
        self._sheet = None
        self._cell_cls = None
        self.byte_len = 0

    def start(self, *, resuming: bool, byte_len: int) -> None:
        workbook_cls, self._cell_cls = _load_openpyxl()
        self._workbook = workbook_cls(write_only=True)
        self._sheet = self._workbook.create_sheet(title=self.sheet_title)
        self._sheet.append(list(self._fields))

    def write(self, batch: list[dict]) -> None:
        for row in batch:
            self._sheet.append(
                [_xlsx_cell(self._sheet, self._cell_cls, row.get(name)) for name in self._fields]
            )

    def finish(self) -> None:
        spool = f"{self._path}.part"
        self._workbook.save(spool)
        os.replace(spool, self._path)

    def close(self) -> None:
        # openpyxl removes its own spool file when save() succeeds and otherwise
        # only at interpreter exit — far too late for a Celery worker, which would
        # accumulate one abandoned spool per cancelled or failed XLSX export. The
        # sheet is closed first: dropping the file while its XML stream is still
        # open leaves a generator that raises "I/O operation on closed file" from
        # whichever unrelated code happens to trigger the collection. ``_writer``
        # exists from the header row start() wrote, and save() has already removed
        # its spool by the time a completed export gets here.
        spool = self._sheet._writer
        if os.path.exists(spool.out):
            self._sheet.close()
            spool.cleanup()


def _writer_for(job, fields: list[str], working_path: str) -> _ExportWriter:
    """Return the writer for ``job``'s format (see ``SnapExportJob.Format``)."""
    from snapadmin.models import SnapExportJob

    if job.export_format == SnapExportJob.Format.XLSX:
        return _WorkbookWriter(working_path, fields)
    return _LineFileWriter(
        working_path, fields, is_csv=job.export_format == SnapExportJob.Format.CSV
    )


def _load_openpyxl():
    """Return ``(Workbook, WriteOnlyCell)``, imported lazily.

    ``openpyxl`` (MIT) is an optional dependency: XLSX is a convenience format
    most installs never ask for, and the licence-hygiene rule keeps anything the
    core does not need out of the base install. The pointed error therefore fires
    when an XLSX export actually runs, not on import.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
    except ImportError as exc:
        raise ImproperlyConfigured(
            "XLSX export needs the openpyxl library. Install the optional extra "
            "`pip install django-snapadmin[xlsx]`, or export this job as csv or json."
        ) from exc
    return Workbook, WriteOnlyCell


def xlsx_available() -> bool:
    """Whether the optional ``[xlsx]`` extra (``openpyxl``) can be imported.

    Uses ``find_spec`` rather than a real import so asking the question costs
    nothing and has no side effects — the REST layer calls it on every export
    request to reject ``xlsx`` with a 400 up front, instead of accepting a job
    that can only fail in the worker minutes later.
    """
    return importlib.util.find_spec("openpyxl") is not None


#: Characters Excel rejects outright (openpyxl raises ``IllegalCharacterError``).
#: Stripped rather than allowed to fail an entire export over one row's stray byte.
_XLSX_ILLEGAL_RE = re.compile(r"[\000-\010\013\014\016-\037]")

#: Excel's hard ceiling on the text length of a single cell.
XLSX_MAX_CELL_CHARS = 32767

#: Value types openpyxl writes natively; anything else is exported as text.
#: ``datetime.datetime`` is covered by ``date``, and ``bool`` by ``int``.
_XLSX_NATIVE_TYPES = (bool, int, float, Decimal, datetime.date, datetime.time, datetime.timedelta)


def _xlsx_value(value: object) -> object:
    """Coerce ``value`` into something openpyxl accepts.

    This cannot be left to openpyxl: an unsupported value (a ``UUID``, a
    ``dict``, an aware datetime) does not fail one cell, it kills the sheet's
    row generator and every later ``append`` raises ``StopIteration``. So:

    * numbers, ``Decimal``, dates, times and durations pass through, so Excel
      receives real numbers and dates rather than text;
    * an aware datetime is converted to the project's current timezone and
      stripped of its ``tzinfo`` (Excel has no concept of one), matching how
      Django renders datetimes everywhere else; an aware time, which has no
      date to convert against, only loses its ``tzinfo``;
    * anything else becomes ``str(value)``, as the CSV and JSON writers do.
    """
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value
    if isinstance(value, datetime.time):
        return value.replace(tzinfo=None) if value.utcoffset() is not None else value
    if value is None or isinstance(value, _XLSX_NATIVE_TYPES):
        return value
    return _XLSX_ILLEGAL_RE.sub("", str(value))[:XLSX_MAX_CELL_CHARS]


def _xlsx_cell(sheet, cell_cls, value: object) -> object:
    """Return what to append for ``value`` — a bare value, or a text-typed cell.

    openpyxl infers a cell's type from its text, so a string beginning with ``=``
    is stored as a **formula**: exported row data that Excel evaluates when the
    file is opened, the spreadsheet counterpart of CSV injection. Those values
    get a cell whose type is pinned to text, so Excel shows the characters that
    are actually in the database and executes nothing.
    """
    value = _xlsx_value(value)
    if isinstance(value, str) and value.startswith("="):
        cell = cell_cls(sheet, value=value)
        cell.data_type = "s"
        return cell
    return value


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

    # The row source owns the queryset/query, the column shape and PII masking; the
    # rest of this function owns chunking, progress, cancellation, crash-safe resume
    # and storage — identically for the built-in ORM source and any custom one.
    source = get_export_source(job)
    fields = source.field_names()

    job.total_rows = source.count()
    if not job.file_name:
        job.file_name = f"export_{job.pk}.{job.export_format}"
    if job.started_at is None:
        job.started_at = timezone.now()
    # Status is already PROCESSING (claimed by run_export_job); persist the rest.
    job.save(update_fields=["total_rows", "file_name", "started_at"])

    name = export_file_name(job)
    working_path = _working_path(name)
    chunk = export_chunk_size()
    writer = _writer_for(job, fields, working_path)
    # A container format has nothing to resume into (see _WorkbookWriter), so it
    # takes the fresh-start branch below and re-exports from the first row.
    resuming = writer.resumable and bool(job.cursor_pk) and os.path.exists(working_path)

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

    cursor = job.cursor_pk if resuming else None
    writer.start(resuming=resuming, byte_len=job.cursor_bytes if resuming else 0)
    try:
        batches = source.iter_batches(cursor=cursor, chunk_size=chunk)
        while True:
            # Cancellation checkpoint — re-read just the status *before* pulling the
            # next batch, so a cancel stops us without writing it.
            job.refresh_from_db(fields=["status"])
            if job.status == SnapExportJob.Status.CANCELLED:
                return

            try:
                batch, next_cursor = next(batches)
            except StopIteration:
                break

            writer.write(batch)

            # Persist the checkpoint *after* the bytes are durable, so a crash
            # can only under-count (a safe, idempotent re-process of the tail).
            job.cursor_pk = next_cursor
            job.cursor_bytes = writer.byte_len
            job.processed_rows += len(batch)
            job.save(update_fields=["cursor_pk", "cursor_bytes", "processed_rows"])

        writer.finish()
    finally:
        writer.close()

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
