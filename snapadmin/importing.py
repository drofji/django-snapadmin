"""
snapadmin/importing.py

CSV / NDJSON import, the write-side counterpart to :mod:`snapadmin.exporting`.

The export side is complete — jobs, formats, streaming, resumability,
pluggable sources — and this module mirrors that architecture for the
opposite direction: reading rows from a file and writing them into a model,
via :class:`~snapadmin.models.SnapImportJob`. Every project feeding master
data in from another system used to write this from scratch, even though
:class:`~snapadmin.models.SnapModel` already knows each model's fields, types
and validators.

The contract (design decisions, not implementation details someone could
have chosen differently by reading the code):

1. **Column -> field mapping.** Header-name matching by default
   (case-insensitive, whitespace/underscore-normalised, matched against both
   the field's name and its ``verbose_name``), plus an optional explicit
   ``column_map = {"CSV Header": "field_name"}`` that wins wherever it is
   given. An unmapped column is reported (in the run's summary) and skipped
   — never guessed at, never a hard failure.
2. **The duplicate key is a configurable natural key** — a field name or a
   tuple of field names. Left unset it defaults to the model's first
   ``unique=True`` field, or the primary key if the import file actually
   carries a mapped column for it; with neither, there is no way to detect a
   duplicate and every row is a create.
3. **On a duplicate hit:** ``on_conflict`` is ``"fail"`` (the default),
   ``"skip"`` or ``"update"``. **The default is "fail"** — an import that
   silently overwrites production rows because nobody passed a flag is the
   same class of bug the rest of this project's write-surface hardening
   exists to close. A "fail" duplicate is reported as a failed *row*, not a
   failed *run* — the same one-bad-row-must-not-abort-a-million-row-import
   principle as every other per-row failure here.
4. **Validation runs through the model's own field validators** —
   ``full_clean()`` on the constructed (or updated) instance, so every
   ``Snap*Field``'s extension/size/format rule applies for free. No parallel
   validation layer.
5. **The report is one NDJSON line per row** —
   ``{"row": <1-based>, "action": "created"|"updated"|"skipped"|"failed",
   "pk": …, "errors": {...}}`` — written through the same storage seam the
   export writer uses (:func:`snapadmin.exporting.get_export_storage`,
   :func:`snapadmin.exporting.export_dir`), plus one ``{"summary": {...}}``
   line once the run completes. **A malformed row is reported and the run
   continues** — one bad row must never abort a run processing a million of
   them.
6. **Crash-safe, chunked checkpoints — not one all-or-nothing transaction.**
   Each row's write is its own database transaction (a bad row's rollback
   never touches an already-committed one, and a poisoned transaction from
   one row's ``IntegrityError`` can't take a later row down with it); the
   job's own bookkeeping (``processed_rows``, the per-outcome counters, the
   report's crash-safe byte checkpoint) is persisted once per chunk, mirroring
   the export path's chunk size. A resumed run skips the rows a prior attempt
   already consumed (``processed_rows`` is itself the file-position cursor —
   rows are read from the file strictly in order, so no separate cursor is
   needed) and truncates the report back to its last confirmed length before
   appending, so a re-run cannot duplicate a report line either.
7. **Both a management command and a job.** ``manage.py snapadmin_import`` is
   the thin caller; :func:`run_import_job` (driving a
   :class:`~snapadmin.models.SnapImportJob`) is the engine, reusing the job
   model shape, chunking and resumability from the export/reindex jobs rather
   than inventing a parallel mechanism.
8. **The import path is a write surface from the first line, not a
   follow-up.** :func:`check_write_surface` enforces ``api_write_fields``,
   ``api_exclude_fields``, ``api_read_only`` / ``api_http_method_names`` and
   PII masking *before a single row is processed* — a column targeting an
   excluded, non-allowlisted or (for a requester without PII access) masked
   field fails the whole run with an error naming the field, rather than
   silently writing it. This is a structural check, run once against the
   resolved column mapping, not a per-row concern.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Any, Iterator

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from snapadmin.conf import get_setting
from snapadmin.exporting import export_dir, get_export_storage
from snapadmin.logging_config import get_logger
from snapadmin.masking import get_masked_fields, user_can_view_pii
from snapadmin.registry import get_model_meta

logger = get_logger(__name__)

DEFAULT_CHUNK_SIZE = 1000

#: HTTP verbs that mean "this model accepts writes through its own API" —
#: mirrors ``snapadmin.checks._api_writable_models``'s notion of "writable".
_WRITE_VERBS = frozenset({"post", "put", "patch"})

#: Extension -> import format, used when a caller does not pass one explicitly.
_FORMAT_BY_EXTENSION = {
    ".csv": "csv",
    ".json": "json",
    ".ndjson": "json",
    ".jsonl": "json",
}


class SnapImportError(Exception):
    """A structural problem that fails the whole import job.

    Distinct from a per-row validation failure (which is reported and the
    run continues): this is raised for something wrong with the *shape* of
    the request itself — an unknown format, a natural key that names a
    non-existent or unmapped field, or a write-surface violation — none of
    which get more correct by processing more rows.
    """


def import_chunk_size() -> int:
    return max(1, int(get_setting("SNAPADMIN_IMPORT_CHUNK_SIZE", DEFAULT_CHUNK_SIZE)))


def guess_import_format(file_path: str) -> str:
    """Infer ``"csv"``/``"json"`` from ``file_path``'s extension.

    Raises :class:`SnapImportError` for an extension this module does not
    recognise — the caller must pass ``import_format`` explicitly rather than
    have a typo'd path silently picked as one format or the other.
    """
    _, ext = os.path.splitext(file_path)
    fmt = _FORMAT_BY_EXTENSION.get(ext.lower())
    if fmt is None:
        raise SnapImportError(
            f"Can't infer an import format from {file_path!r} — pass --format explicitly "
            f"(one of: csv, json)."
        )
    return fmt


def _normalize_header(name: str) -> str:
    """Case/whitespace/underscore-insensitive key for header matching."""
    return re.sub(r"[\s_]+", "_", str(name).strip().lower()).strip("_")


def _import_fields(model) -> list:
    """Concrete fields eligible for import — mirrors ``exporting._export_fields``."""
    return list(model._meta.fields)


def read_header(file_path: str, import_format: str) -> list[str]:
    """Return the column names of ``file_path`` without reading the whole file.

    For NDJSON this is the key order of the *first* line — later lines may
    carry a different key set (an unmapped one is reported per rule 1; a
    missing one just yields ``None`` for that row's coercion, handled by
    ``field.null``/full_clean the same way a blank CSV cell is).
    """
    if import_format == "csv":
        with open(file_path, newline="", encoding="utf-8-sig") as fh:
            try:
                return next(csv.reader(fh))
            except StopIteration:
                return []
    if import_format == "json":
        with open(file_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                return list(json.loads(line).keys())
        return []
    raise SnapImportError(f"Unknown import format {import_format!r} (expected 'csv' or 'json').")


def iter_input_rows(file_path: str, import_format: str) -> Iterator[dict]:
    """Yield each data row of ``file_path`` as a ``{column: raw_value}`` dict."""
    if import_format == "csv":
        with open(file_path, newline="", encoding="utf-8-sig") as fh:
            yield from csv.DictReader(fh)
        return
    if import_format == "json":
        with open(file_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
        return
    raise SnapImportError(f"Unknown import format {import_format!r} (expected 'csv' or 'json').")


def resolve_column_map(
    model, header: list[str], *, explicit: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Return ``(column -> field_name, unmapped_columns)`` for ``header``.

    ``explicit`` (``{"CSV Header": "field_name"}``) wins wherever a header is
    named in it; every other header is matched by normalised name against
    both the field's ``name`` and its ``verbose_name``. A header matching
    neither is returned in ``unmapped_columns`` rather than raising — rule 1.
    """
    explicit = explicit or {}
    fields = _import_fields(model)
    fields_by_name = {f.name: f for f in fields}
    by_norm_name = {_normalize_header(f.name): f.name for f in fields}
    by_norm_verbose = {_normalize_header(str(f.verbose_name)): f.name for f in fields}

    column_map: dict[str, str] = {}
    unmapped: list[str] = []
    for column in header:
        if column in explicit:
            field_name = explicit[column]
            if field_name not in fields_by_name:
                raise SnapImportError(
                    f"column_map[{column!r}] names {field_name!r}, which is not a field on "
                    f"{model._meta.label}."
                )
            column_map[column] = field_name
            continue
        norm = _normalize_header(column)
        field_name = by_norm_name.get(norm) or by_norm_verbose.get(norm)
        if field_name is None:
            unmapped.append(column)
            continue
        column_map[column] = field_name
    return column_map, unmapped


