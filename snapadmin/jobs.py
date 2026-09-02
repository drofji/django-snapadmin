"""
snapadmin/jobs.py

The resumable, progress-tracking background-job models: :class:`SnapExportJob`,
:class:`SnapReindexJob` and :class:`SnapImportJob`, sharing :class:`SnapJobBase`.

Split out of ``snapadmin.models`` (#SIMPL1f) and re-exported from there
unchanged: ``from snapadmin.models import SnapExportJob`` keeps working. This
module stays inside the ``snapadmin`` package (not a subpackage) so Django's
app-config resolution for these model classes is unaffected by the move —
they are still declared under the same app as before, just in a different
file that ``snapadmin.models`` imports at module level.
"""

from __future__ import annotations

import uuid

from django.apps import apps
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SnapJobBase(models.Model):
    """Abstract base for a resumable, progress-tracking background job.

    Shared by :class:`SnapExportJob` and :class:`SnapReindexJob`: both are
    created up front and filled in chunk by chunk by a runner that updates
    ``processed_rows`` so a poller can report live progress (``progress_percent``)
    and an ETA (``eta_seconds``), resume from a crash via a ``cursor_pk``
    checkpoint (``pk__gt`` cursor pagination, no OFFSET drift), and can be
    stopped between chunks by setting ``status`` to ``cancelled``
    (``is_finished`` then becomes true). ``cursor_pk`` itself is declared on
    each concrete subclass rather than here — its ``help_text`` carries a
    genuinely different meaning per job (last *exported* vs last *indexed*
    row) and collapsing that into one shared string would make one of the two
    readings a lie.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    #: Human-readable prefix for __str__, set by each concrete subclass
    #: (e.g. "Export", "Reindex"). A plain class attribute, not a model field.
    job_label: str = ""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    app_label = models.CharField(max_length=100, verbose_name=_("App Label"))
    model = models.CharField(max_length=100, verbose_name=_("Model"))
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True, verbose_name=_("Status"))
    total_rows = models.PositiveIntegerField(default=0, verbose_name=_("Total Rows"))
    processed_rows = models.PositiveIntegerField(default=0, verbose_name=_("Processed Rows"), help_text=_("Rows written so far — drives progress-percent and ETA reporting."))
    error = models.TextField(blank=True, verbose_name=_("Error"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Created At"))
    started_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Started At"))
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Finished At"))
    # The submitter's tenant (#FUT1b), captured once when the job targets a
    # tenant_scoped model and stamped here so a worker that runs later, in a
    # different process with no request in hand, can replay it via
    # snapadmin.tenancy.use_tenant() — see SnapExportJob/SnapImportJob's
    # creation paths. Blank for a job targeting a model that is not
    # tenant-scoped, and for SnapReindexJob, whose sweep is deliberately
    # cross-tenant (see snapadmin.reindexing) and never stamps this field.
    tenant_id = models.CharField(max_length=64, blank=True, default="", db_index=True, verbose_name=_("Tenant"), help_text=_("The submitter's tenant, replayed when the job runs. Blank for a job whose target model is not tenant-scoped."))

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.job_label} {self.app_label}.{self.model} [{self.status}] {self.processed_rows}/{self.total_rows}"

    def target_model(self):
        """Resolve the model this job targets (raises LookupError if unknown)."""
        return apps.get_model(self.app_label, self.model)

    @property
    def is_finished(self) -> bool:
        return self.status in (self.Status.COMPLETED, self.Status.FAILED, self.Status.CANCELLED)

    @property
    def progress_percent(self) -> int:
        if not self.total_rows:
            return 100 if self.status == self.Status.COMPLETED else 0
        return min(100, round(self.processed_rows * 100 / self.total_rows))

    @property
    def eta_seconds(self):
        """Estimated seconds remaining, or ``None`` when not computable yet."""
        if self.status == self.Status.COMPLETED:
            return 0
        if self.status != self.Status.PROCESSING or not self.started_at or not self.processed_rows:
            return None
        elapsed = (timezone.now() - self.started_at).total_seconds()
        rate = self.processed_rows / elapsed if elapsed > 0 else 0
        if rate <= 0:
            return None
        return round(max(0, self.total_rows - self.processed_rows) / rate)


class SnapExportJob(SnapJobBase):
    """A background CSV / JSON / XLSX export of a model's rows.

    Created via ``POST /api/exports/``; a Celery task
    (``snapadmin.run_export``) fills it in chunk by chunk, updating
    ``processed_rows`` so ``GET /api/exports/<id>/`` can report live progress and
    an ETA. Fault-tolerant: the writer resumes from the ``cursor_pk`` /
    ``cursor_bytes`` checkpoint (not the ``processed_rows`` counter) so a retry
    never duplicates or skips a row — except for ``xlsx``, a container format
    that a retry re-exports from the first row. Cancellable: setting ``status`` to
    ``cancelled`` stops the task between chunks. See :mod:`snapadmin.exporting`.
    """

    job_label = "Export"

    class Format(models.TextChoices):
        # CSV and JSON (newline-delimited) are line-based: the writer appends
        # chunk after chunk and resumes from a byte offset. XLSX is a *container*
        # format written whole on completion, so it needs the optional [xlsx]
        # extra (openpyxl) and does not resume — see snapadmin.exporting.
        CSV = "csv", "CSV"
        JSON = "json", "JSON"
        XLSX = "xlsx", "XLSX"

    export_format = models.CharField(max_length=8, choices=Format.choices, default=Format.CSV, verbose_name=_("Format"))
    filters = models.JSONField(default=dict, blank=True, verbose_name=_("Filters"), help_text=_("ORM field=value filters applied to the export queryset."))
    # Named row source (see SNAPADMIN_EXPORT_SOURCES + snapadmin.exporting). Blank
    # (the default) uses the built-in ORM source: model.objects.filter(**filters)
    # serialized as raw column rows. A non-blank value names a registered custom
    # source (ES-query-backed, key-list-backed, custom document shape); the runner's
    # crash-safe chunking / progress / cancel / resume / storage are unchanged.
    source = models.CharField(max_length=64, blank=True, default="", verbose_name=_("Row Source"), help_text=_("Registered SNAPADMIN_EXPORT_SOURCES name; blank uses the default ORM source."))
    # Crash-safe resume checkpoint (see snapadmin.exporting). cursor_pk is the
    # primary key of the last exported row, used for pk__gt cursor pagination on
    # resume (no OFFSET drift); cursor_bytes is the working file's byte length
    # confirmed at that pk, used to truncate any uncheckpointed tail on resume.
    # Stored as a string so any primary-key type (int / UUID / char) round-trips.
    # (Declared here rather than on SnapJobBase — see the base's docstring.)
    cursor_pk = models.CharField(max_length=255, blank=True, default="", verbose_name=_("Resume Cursor (PK)"), help_text=_("Primary key of the last exported row; blank means start from the beginning."))
    cursor_bytes = models.PositiveBigIntegerField(default=0, verbose_name=_("Resume Byte Offset"), help_text=_("Byte length of the working file confirmed at cursor_pk."))
    file_name = models.CharField(max_length=255, blank=True, verbose_name=_("File Name"))
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=_("Requested By"))

    class Meta(SnapJobBase.Meta):
        verbose_name = _("Export Job")
        verbose_name_plural = _("Export Jobs")


class SnapReindexJob(SnapJobBase):
    """A background Elasticsearch reindex of a model's rows.

    Created by the ``snapadmin_reindex`` management command; the runner in
    :mod:`snapadmin.reindexing` fills it in chunk by chunk, updating
    ``processed_rows`` so the command can report live progress and an ETA.
    Fault-tolerant: the runner resumes from the ``cursor_pk`` checkpoint
    (``pk__gt`` cursor pagination, no OFFSET drift) so a retry with ``--resume``
    picks up where a crash left off rather than restarting the whole table.
    Reindexing is idempotent — each document is written under ``_id = pk``, so a
    resumed (or fully restarted) run only ever overwrites, never duplicates.
    Cancellable: setting ``status`` to ``cancelled`` stops the runner between
    chunks. See :mod:`snapadmin.reindexing`.
    """

    job_label = "Reindex"

    # Crash-safe resume checkpoint (see snapadmin.reindexing): cursor_pk is the
    # primary key of the last indexed row, used for pk__gt cursor pagination on
    # resume (no OFFSET drift). Stored as a string so any primary-key type
    # (int / UUID / char) round-trips. ES_ONLY models have no DB pk cursor and
    # always reindex in a single pass, so this stays blank for them.
    # (Declared here rather than on SnapJobBase — see the base's docstring.)
    cursor_pk = models.CharField(max_length=255, blank=True, default="", verbose_name=_("Resume Cursor (PK)"), help_text=_("Primary key of the last indexed row; blank means start from the beginning."))

    class Meta(SnapJobBase.Meta):
        verbose_name = _("Reindex Job")
        verbose_name_plural = _("Reindex Jobs")


class SnapImportJob(SnapJobBase):
    """A background CSV / NDJSON import of rows into a model, mirroring
    :class:`SnapExportJob`'s architecture for the opposite direction.

    Created via ``manage.py snapadmin_import``; the runner in
    :mod:`snapadmin.importing` fills it in chunk by chunk, updating
    ``processed_rows`` (and the per-outcome counters below) so the command can
    report live progress. Fault-tolerant: unlike the export/reindex jobs, a
    row here needs no cursor into the *target* model — ``processed_rows``
    itself is the resume cursor into the *input file* (skip that many rows
    from the top and continue), because rows are read from the file strictly
    in order. The run's own NDJSON report (one line per row plus a summary
    line) is checkpointed the same crash-safe way :class:`SnapExportJob`'s
    line-based writer is (``report_cursor_bytes`` truncates any
    uncheckpointed tail before a resume appends further), through the same
    storage seam (:func:`snapadmin.exporting.get_export_storage`). Cancellable:
    setting ``status`` to ``cancelled`` stops the runner between chunks. See
    :mod:`snapadmin.importing`.
    """

    job_label = "Import"

    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        JSON = "json", "JSON"

    class OnConflict(models.TextChoices):
        # The default is FAIL, deliberately — see snapadmin.importing's module
        # docstring: an import that silently overwrites production rows
        # because nobody passed a flag is the same class of bug this whole
        # batch of fixes exists to close.
        FAIL = "fail", _("Fail")
        SKIP = "skip", _("Skip")
        UPDATE = "update", _("Update")

    import_format = models.CharField(max_length=8, choices=Format.choices, default=Format.CSV, verbose_name=_("Format"))
    source_name = models.CharField(max_length=255, blank=True, verbose_name=_("Source File"), help_text=_("Display-only name of the input file this job reads."))
    # Explicit header -> field-name overrides (see snapadmin.importing.resolve_column_map).
    # Header-name matching fills in every column not named here.
    column_map = models.JSONField(default=dict, blank=True, verbose_name=_("Column Map"), help_text=_("Explicit CSV/JSON header -> field name overrides; header-name matching fills the rest."))
    # A field name or list of field names; blank resolves the default at run time
    # (the model's first unique=True field, or the pk if the file carries it).
    natural_key = models.JSONField(default=list, blank=True, verbose_name=_("Natural Key"), help_text=_("Field name(s) that identify a duplicate row; blank resolves the default at run time."))
    on_conflict = models.CharField(max_length=8, choices=OnConflict.choices, default=OnConflict.FAIL, verbose_name=_("On Conflict"))
    # Crash-safe resume checkpoint for the NDJSON report file — see the class
    # docstring. Stored the same way SnapExportJob.cursor_bytes is.
    report_file_name = models.CharField(max_length=255, blank=True, verbose_name=_("Report File"))
    report_cursor_bytes = models.PositiveBigIntegerField(default=0, verbose_name=_("Report Resume Byte Offset"), help_text=_("Byte length of the report file confirmed as written."))
    created_count = models.PositiveIntegerField(default=0, verbose_name=_("Created"))
    updated_count = models.PositiveIntegerField(default=0, verbose_name=_("Updated"))
    skipped_count = models.PositiveIntegerField(default=0, verbose_name=_("Skipped"))
    failed_count = models.PositiveIntegerField(default=0, verbose_name=_("Failed"))
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=_("Requested By"), help_text=_("Used for PII-access checks on masked target fields; blank is treated as no PII access."))

    class Meta(SnapJobBase.Meta):
        verbose_name = _("Import Job")
        verbose_name_plural = _("Import Jobs")