def resolve_natural_key(
    model, *, explicit: str | tuple[str, ...] | list[str] | None, mapped_field_names: set[str],
) -> tuple[str, ...] | None:
    """Resolve the duplicate-detection key — rule 2.

    ``explicit`` (a field name, or a tuple/list of them) always wins and must
    name real fields. Otherwise: the model's first ``unique=True`` field
    (excluding the primary key), or the primary key itself if the import file
    actually maps a column to it; with neither, ``None`` — every row is then
    a create, since there is nothing to detect a duplicate with.
    """
    fields_by_name = {f.name: f for f in model._meta.fields}

    if explicit:
        keys = (explicit,) if isinstance(explicit, str) else tuple(explicit)
        for name in keys:
            if name not in fields_by_name:
                raise SnapImportError(
                    f"natural_key names {name!r}, which is not a field on {model._meta.label}."
                )
        return keys

    for f in model._meta.fields:
        if f.unique and not f.primary_key:
            return (f.name,)

    pk_name = model._meta.pk.name
    if pk_name in mapped_field_names:
        return (pk_name,)

    return None


def check_write_surface(model, field_names: set[str], *, requested_by=None) -> None:
    """Enforce the write-surface rules — rule 8. Raises :class:`SnapImportError`.

    Checked once against the resolved column mapping, before any row is
    processed: a model that refuses writes through its own API refuses them
    here too, a column targeting an excluded or non-allowlisted field is
    rejected by name, and a column targeting a masked/PII field is rejected
    unless ``requested_by`` is a user with PII access (``requested_by=None``
    — the default for a bare CLI invocation with no ``--requested-by`` — has
    no PII access, the fail-closed default).
    """
    if get_model_meta(model, "api_read_only", False):
        raise SnapImportError(
            f"{model._meta.label} is api_read_only — import refuses to write to it."
        )
    methods = get_model_meta(model, "api_http_method_names", None)
    if methods is not None and not _WRITE_VERBS.intersection(m.lower() for m in methods):
        raise SnapImportError(
            f"{model._meta.label}'s api_http_method_names excludes every write verb — "
            "import refuses to write to it."
        )

    excluded = set(get_model_meta(model, "api_exclude_fields", []) or [])
    excluded_hit = sorted(field_names & excluded)
    if excluded_hit:
        raise SnapImportError(
            f"Column(s) target excluded field(s) on {model._meta.label}: "
            f"{', '.join(excluded_hit)}."
        )

    allowlist = get_model_meta(model, "api_write_fields", None)
    if allowlist is not None:
        disallowed = sorted(field_names - set(allowlist))
        if disallowed:
            raise SnapImportError(
                f"Column(s) target field(s) outside api_write_fields on {model._meta.label}: "
                f"{', '.join(disallowed)}."
            )

    masked = set(get_masked_fields(model._meta.app_label, model._meta.model_name))
    masked_hit = sorted(field_names & masked)
    if masked_hit and not user_can_view_pii(requested_by):
        raise SnapImportError(
            f"Column(s) target masked/PII field(s) on {model._meta.label}: "
            f"{', '.join(masked_hit)}. The requester has no PII access (pass "
            "--requested-by a user holding it) — import refuses to write raw values into them."
        )


def _coerce(field, raw: Any) -> Any:
    """One column value -> the Python value ``field.attname`` accepts.

    A blank string against a nullable field becomes ``None`` (the common CSV
    convention for "no value"); everything else goes through the field's own
    ``to_python()``, so a genuinely malformed value raises the same
    ``ValidationError`` ``full_clean()`` would — caught by the per-row loop,
    never a parallel validation layer (rule 4).
    """
    if raw == "" and field.null:
        return None
    return field.to_python(raw)


def _build_field_values(model, column_map: dict[str, str], row: dict) -> dict[str, Any]:
    fields_by_name = {f.name: f for f in model._meta.fields}
    values: dict[str, Any] = {}
    for column, field_name in column_map.items():
        field = fields_by_name[field_name]
        values[field.attname] = _coerce(field, row.get(column))
    return values


def _natural_key_values(
    natural_key: tuple[str, ...], column_map: dict[str, str], row: dict,
) -> dict[str, Any] | None:
    """The natural key's raw column values for one ``row``, or ``None`` if the
    row does not carry every key column (nothing to dedupe this row against)."""
    field_to_column = {field_name: column for column, field_name in column_map.items()}
    values: dict[str, Any] = {}
    for name in natural_key:
        column = field_to_column.get(name)
        if column is None or column not in row:
            return None
        values[name] = row[column]
    return values


def _message_dict(exc: ValidationError) -> dict[str, list[str]]:
    if hasattr(exc, "message_dict"):
        return {k: list(v) for k, v in exc.message_dict.items()}
    return {"__all__": list(exc.messages)}


def _process_row(
    model, column_map: dict[str, str], natural_key: tuple[str, ...] | None,
    on_conflict: str, row_number: int, row: dict,
) -> dict:
    """Process one row to completion. Never raises — every failure mode
    becomes a ``"failed"`` report entry so one bad row can't abort the run.

    Each row is its own top-level database transaction: a failure rolls back
    only that row, and a later row is never poisoned by an earlier one's
    aborted transaction (the way sharing one long transaction across many
    rows would leave it, on a backend like PostgreSQL).
    """
    from snapadmin.models import SnapImportJob

    try:
        with transaction.atomic():
            key_values = _natural_key_values(natural_key, column_map, row) if natural_key else None
            existing = model.objects.filter(**key_values).first() if key_values else None

            if existing is not None:
                if on_conflict == SnapImportJob.OnConflict.FAIL:
                    return {
                        "row": row_number, "action": "failed", "pk": existing.pk,
                        "errors": {"__all__": [
                            f"Duplicate key {key_values!r} matches existing pk={existing.pk!r}."
                        ]},
                    }
                if on_conflict == SnapImportJob.OnConflict.SKIP:
                    return {"row": row_number, "action": "skipped", "pk": existing.pk, "errors": {}}
                # on_conflict == "update"
                values = _build_field_values(model, column_map, row)
                for attname, value in values.items():
                    setattr(existing, attname, value)
                existing.full_clean()
                existing.save()
                return {"row": row_number, "action": "updated", "pk": existing.pk, "errors": {}}

            values = _build_field_values(model, column_map, row)
            instance = model(**values)
            instance.full_clean()
            instance.save()
            return {"row": row_number, "action": "created", "pk": instance.pk, "errors": {}}
    except ValidationError as exc:
        return {"row": row_number, "action": "failed", "pk": None, "errors": _message_dict(exc)}
    except (ValueError, TypeError, IntegrityError) as exc:
        return {"row": row_number, "action": "failed", "pk": None, "errors": {"__all__": [str(exc)]}}


def _tally(job, entry: dict) -> None:
    action = entry["action"]
    if action == "created":
        job.created_count += 1
    elif action == "updated":
        job.updated_count += 1
    elif action == "skipped":
        job.skipped_count += 1
    else:
        job.failed_count += 1
    job.processed_rows += 1


def _write_lines(handle, lines: list[dict]) -> None:
    buffer = io.StringIO()
    for line in lines:
        buffer.write(json.dumps(line, default=str))
        buffer.write("\n")
    data = buffer.getvalue().encode("utf-8")
    handle.write(data)
    handle.flush()
    os.fsync(handle.fileno())


def _report_path(job) -> str:
    return os.path.join(export_dir(), job.report_file_name)


def _publish_report(job) -> None:
    """Publish the local report file through the export storage seam."""
    storage = get_export_storage()
    working_path = _report_path(job)
    try:
        if os.path.abspath(storage.path(job.report_file_name)) == os.path.abspath(working_path):
            return
    except NotImplementedError:
        pass  # Remote storage — fall through to upload.
    if storage.exists(job.report_file_name):
        storage.delete(job.report_file_name)
    with open(working_path, "rb") as fh:
        storage.save(job.report_file_name, fh)


def start_import(
    model, *, file_path: str, import_format: str | None = None,
    column_map: dict[str, str] | None = None,
    natural_key: str | tuple[str, ...] | list[str] | None = None,
    on_conflict: str = "fail", requested_by=None, resume: bool = False,
):
    """Create (or, with ``resume``, reuse) a :class:`SnapImportJob` for ``model``.

    ``resume=True`` looks for the most recent unfinished-or-failed job for
    this model *and* this source file name (two different files targeting
    the same model must never resume each other's job) and resets it to
    ``pending``; otherwise a fresh job is created.
    """
    from snapadmin.models import SnapImportJob

    fmt = import_format or guess_import_format(file_path)
    source_name = os.path.basename(file_path)

    if resume:
        existing = (
            SnapImportJob.objects
            .filter(app_label=model._meta.app_label, model=model.__name__, source_name=source_name)
            .exclude(status=SnapImportJob.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            existing.status = SnapImportJob.Status.PENDING
            existing.error = ""
            existing.save(update_fields=["status", "error"])
            return existing

    return SnapImportJob.objects.create(
        app_label=model._meta.app_label,
        model=model.__name__,
        import_format=fmt,
        source_name=source_name,
        column_map=column_map or {},
        natural_key=(
            [natural_key] if isinstance(natural_key, str) else list(natural_key or [])
        ),
        on_conflict=on_conflict,
        requested_by=requested_by,
    )


def run_import_job(job, *, file_path: str, chunk_size: int | None = None, on_progress=None) -> dict:
    """Execute (or resume) the import for ``job``. See the module docstring.

    Single-flight, like :func:`snapadmin.exporting.run_export_job`: the job
    is claimed with an atomic compare-and-set that only a ``pending`` or
    ``failed`` job wins. A structural problem (an unmapped natural key, a
    write-surface violation, an unreadable file) raises inside ``_run`` and
    is captured onto the job as ``failed`` here, never raised out — this is a
    background-job runner, not a request handler.
    """
    from snapadmin.models import SnapImportJob

    Status = SnapImportJob.Status
    claimed = (
        SnapImportJob.objects
        .filter(pk=job.pk, status__in=[Status.PENDING, Status.FAILED])
        .update(status=Status.PROCESSING)
    )
    if not claimed:
        logger.info("snapadmin.import.skipped", job=str(job.pk))
        return {"skipped": True, "reason": "already processing or finished"}

    job.refresh_from_db()
    try:
        return _run(job, file_path=file_path, chunk_size=chunk_size or import_chunk_size(),
                    on_progress=on_progress)
    except Exception as exc:
        logger.exception("snapadmin.import.failed", job=str(job.pk))
        job.status = Status.FAILED
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        return {"errors": [str(exc)], "created": job.created_count, "updated": job.updated_count,
                "skipped": job.skipped_count, "failed": job.failed_count}


def _run(job, *, file_path: str, chunk_size: int, on_progress) -> dict:
    from snapadmin.models import SnapImportJob

    Status = SnapImportJob.Status
    model = job.target_model()

    header = read_header(file_path, job.import_format)
    if not header:
        raise SnapImportError(f"{file_path} has no header row (or is empty) — nothing to import.")

    column_map, unmapped_columns = resolve_column_map(model, header, explicit=job.column_map or None)
    if not column_map:
        raise SnapImportError(
            f"No column in {file_path} maps to a field on {model._meta.label}."
        )
    mapped_field_names = set(column_map.values())

    check_write_surface(model, mapped_field_names, requested_by=job.requested_by)

    explicit_key = tuple(job.natural_key) if job.natural_key else None
    natural_key = resolve_natural_key(model, explicit=explicit_key, mapped_field_names=mapped_field_names)
    if natural_key:
        missing = [name for name in natural_key if name not in mapped_field_names]
        if missing:
            raise SnapImportError(
                f"Natural key field(s) not present in {file_path}'s mapped columns: "
                f"{', '.join(missing)}."
            )

    if job.started_at is None:
        job.started_at = timezone.now()
    if not job.report_file_name:
        job.report_file_name = f"import_{job.pk}_report.ndjson"
    if not job.total_rows:
        # A second full read of the file (rows are discarded as counted, so
        # this stays memory-flat) — the price of a progress percentage
        # instead of only a raw processed-rows count. Unset on resume (the
        # file hasn't changed), so this never re-runs on a later attempt.
        job.total_rows = sum(1 for _ in iter_input_rows(file_path, job.import_format))
    job.save(update_fields=["started_at", "report_file_name", "total_rows"])

    report_path = _report_path(job)
    resuming = job.processed_rows > 0 and os.path.exists(report_path)
    if resuming:
        with open(report_path, "r+b") as truncator:
            truncator.truncate(job.report_cursor_bytes)
    else:
        if os.path.exists(report_path):
            os.remove(report_path)
        if job.processed_rows:
            # A stale counter with no local report file to resume into (a
            # different worker, an ephemeral volume) — restart clean rather
            # than silently skip rows a fresh pass never wrote a report for.
            job.processed_rows = 0
            job.created_count = job.updated_count = job.skipped_count = job.failed_count = 0

    skip_rows = job.processed_rows
    row_number = skip_rows
    cancelled = False

    report_handle = open(report_path, "ab")
    try:
        rows = iter_input_rows(file_path, job.import_format)
        for _ in range(skip_rows):
            try:
                next(rows)
            except StopIteration:
                break

        row_iter = iter(rows)
        exhausted = False
        while not exhausted and not cancelled:
            chunk_rows = []
            for row in row_iter:
                chunk_rows.append(row)
                if len(chunk_rows) >= chunk_size:
                    break
            else:
                exhausted = True
            if not chunk_rows:
                break

            # The whole chunk — every row's write (or, for a failed row, its
            # rolled-back savepoint) plus the job's own checkpoint advance —
            # commits as one transaction. A crash anywhere before this block
            # exits leaves *nothing* from this chunk durable: not the row
            # writes, not processed_rows, not the report's confirmed byte
            # length. Resume therefore always restarts at a clean chunk
            # boundary and can never re-create a row this chunk already
            # committed — the property the "no duplicates after a crash"
            # requirement actually depends on.
            with transaction.atomic():
                chunk_entries = []
                for row in chunk_rows:
                    row_number += 1
                    entry = _process_row(
                        model, column_map, natural_key, job.on_conflict, row_number, row,
                    )
                    chunk_entries.append(entry)
                    _tally(job, entry)

                # Report lines are written (and fsynced) here, still inside
                # the transaction that will commit processed_rows /
                # report_cursor_bytes together — see snapadmin.exporting's
                # crash-safe writer for the same fsync-before-checkpoint
                # ordering, applied here at chunk instead of line grain.
                _write_lines(report_handle, chunk_entries)
                job.report_cursor_bytes = report_handle.tell()
                job.save(update_fields=[
                    "processed_rows", "created_count", "updated_count",
                    "skipped_count", "failed_count", "report_cursor_bytes",
                ])

            if on_progress:
                on_progress(job)

            job.refresh_from_db(fields=["status"])
            if job.status == Status.CANCELLED:
                cancelled = True

        if cancelled:
            logger.info("snapadmin.import.cancelled", job=str(job.pk), rows=job.processed_rows)
            return {"cancelled": True, "created": job.created_count, "updated": job.updated_count,
                    "skipped": job.skipped_count, "failed": job.failed_count}

        summary = {
            "created": job.created_count, "updated": job.updated_count,
            "skipped": job.skipped_count, "failed": job.failed_count,
            "total": job.processed_rows, "unmapped_columns": unmapped_columns,
        }
        _write_lines(report_handle, [{"summary": summary}])
        job.report_cursor_bytes = report_handle.tell()
        job.save(update_fields=["report_cursor_bytes"])
    finally:
        report_handle.close()

    _publish_report(job)
    job.status = Status.COMPLETED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at"])
    if on_progress:
        on_progress(job)
    logger.info(
        "snapadmin.import.completed", job=str(job.pk), rows=job.processed_rows,
        created=job.created_count, updated=job.updated_count,
        skipped=job.skipped_count, failed=job.failed_count,
    )
    return summary
