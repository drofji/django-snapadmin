"""
snapadmin/models.py
Core module for SnapAdmin — an auto-registration layer on top of Django's built-in admin with Unfold integration.
"""

import hashlib
import secrets
import string
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import timedelta
from enum import Enum
from typing import Any, NamedTuple, NoReturn

from asgiref.sync import sync_to_async

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, FieldError, ImproperlyConfigured, ValidationError
from django.contrib import admin
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.conf import settings

# Unfold imports
try:
    from django.conf import settings
    if 'unfold' not in settings.INSTALLED_APPS:
        raise ImportError("Unfold not in INSTALLED_APPS")  # pragma: no cover

    from unfold.admin import ModelAdmin
    from unfold.contrib.filters.admin import (
        RangeDateFilter,
        RangeNumericFilter,
        TextFilter,
        RelatedDropdownFilter,
        ChoicesDropdownFilter,
    )
    from unfold.decorators import display as unfold_display
    UNFOLD_INSTALLED = True
except (ImportError, RuntimeError):  # pragma: no cover
    from django.contrib.admin import ModelAdmin
    RangeDateFilter = admin.DateFieldListFilter
    RangeNumericFilter = admin.AllValuesFieldListFilter
    TextFilter = admin.AllValuesFieldListFilter
    RelatedDropdownFilter = admin.RelatedFieldListFilter
    ChoicesDropdownFilter = admin.ChoicesFieldListFilter

    def unfold_display(description=None, header=False, label=False, **kwargs):
        def decorator(func):
            if description:
                func.short_description = description
            return func
        return decorator
    UNFOLD_INSTALLED = False


def _wysiwyg_widget():
    """Return a CKEditor 5 widget for wysiwyg fields, importing it lazily.

    ``django-ckeditor-5`` bundles CKEditor 5 (a GPL / commercial editor), so it is
    an **optional** dependency — only projects that actually use wysiwyg fields
    need it. Importing it here (rather than at module load) lets SnapModels load
    without it installed; the clear error only fires if a wysiwyg field is used.
    """
    try:
        from django_ckeditor_5.widgets import CKEditor5Widget
    except ImportError as exc:
        raise ImproperlyConfigured(
            "A SnapModel field sets wysiwyg=True, which needs the CKEditor 5 "
            "rich-text editor. Install the optional extra "
            "`pip install django-snapadmin[wysiwyg]`, add 'django_ckeditor_5' to "
            "INSTALLED_APPS and define CKEDITOR_5_CONFIGS['extends']."
        ) from exc
    return CKEditor5Widget(config_name="extends")


from snapadmin import fields as snapfields
from snapadmin.conf import get_setting
from snapadmin.fields import DjangoFieldAttributeEnum, SnapFieldAttributeEnum, SnapField
from snapadmin.logging_config import get_logger
from snapadmin.pagination import EstimatedCountPaginator
from snapadmin.registry import get_model_meta, is_registered, register
from snapadmin.sanitize import sanitize_html

logger = get_logger(__name__)


# ===========================================================================
# API Token Models
# ===========================================================================

def validate_allowed_models(value):
    if not isinstance(value, list):
        raise ValidationError(_("Allowed models must be a list."))
    for item in value:
        if not isinstance(item, str) or "." not in item:
            raise ValidationError(_("Invalid model format: '%(item)s'."), params={"item": item})
        parts = item.split(".")
        if len(parts) != 2:
            raise ValidationError(_("Invalid model format: '%(item)s'."), params={"item": item})
        try:
            apps.get_model(parts[0], parts[1])
        except LookupError:
            raise ValidationError(_("Model '%(item)s' does not exist."), params={"item": item})


def validate_allowed_scopes(value):
    """Every entry must be a non-blank string — SnapAdmin never inspects what
    a scope *means* (that is entirely the project's), only its shape.
    """
    if not isinstance(value, list):
        raise ValidationError(_("Allowed scopes must be a list."))
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(_("Invalid scope: '%(item)s'."), params={"item": item})

TOKEN_KEY_LENGTH = 40
TOKEN_PREFIX_LENGTH = 8

def _generate_token_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(TOKEN_KEY_LENGTH))

def hash_token_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw token key.

    Token keys are high-entropy random strings, so a single fast cryptographic
    hash (rather than a slow password hash) is the appropriate, constant-cost
    way to store them: the raw key is never written to the database, and lookup
    is an indexed equality match on the digest.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

class APIToken(models.Model):
    # Holds the raw secret only on the in-memory instance that just minted it
    # (during save/create). It is never persisted and is None for any token
    # re-fetched from the database.
    _raw_token_key: str | None = None

    token_name = models.CharField(max_length=100, verbose_name=_("Token Name"), help_text=_("A descriptive name for this token (e.g. 'CI Pipeline', 'Read-only dashboard')."))
    token_prefix = models.CharField(max_length=TOKEN_PREFIX_LENGTH, blank=True, editable=False, verbose_name=_("Token Prefix"), help_text=_("First 8 characters of the key, for identification. Not secret."))
    token_digest = models.CharField(max_length=64, unique=True, blank=True, editable=False, verbose_name=_("Token Digest"), help_text=_("SHA-256 hash of the secret key. The raw key is never stored — it is shown only once, at creation."))
    # settings.AUTH_USER_MODEL (not a hard-coded auth.User) so projects with a
    # custom user model can use the package.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens", verbose_name=_("Owner"))
    expiration_date = models.DateTimeField(null=True, blank=True, verbose_name=_("Expiration Date"), help_text=_("Leave blank for a token that never expires."))
    allowed_models = models.JSONField(default=list, blank=True, validators=[validate_allowed_models], verbose_name=_("Allowed Models"), help_text=_("List of 'app_label.ModelName' strings this token can access."))
    allowed_scopes = models.JSONField(default=list, blank=True, validators=[validate_allowed_scopes], verbose_name=_("Allowed Scopes"), help_text=_("Free-form strings a project's own views may check with token_has_scope() — SnapAdmin stores and exposes them, the meaning is the project's. Empty denies every scope check (fail-closed), unlike an empty Allowed Models."))
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"), help_text=_("Inactive tokens are rejected without being deleted."))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Last Used At"))

    class Meta:
        verbose_name = _("API Token")
        verbose_name_plural = _("API Tokens")
        ordering = ["-created_at"]
        permissions = [
            # Unlocks unmasked PII in the admin + REST API (see snapadmin.masking).
            ("view_raw_pii", _("Can view unmasked PII data")),
        ]

    def __str__(self) -> str:
        return f"{self.token_name} ({self.user.get_username()})"

    @property
    def token_key(self) -> str | None:
        """The raw secret key.

        Available only on the instance that just created the token; it is hashed
        at rest and never reloaded, so this returns ``None`` for a token fetched
        from the database. Use :attr:`token_prefix` to identify stored tokens.
        """
        return self._raw_token_key

    def save(self, *args, **kwargs):
        # Mint and hash the key on first save (covers create_for_user,
        # objects.create, and a bare APIToken(...).save() from the admin).
        if not self.token_digest:
            raw_key = _generate_token_key()
            self._raw_token_key = raw_key
            self.token_prefix = raw_key[:TOKEN_PREFIX_LENGTH]
            self.token_digest = hash_token_key(raw_key)
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        if self.expiration_date is None:
            return False
        return timezone.now() > self.expiration_date

    @property
    def is_valid(self) -> bool:
        return self.is_active and not self.is_expired

    def can_access_model(self, app_label: str, model_name: str) -> bool:
        """Whether this token may target ``app_label.ModelName``.

        An **empty** ``allowed_models`` is *not* unrestricted access: it means
        "any model the owning user already has Django permissions for". The token
        scope is always AND-ed with ``user.has_perm`` (see
        ``snapadmin.api.authentication.token_has_permission``), so an empty list
        delegates entirely to the user's permissions. A **non-empty** list
        further narrows access to exactly those entries.
        """
        if not self.allowed_models: return True
        return f"{app_label}.{model_name}" in self.allowed_models

    def touch(self) -> None:
        APIToken.objects.filter(pk=self.pk).update(last_used_at=timezone.now())

    def rotate(self, request=None) -> str:
        """Replace the secret in place: same row, id, scopes and history.

        Clearing ``token_digest`` before saving re-triggers the same minting
        branch :meth:`save` already uses on first create, so create and
        rotate can never mint a key two different ways. The new raw key is
        returned exactly once — the way creation does — and the old key
        stops authenticating immediately, since a lookup is by digest, not
        against a list of historically valid keys.

        Written to the audit trail as an update (never the raw key — only
        the prefix, which is not secret). ``request`` threads through when
        the caller has one (the ``rotate`` viewset action always does);
        called as a bare model method with no request, the row still
        records the rotation, just with no identifiable actor.
        """
        old_prefix = self.token_prefix
        self.token_digest = ""
        self.save(update_fields=["token_prefix", "token_digest"])

        from snapadmin import audit
        audit.record_audit(
            request, audit.UPDATE, self,
            {"token_digest": {"old": f"prefix {old_prefix}", "new": f"prefix {self.token_prefix}"}},
        )
        return self.token_key

    @classmethod
    def create_for_user(
        cls,
        user: AbstractBaseUser,
        token_name: str,
        allowed_models: list[str] | None = None,
        allowed_scopes: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> "APIToken":
        expiration_date = None
        if expires_in_days is not None:
            expiration_date = timezone.now() + timedelta(days=expires_in_days)
        return cls.objects.create(
            user=user,
            token_name=token_name,
            allowed_models=allowed_models or [],
            allowed_scopes=allowed_scopes or [],
            expiration_date=expiration_date,
        )

# ===========================================================================
# Error Monitoring
# ===========================================================================

ERROR_MESSAGE_MAX_LENGTH = 2000
ERROR_TRACEBACK_MAX_LENGTH = 10000


class ErrorEvent(models.Model):
    """One server-side error captured by ``SnapErrorMonitorMiddleware``.

    Events feed the 15-minute spike alert and the daily grouped digest email
    (see :mod:`snapadmin.monitoring`). Rows are purged automatically after
    ``SNAPADMIN_ERROR_RETENTION_DAYS`` by the digest task.
    """

    exception_class = models.CharField(max_length=255, verbose_name=_("Exception"), help_text=_("Exception class name, or HTTP<code> for a 5xx response without an exception."))
    message = models.TextField(blank=True, verbose_name=_("Message"))
    path = models.CharField(max_length=500, blank=True, verbose_name=_("Path"))
    method = models.CharField(max_length=10, blank=True, verbose_name=_("Method"))
    status_code = models.PositiveIntegerField(default=500, verbose_name=_("Status Code"))
    fingerprint = models.CharField(max_length=64, db_index=True, blank=True, verbose_name=_("Fingerprint"), help_text=_("SHA-256 of exception class + path — groups repeats of the same error."))
    traceback = models.TextField(blank=True, verbose_name=_("Traceback"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Occurred At"))

    class Meta:
        verbose_name = _("Error Event")
        verbose_name_plural = _("Error Events")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.exception_class} @ {self.path or '—'}"

    @staticmethod
    def fingerprint_for(exception_class: str, path: str) -> str:
        return hashlib.sha256(f"{exception_class}|{path}".encode("utf-8")).hexdigest()

    @classmethod
    def record(
        cls,
        *,
        exception_class: str,
        message: str = "",
        path: str = "",
        method: str = "",
        status_code: int = 500,
        traceback_text: str = "",
    ) -> "ErrorEvent":
        """Persist one event, truncating unbounded inputs to safe lengths."""
        return cls.objects.create(
            exception_class=exception_class[:255],
            message=message[:ERROR_MESSAGE_MAX_LENGTH],
            path=path[:500],
            method=method[:10],
            status_code=status_code,
            fingerprint=cls.fingerprint_for(exception_class[:255], path[:500]),
            traceback=traceback_text[:ERROR_TRACEBACK_MAX_LENGTH],
        )


class SnapadminAuditLog(models.Model):
    """An append-only record of one administrative create/update/delete.

    Written by :func:`snapadmin.audit.record_audit` for actions performed through
    a SnapAdmin-generated admin. Rows are **immutable**: ``save`` refuses to
    update a persisted row and ``delete`` refuses outright, so the trail cannot be
    edited or single-object-deleted through the ORM (the admin is read-only too).
    Retention pruning uses ``QuerySet.delete()``, which bypasses the instance
    guard by design; for defence against direct DB tampering, add a database
    trigger / append-only role on top.
    """

    class Action(models.TextChoices):
        CREATE = "create", _("Created")
        UPDATE = "update", _("Updated")
        DELETE = "delete", _("Deleted")

    action = models.CharField(max_length=16, choices=Action.choices, db_index=True, verbose_name=_("Action"))
    # actor keeps referential integrity but survives user deletion via actor_repr.
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=_("Actor"))
    actor_repr = models.CharField(max_length=255, blank=True, verbose_name=_("Actor (snapshot)"))
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_("IP Address"))
    user_agent = models.TextField(blank=True, verbose_name=_("User Agent"))
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, verbose_name=_("Content Type"))
    # app_label/model are snapshots so SIEM filtering survives content-type loss.
    app_label = models.CharField(max_length=100, blank=True, db_index=True, verbose_name=_("App Label"))
    model = models.CharField(max_length=100, blank=True, db_index=True, verbose_name=_("Model"))
    object_id = models.CharField(max_length=255, blank=True, verbose_name=_("Object ID"))
    object_repr = models.CharField(max_length=255, blank=True, verbose_name=_("Object"))
    changes = models.JSONField(null=True, blank=True, verbose_name=_("Changes"), help_text=_("Before/after field diff, if any."))
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Timestamp"))

    class Meta:
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"{self.get_action_display()} {self.object_repr} by {self.actor_repr or 'anonymous'}"

    def save(self, *args, **kwargs):
        # Append-only: a persisted row (pk already set) can never be re-saved.
        if self.pk is not None:
            raise ValidationError(_("Audit log entries are immutable and cannot be modified."))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Audit log entries are immutable and cannot be deleted."))

    # GDPR retention (#RET2a). SnapadminAuditLog is deliberately not a SnapModel
    # and never registered (see the class docstring and snap_model()'s "no
    # retention purge" note) — registering it would also expose it through the
    # dynamic REST/GraphQL API and the offline cache, which is not what this
    # model is for. So it does not carry data_retention_days as a plain class
    # attribute the way SnapModel.purge_expired() reads one; instead it is read
    # live here, and snapadmin.tasks.purge_expired_data() calls this classmethod
    # directly (an explicit, additive call, not the apps.get_models() sweep).
    data_retention_field = "timestamp"

    @classmethod
    def data_retention_days(cls) -> int:
        """``SNAPADMIN_AUDIT_RETENTION_DAYS``, read live (not frozen at import time).

        Defaults to 365 — the value this setting has always documented, now
        actually enforced by an unattended purge instead of only by
        ``snapadmin_audit_export --purge``. Set to ``0`` or a negative number to
        disable automatic purging (the export command's own ``--purge`` still
        works, since it reads the same setting independently).
        """
        return int(get_setting("SNAPADMIN_AUDIT_RETENTION_DAYS", 365))

    @classmethod
    def purge_expired(cls, *, now=None, dry_run: bool = False) -> int:
        """Delete audit rows past :meth:`data_retention_days` (GDPR).

        Mirrors :meth:`SnapModel.purge_expired` for the one built-in model that
        is not a ``SnapModel``. ``QuerySet.delete()`` bypasses the append-only
        guard on :meth:`delete` by design — retention pruning is the one
        sanctioned way to remove a row, never a single-object delete.
        """
        retention_days = cls.data_retention_days()
        if retention_days <= 0:
            return 0
        now = now or timezone.now()
        cutoff = now - timedelta(days=retention_days)
        qs = cls.objects.filter(timestamp__lt=cutoff)
        if dry_run:
            return qs.count()
        count = qs.count()
        qs.delete()  # sanctioned bypass of the append-only guard — see above
        return count


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


# ===========================================================================
# Enums & Helpers
# ===========================================================================

class SnapModelAttributeEnum(str, Enum):
    ADMIN_OVERRIDES = "admin_overrides"


class SnapPurgeError(Exception):
    """Raised when a GDPR purge cannot be fully applied across every storage layer.

    Typically means the primary database delete succeeded but a secondary
    store (e.g. the Elasticsearch mirror) could not be cleared — the caller
    must not treat the affected model as cleanly purged.
    """


class SnapEsUnavailable(Exception):
    """Raised by an ES query method when Elasticsearch cannot answer and the
    caller has opted out of the database fallback (``db_fallback=False`` or
    ``SNAPADMIN_ES_DB_FALLBACK=False``).

    Signals that a DUAL model's Elasticsearch backend is disabled or erroring,
    so the query would otherwise have run its (potentially unscalable) database
    equivalent — a full-table ``GROUP BY`` or an unbounded ``.iterator()``. The
    original Elasticsearch error, when there was one, is chained as ``__cause__``.
    ES_ONLY models never raise this (they have no database to fall back to), and
    DB_ONLY models never raise it (the database is their primary store).
    """


class EsStorageMode(str, Enum):
    """Modes for Elasticsearch integration."""

    DB_ONLY = "db_only"  # Standard Django behavior
    DUAL = "dual"        # Save to both DB and ES, search via ES
    ES_ONLY = "es_only"  # Save/retrieve only via ES, no DB table needed


# ES field types that support exact (term/terms) matching directly. Analysed
# ``text`` is deliberately excluded — a term query against it matches individual
# analysed tokens, not the stored value, which is almost never what a caller of
# es_filter() intends; text fields are routed to their keyword sub-field instead.
_ES_EXACT_FILTER_TYPES = frozenset({
    "keyword", "constant_keyword", "wildcard",
    "boolean",
    "long", "integer", "short", "byte",
    "double", "float", "half_float", "scaled_float", "unsigned_long",
    "date", "date_nanos",
    "ip", "version",
})
# Sub-field types under a ``text`` field's ``fields`` that a term filter can use.
_ES_KEYWORD_SUBFIELD_TYPES = frozenset({"keyword", "constant_keyword", "wildcard"})
# Default number of buckets returned per ``es_aggregate`` facet (ES's own
# ``terms`` aggregation default), overridable per call via ``size=``.
_ES_DEFAULT_AGG_SIZE = 10


class EsQuerySet:
    """A lightweight mock QuerySet for Elasticsearch-only models."""

    def __init__(self, model, hits=None, filters=None):
        from django.db.models.sql.query import Query
        self.model = model
        self._hits = hits if hits is not None else []
        # Every field=value filter chained onto this queryset so far — kept
        # even though filter() already narrowed _hits, because get() below
        # bypasses _hits entirely (it fetches straight from ES by pk) and
        # must re-check this dict to avoid returning a hit outside the
        # already-applied filter (see get()'s docstring).
        self._filters = dict(filters) if filters else {}
        self.query = Query(model)  # Mock query for DRF
        self._result_cache = self._hits
        self._prefetch_related_lookups = []
        self._sticky_filter = False
        self._for_write = False
        self._prefetch_done = False
        self._known_related_objects = {}

    def __iter__(self):
        return iter(self._hits)

    def __len__(self):
        return len(self._hits)

    def __getitem__(self, k):
        if isinstance(k, slice):
            return self._clone(self._hits[k])
        return self._hits[k]

    def count(self):
        return len(self._hits)

    def delete(self):
        if self.model.es_storage_mode == EsStorageMode.ES_ONLY:
            try:
                es = self.model.get_es_client()
                for hit in self._hits:
                    es.delete(index=self.model.get_es_index_name(), id=hit.pk, ignore=[404])
            except Exception as exc:
                logger.warning(
                    "es_queryset_delete_failed",
                    model=self.model.__name__,
                    hit_count=len(self._hits),
                    error=str(exc),
                )
        return len(self._hits), {self.model._meta.label: len(self._hits)}

    def filter(self, *args, **kwargs):
        if not kwargs:
            return self

        new_hits = []
        for hit in self._hits:
            match = True
            for key, val in kwargs.items():
                # Handle simple filter: field=value
                if getattr(hit, key, None) != val:
                    match = False
                    break
            if match:
                new_hits.append(hit)
        return self._clone(new_hits, filters={**self._filters, **kwargs})

    def exclude(self, *args, **kwargs):
        return self

    def order_by(self, *field_names):
        return self

    def select_related(self, *fields):
        return self

    def prefetch_related(self, *lookups):
        return self

    def _clone(self, hits=None, filters=None):
        return EsQuerySet(
            self.model,
            hits if hits is not None else self._hits,
            filters if filters is not None else self._filters,
        )

    def using(self, alias):
        return self

    def none(self):
        return self._clone([])

    def all(self):
        return self

    def get(self, *args, **kwargs):
        """Fetch one document by pk directly from Elasticsearch.

        Bypasses ``_hits`` entirely (a direct ES lookup, not a scan of an
        already-fetched page), so any ``filter()`` chained onto this
        queryset before ``get()`` — most importantly a tenant-scoping filter
        (see ``snapadmin.tenancy.scope_queryset``) — is re-checked against
        the fetched document here via ``_filters``. Without this, a caller
        holding a *filtered* queryset could still ``.get(pk=...)`` a
        document the filter excluded, since ES itself was never asked about
        the filter at all.
        """
        pk = kwargs.get("pk") or kwargs.get("id")
        if pk:
            try:
                es = self.model.get_es_client()
                hit = es.get(index=self.model.get_es_index_name(), id=str(pk))
                data = hit["_source"]
                obj = self.model(**{k: v for k, v in data.items() if k != "id"})
                obj.pk = data.get("id")
            except Exception as exc:
                # A connection failure surfaces as DoesNotExist to the caller —
                # log the real cause so outages aren't mistaken for missing rows.
                logger.warning(
                    "es_get_failed",
                    model=self.model.__name__,
                    pk=pk,
                    error=str(exc),
                )
                raise self.model.DoesNotExist
            for key, val in self._filters.items():
                if getattr(obj, key, None) != val:
                    raise self.model.DoesNotExist
            return obj
        raise self.model.DoesNotExist

    def first(self):
        """The first hit, or ``None`` — matches ``QuerySet.first()``'s contract."""
        return self._hits[0] if self._hits else None

    def last(self):
        """The last hit, or ``None`` — matches ``QuerySet.last()``'s contract."""
        return self._hits[-1] if self._hits else None

    # ------------------------------------------------------------------
    # Async counterparts (#PROP1b). A real Django QuerySet gets aget/afirst/
    # alast for free from Django itself; EsQuerySet is a standalone mock (it
    # does not subclass QuerySet), so it needs its own thin async wrappers
    # over the sync methods above — the same pattern Django's own QuerySet
    # uses internally.
    # ------------------------------------------------------------------

    async def aget(self, *args, **kwargs):
        return await sync_to_async(self.get, thread_sensitive=True)(*args, **kwargs)

    async def afirst(self):
        return await sync_to_async(self.first, thread_sensitive=True)()

    async def alast(self):
        return await sync_to_async(self.last, thread_sensitive=True)()

    def exists(self) -> bool:
        return bool(self._hits)

    @property
    def ordered(self) -> bool:
        return True


class EsManager(models.Manager):
    """Manager that uses Elasticsearch for ES_ONLY models."""

    def get_queryset(self):
        # The single point every SnapModel query — the admin, the REST/GraphQL
        # APIs, the offline cache, purge, import's duplicate-key lookup —
        # funnels through, so it is where tenant scoping (#FUT1) is enforced
        # once for all of them. A no-op for a model that never opted in
        # (tenant_scoped = False, the default); see snapadmin.tenancy.
        from snapadmin.tenancy import scope_queryset

        if getattr(self.model, "es_storage_mode", None) == EsStorageMode.ES_ONLY:
            limit = get_setting("SNAPADMIN_ES_SEARCH_LIMIT", 1000)
            qs = self.model.es_search(limit=limit)
            if not isinstance(qs, EsQuerySet):
                qs = EsQuerySet(self.model, [])
            return scope_queryset(self.model, qs)
        # No default ordering is injected here. A default ``order_by("-pk")`` on
        # the base manager leaks into ``GROUP BY`` for ``.values().annotate()``
        # aggregations (Django appends ordering columns to the GROUP BY), which
        # silently returns one row per pk instead of per group. The "-pk" newest-
        # first default is applied in the presentation layers that need a stable
        # order instead (admin changelist ``ordering`` and the API list view).
        return scope_queryset(self.model, super().get_queryset())


class DjangoAdminClassAttributeEnum(str, Enum):
    FIELDS = "fields"
    FIELDSETS = "fieldsets"
    LIST_DISPLAY = "list_display"
    SEARCH_FIELDS = "search_fields"
    LIST_FILTER = "list_filter"
    AUTOCOMPLETE_FIELDS = "autocomplete_fields"
    MEDIA_CLASS = "Media"
    CSS_MEDIA = "css"
    JS_MEDIA = "js"
    ALL_MEDIA = "all"
    INLINES = "inlines"

@admin.display(description="ID")
def formatted_id(obj):
    pk = obj.pk
    # Only integer PKs get the zero-padded "000123" treatment. UUID/char/composite
    # PKs are rendered verbatim so the column never crashes on a non-int id.
    if isinstance(pk, int):
        raw = f"{pk:06d}"
        significant_start = next((i for i, ch in enumerate(raw) if ch != "0"), len(raw))
        leading = raw[:significant_start]
        number = raw[significant_start:] or "0"
        val = mark_safe(f'<span class="faded-zeros">{leading}</span>{number}')
    else:
        val = mark_safe(str(pk))
    if UNFOLD_INSTALLED:
        return [val, None, None]
    return val


class AdminFieldSets(NamedTuple):
    """:meth:`SnapModel.get_admin_fields`'s return shape (#ADM2b).

    A plain 5-tuple by construction, so existing positional unpacking,
    indexing and ``len()`` all keep working unchanged; new code can use the
    named members instead. Adding a sixth member remains a breaking change —
    the point of pinning this shape is that such a change is now announced
    (release notes, this docstring) rather than discovered as a silent
    ``ValueError`` at admin autodiscover.
    """
    form_fields: list[str]
    list_display: list[str]
    search_fields: list[str]
    list_filter: list[Any]
    autocomplete_fields: list[str]


def _any_offline_capable_model() -> bool:
    """Whether any Snap-registered model has ``offline_mode = True`` (#JS2e).

    ``connectivity.js`` is only worth loading when at least one model actually
    has an offline layer for it to drive — otherwise it is a health poll and a
    save-blocking guard with nothing behind them. Reads ``get_model_meta`` so a
    plain ``@snap_model``-decorated model (which accepts ``offline_mode`` as a
    decorator keyword rather than a class attribute) counts too.
    """
    return any(
        get_model_meta(model, "offline_mode", False)
        for model in apps.get_models()
        if is_registered(model)
    )

# ===========================================================================
# Admin Mixin
# ===========================================================================

class PIIMaskingAdminMixin:
    """Masks configured PII fields in the admin for users without PII access.

    * **Changelist:** masked columns are swapped for request-bound display
      callables that obfuscate the value — thread-safe, since the request is
      passed into ``get_list_display``.
    * **Change form:** masked fields are dropped from the form for unprivileged
      users (see the generated ``get_fieldsets``), so raw PII is never rendered
      in an editable input.

    Privileged users (superusers, ``snapadmin.view_raw_pii`` holders) see raw
    values in both views. Uses :mod:`snapadmin.masking`, so a field configured
    in ``SNAPADMIN_MASKING_RULES`` is obfuscated by its own rule here too.
    """

    def _snap_masked_fields(self) -> list[str]:
        from snapadmin.masking import get_masked_fields
        return get_masked_fields(self.model._meta.app_label, self.model._meta.model_name)

    def _snap_mask_column(self, field_name, user=None):
        from snapadmin.masking import mask_field

        opts = self.model._meta

        def column(obj):
            return mask_field(opts.app_label, opts.model_name, field_name,
                              getattr(obj, field_name, None), user)

        column.short_description = field_name.replace("_", " ").title()
        column.__name__ = f"masked_{field_name}"
        return column

    def get_list_display(self, request):
        from snapadmin.masking import user_can_view_pii

        display = list(super().get_list_display(request))
        masked = set(self._snap_masked_fields())
        if not masked or user_can_view_pii(request.user):
            return display
        return [
            self._snap_mask_column(name, request.user) if name in masked else name
            for name in display
        ]


class SnapSaveMixin:
    """``ModelAdmin`` mixin that writes an audit-trail entry on every admin save.

    Applied automatically to the admins :meth:`SnapModel.register_all_admins`
    generates. Add it to a hand-written ``ModelAdmin`` to get the same trail::

        class OrderAdmin(SnapSaveMixin, admin.ModelAdmin):
            ...

    A create records the initial field values; an edit records only what changed.
    """

    def _stamp_tenant(self, obj) -> None:
        """Force a tenant-scoped model's tenant column to this request's bound
        tenant on create (#FUT1b).

        The generated admin form never exposes the tenant column (it carries
        no Snap field ``show_in_form`` flag — see ``get_admin_fields()`` —
        by design, the same reasoning as the REST/GraphQL write paths:
        assignment is server-side only), so nothing else would ever set it.
        Refuses the save outright when no tenant is bound rather than
        silently creating a row nobody can ever see again — the row would
        otherwise carry an empty tenant value that matches no tenant's
        filter, an orphan indistinguishable from a bug.
        """
        from django.core.exceptions import PermissionDenied

        from snapadmin.tenancy import ALL_TENANTS, get_current_tenant, is_tenant_scoped, tenant_field_name

        model = type(obj)
        if not is_tenant_scoped(model):
            return
        current = get_current_tenant()
        if current is None or current is ALL_TENANTS:
            raise PermissionDenied(
                f"No tenant is bound to this request — {model._meta.label} is "
                "tenant-scoped and refuses to create a row with no tenant assigned."
            )
        setattr(obj, tenant_field_name(model), current)

    def save_model(self, request, obj, form, change):
        if not change:
            self._stamp_tenant(obj)
            super().save_model(request, obj, form, change)
            # Audit trail: snapshot the created field values.
            from snapadmin import audit
            created = {
                name: {"old": None, "new": audit.format_value(form.cleaned_data.get(name))}
                for name in form.cleaned_data
            }
            audit.record_audit(request, audit.CREATE, obj, created or None)
            return
        change_lines = []
        changes = {}
        for field_name in form.changed_data:
            old_val = form.initial.get(field_name)
            new_val = form.cleaned_data.get(field_name)
            if old_val != new_val:
                verbose = _(self.model._meta.get_field(field_name).verbose_name)
                change_lines.append(f"{verbose}: '{old_val}' -> '{new_val}'")
                from snapadmin import audit
                changes[field_name] = {
                    "old": audit.format_value(old_val),
                    "new": audit.format_value(new_val),
                }
        super().save_model(request, obj, form, change)
        if change_lines:
            LogEntry.objects.log_actions(
                user_id=request.user.id,
                queryset=[obj],
                action_flag=CHANGE,
                change_message="\n".join(change_lines),
                single_object=True,
            )
            # Remember we already wrote a granular "field: old -> new" entry for
            # this object so log_change() can suppress Django's generic duplicate.
            request._snap_logged_change = True
        if changes:
            from snapadmin import audit
            audit.record_audit(request, audit.UPDATE, obj, changes)

    def delete_model(self, request, obj):
        # Capture the object before it is gone.
        from snapadmin import audit
        audit.record_audit(request, audit.DELETE, obj, None)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # Bulk "delete selected" admin action — audit each row before deletion.
        from snapadmin import audit
        for obj in queryset:
            audit.record_audit(request, audit.DELETE, obj, None)
        super().delete_queryset(request, queryset)

    def log_change(self, request, obj, message):
        # Django's admin writes its own generic "Changed X." LogEntry after
        # save_model/save_related. When our save_model already logged the detailed
        # "field: old -> new" entry, that generic row is a duplicate (same object,
        # timestamp and user) — skip it. Otherwise fall back to the default entry
        # so changes we don't diff (e.g. M2M-only edits) still appear in history.
        if getattr(request, "_snap_logged_change", False):
            return None
        return super().log_change(request, obj, message)

    def save_related(self, request, form, formsets, change):
        for formset in formsets:
            if not formset.has_changed(): continue
            for related_form in formset.forms:
                try:
                    instance = related_form.instance
                    if not (instance.pk and related_form.has_changed()): continue
                    change_lines = []
                    for field_name in related_form.changed_data:
                        old_val = related_form.initial.get(field_name)
                        new_val = related_form.cleaned_data.get(field_name)
                        if old_val != new_val:
                            verbose = instance._meta.get_field(field_name).verbose_name
                            change_lines.append(f"{verbose}: '{old_val}' -> '{new_val}'")
                    if change_lines:
                        LogEntry.objects.log_actions(
                            user_id=request.user.id,
                            queryset=[instance],
                            action_flag=CHANGE,
                            change_message="\n".join(change_lines),
                            single_object=True,
                        )
                except Exception: pass
        super().save_related(request, form, formsets, change)

# ===========================================================================
# Base SnapModel
# ===========================================================================

class SnapModel(models.Model):
    """Abstract base model that generates the admin, the API and the search mapping.

    Subclass it instead of ``django.db.models.Model`` and declare ``Snap*Field``
    fields; the class attributes below then decide what SnapAdmin builds from them::

        from snapadmin import fields as snap, models as snap_models

        class Product(snap_models.SnapModel):
            name  = snap.SnapCharField(max_length=200, searchable=True)
            price = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)

            api_write_fields = ["name", "price"]   # everything else is read-only via the API

    One call in ``admin.py`` registers every subclass in the project::

        SnapModel.register_all_admins()

    The model is abstract and adds no columns of its own, so subclassing it costs
    no migration. The attributes are grouped as:

    * **Admin** — ``admin_enabled``, ``admin_sections``, ``admin_tabs``,
      ``snap_inlines``, ``admin_mixins`` (compose with third-party ``ModelAdmin``
      classes rather than replacing them), ``js_admin_files``/``css_admin_files``.
    * **API** — ``api_exclude_fields`` (never leaves the server),
      ``api_write_fields`` (mass-assignment allowlist), ``api_read_only`` /
      ``api_http_method_names`` (HTTP-method policy), ``api_filter_lookups`` /
      ``api_default_text_lookups`` / ``api_json_filters`` (generated query filters).
    * **Elasticsearch** — ``es_index_enabled``, ``es_storage_mode``,
      ``es_index_name``, ``es_mapping``.
    * **Compliance** — ``data_retention_days``/``data_retention_files`` for the
      GDPR purge.

    Each attribute is documented inline where it is declared below, and in full at
    https://drofji.github.io/django-snapadmin/#snap-model.
    """

    admin_enabled = True
    js_admin_files = []
    css_admin_files = []
    # Attributes/methods merged onto the generated ModelAdmin last, so they
    # always win over everything register_admin() itself produces (#ADM2a) —
    # e.g. {"list_per_page": 25} or a project's own get_readonly_fields.
    admin_overrides = {}
    snap_inlines = []
    admin_sections = []
    # Ecosystem compatibility: extra ModelAdmin base classes prepended
    # to the auto-generated admin, so third-party admin mixins compose with
    # SnapAdmin's config instead of replacing it — e.g.
    #   admin_mixins = [ImportExportModelAdmin]         # django-import-export
    #   admin_mixins = [reversion.admin.VersionAdmin]   # django-reversion
    #   admin_mixins = [SimpleHistoryAdmin]             # django-simple-history
    #   admin_mixins = [GuardedModelAdmin]              # django-guardian
    # Set admin_enabled = False instead to let a package fully own the admin.
    admin_mixins = []

    objects = EsManager()

    # Unfold specific
    compressed_fields = False
    warn_unsaved_form = True
    list_filter_submit = True
    admin_tabs = []

    # Elasticsearch integration
    es_index_enabled = False
    es_storage_mode = EsStorageMode.DB_ONLY
    es_index_name = None
    es_mapping = None

    # API field exposure control. Field names listed here are excluded from the
    # auto-generated REST serializer, the GraphQL object type and the
    # /api/models/schema/ introspection — use it for columns that must never
    # leave the server (internal notes, cost prices, soft-delete flags, …).
    # The admin is unaffected.
    api_exclude_fields: list[str] = []

    # API write allowlist. When set to a list, only the named fields accept a
    # client-supplied value on REST create/update — every other field becomes
    # read-only through the API (it may still be returned in responses, unless
    # also listed in api_exclude_fields). Use it to stop mass-assignment on
    # fields that must only ever change server-side (status flags, ownership
    # FKs, computed/internal columns). Left as None (the default), every
    # non-excluded field stays writable — a snapadmin.W004 system check warns
    # about this so the tradeoff is a deliberate choice, not an oversight.
    api_write_fields: list[str] | None = None

    # Per-model REST HTTP-method policy for the dynamic model API. By default a
    # SnapModel exposes full CRUD (list/retrieve/create/update/destroy). Two knobs
    # narrow that per model, without re-mounting routes:
    #
    #   api_read_only = True   -> only the safe read methods (GET/HEAD/OPTIONS) are
    #       allowed; POST/PUT/PATCH/DELETE answer 405 (no blank-row insert). For an
    #       import-only / reference table served read-only over the API.
    #   api_http_method_names  -> an explicit lowercase allowlist mirroring DRF's
    #       http_method_names, e.g. ["get", "post"]. HEAD and OPTIONS are always
    #       included. Takes precedence over api_read_only when both are set.
    #
    # Both default to today's full CRUD. A snapadmin.W007 system check flags a model
    # that is field-read-only (api_write_fields = []) yet still write-exposed,
    # nudging toward api_read_only.
    api_read_only: bool = False
    api_http_method_names: list[str] | None = None

    # Per-model override for the auto-generated REST API filters (see
    # snapadmin.api.filters). By default every text-type field (CharField,
    # TextField, EmailField, URLField, SlugField) exposes exact/icontains/
    # startswith/in lookups, with the bare ``?field=value`` query parameter
    # performing an *exact* match — index-usable, unlike the previous default
    # of an implicit substring (icontains) match on every text field, which
    # could not use an index and matched unrelated superstrings. Substring
    # search stays available via the explicit ``?field__icontains=value``
    # suffix. Set a field's lookup list here to widen or narrow that default
    # for one field on one model, e.g.
    #   api_filter_lookups = {"name": ["exact", "icontains"]}
    # Left as None (the default), every text field uses the model/project/library
    # default lookup set (see api_default_text_lookups below).
    api_filter_lookups: dict[str, list[str]] | None = None

    # Model-wide default lookup set for *every* text field, applied to any field
    # not named in api_filter_lookups. Use it to change the posture for a whole
    # model at once instead of enumerating each column — e.g. drop the
    # non-indexable ``icontains`` on a large table:
    #   api_default_text_lookups = ["exact", "startswith", "in"]
    # Precedence (first non-None wins): per-field api_filter_lookups → this
    # attribute → the project-wide SNAPADMIN_API_TEXT_LOOKUPS setting → the
    # library default (exact/icontains/startswith/in). Left as None (the default),
    # the project setting or library default applies.
    api_default_text_lookups: list[str] | None = None

    # Auto-generated REST API filters for JSON columns. JSONField gets no filter
    # by default — declare which key-paths within which JSON field are filterable
    # and the dynamic API exposes each as a `<json_field>__<key_path>` query param
    # (dots in the key-path become double underscores), e.g.:
    #   api_json_filters = {"payload": ["a.b", "a.c"]}
    # exposes ?payload__a__b=value and ?payload__a__c=value. A match covers both a
    # scalar value equal to `value` and, when the JSON value at that path is a
    # list, list-membership (does the list contain `value`). JSON columns carry no
    # index, so these filters always run as a full table scan — for filtering at
    # scale on large tables, use SnapModel.es_search() (Elasticsearch integration)
    # instead. Left as None (the default), no JSON key-path filters are exposed.
    api_json_filters: dict[str, list[str]] | None = None

    # Optional index-level settings applied when the ES index is first created —
    # e.g. custom analyzers under "analysis", "number_of_shards", "number_of_replicas".
    # Existing indexes are never altered (most index settings are static in ES);
    # to apply a change, delete the index and run es_reindex_all().
    es_index_settings: dict | None = None

    # Automatic ES mapping derivation. When True, the index mapping is derived
    # from the model's concrete fields — Char/Text → text with a ".raw" keyword
    # subfield (exact match / aggregations), Email/Slug/URL/UUID/IP/File →
    # keyword, integers/FK → long, Float → double, Decimal → scaled_float,
    # Date/DateTime → date, Boolean → boolean, JSON → object. Entries declared
    # in es_mapping override or extend the derived ones, so you only write
    # mappings for the fields that need something special.
    es_auto_mapping: bool = False

    # Automatic ES query routing for the REST API (DUAL mode only).
    # When True (default) and the model's data is mirrored in Elasticsearch
    # (es_storage_mode = DUAL), full-text `?search=` API requests are executed
    # against ES (fuzzy, relevance-ranked) instead of DB `icontains` — plain
    # listings and filters stay on the database. Set False to keep every API
    # query on the DB for this model; the global kill-switch is the
    # SNAPADMIN_ES_QUERY_ROUTING setting.
    es_query_routing: bool = True

    # GDPR data retention
    # Set data_retention_days to a positive integer to enable automatic deletion of old records.
    # Records older than this many days (measured on data_retention_field) will be removed.
    data_retention_days: int | None = None
    data_retention_field: str = "created_at"
    # Storage-backed field names (SnapFileField / SnapImageField) whose files are
    # deleted alongside an expiring row, so a purged row never leaves an orphaned
    # file behind on disk — see purge_expired()'s "files before rows" ordering
    # and the shared-file skip rule documented there. None (the default) purges
    # rows only, exactly today's behaviour.
    data_retention_files: list[str] | None = None

    # Multi-tenancy (#FUT1). Set tenant_scoped = True and add a tenant column
    # (snapadmin.tenancy.tenant_field()) to opt this model into row-level
    # tenant isolation. Once set, every generated surface — the admin, the
    # REST/GraphQL APIs, Elasticsearch routing, exports, imports, the offline
    # cache — requires a bound tenant context (snapadmin.tenancy.use_tenant())
    # to see or write any row; with none bound, every read returns empty and
    # every write is refused, never "every row" (default-deny). Isolation is
    # *logical*, not physical — one query path that bypasses the manager still
    # leaks; see SECURITY.md. False (the default) leaves a model exactly as
    # before: tenant scoping is opt-in, never retrofitted onto an existing
    # project's models. See snapadmin.tenancy for the full mechanism.
    tenant_scoped: bool = False
    # Name of the tenant column, resolved through get_model_meta() the same
    # way every other name here is — so a decorated plain model, or a project
    # setting, can override it too. None (the default) resolves to "tenant_id".
    tenant_field: str | None = None

    # Offline mode
    # Set offline_mode = True to enable client-side caching (IndexedDB) of this model's
    # admin list view. When the browser loses connectivity, a red offline banner appears
    # and the last cached rows are shown; the cache is refreshed and queued changes are
    # synced automatically once the connection is restored.
    offline_mode: bool = False

    # How many of the most-recent rows (ordered by -pk) to prefetch and cache for
    # offline viewing. The offline-data endpoint clamps any client-supplied ?limit=
    # to this value and uses it as the default. Raise it for models you want fully
    # browsable offline; lower it on very wide rows to keep IndexedDB small.
    offline_cache_limit: int = 100

    # Large-dataset / performance tuning
    # These map straight onto Django admin's list-view knobs. The defaults match
    # Django's own, but SnapModel also auto-derives `list_select_related` from the
    # ForeignKey columns shown in the list view (see register_admin) so related
    # columns never trigger N+1 queries — no manual configuration required.
    #
    # list_per_page         — rows per page in the admin list view.
    # list_max_show_all     — cap for the "Show all" link (guards huge tables).
    # show_full_result_count — when False, the admin skips the second, unfiltered
    #   COUNT(*) it normally runs to display "X total"; on multi-million-row tables
    #   that full count is the single most expensive query, so disable it there.
    list_per_page: int = 100
    list_max_show_all: int = 200
    show_full_result_count: bool = True

    class Meta:
        abstract = True
        ordering = ["-pk"]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Enter every subclass into the SnapAdmin registry as it is declared.

        Runs inside ``ModelBase.__new__``, *before* Django attaches ``_meta``, so
        it may only record the class itself — no field or ``Meta`` inspection.
        Abstract intermediate bases are registered alongside concrete models,
        which is what the ``issubclass`` test this replaces already matched;
        ``apps.get_models()`` filters the abstract ones out at every call site.
        """
        super().__init_subclass__(**kwargs)
        register(cls)

    @classmethod
    def is_concrete_subclass(cls, model: type) -> bool:
        """Whether ``model`` is a SnapAdmin model (a ``SnapModel`` subclass, not the base).

        Kept as the historical name and signature; the answer now comes from
        :mod:`snapadmin.registry`, which ``__init_subclass__`` populates.
        """
        return is_registered(model)

    @classmethod
    def get_es_index_name(cls) -> str:
        return cls.es_index_name or f"snap_{cls._meta.app_label}_{cls._meta.model_name.lower()}"

    @classmethod
    def get_es_client(cls):
        """Build the Elasticsearch client for this model.

        Connection is configurable beyond the URL:

        - ``ELASTICSEARCH_KWARGS`` — dict merged into the ``Elasticsearch(...)``
          constructor (``api_key``, ``basic_auth``, ``ca_certs``,
          ``verify_certs``, ``retry_on_timeout``, ``max_retries``,
          ``request_timeout`` — overrides the default 5s — etc.).
        - ``SNAPADMIN_ES_CLIENT_FACTORY`` — dotted path to a zero-argument
          callable returning a ready client; takes precedence over everything
          else for fully custom setups (cloud_id, sniffing, custom transport).
        """
        factory_path = get_setting("SNAPADMIN_ES_CLIENT_FACTORY", None)
        if factory_path:
            from django.utils.module_loading import import_string

            factory = import_string(factory_path) if isinstance(factory_path, str) else factory_path
            return factory()

        from elasticsearch import Elasticsearch

        url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
        kwargs = {"request_timeout": 5, **getattr(settings, "ELASTICSEARCH_KWARGS", {})}
        return Elasticsearch([url], **kwargs)

    def api_can_delete(self, request) -> bool:
        """Per-object deletion veto for the REST API (default: allow).

        Consulted by the dynamic model API's ``DELETE`` handler *after* the
        normal model permission check. Override on a model to forbid deleting
        specific objects without re-mounting the API routes — e.g. protect
        superusers or "system" rows::

            class Account(SnapModel):
                def api_can_delete(self, request) -> bool:
                    return not self.is_system

        Returning ``False`` makes the endpoint respond ``403 Forbidden``. A
        project-wide guard can also be configured with the
        ``SNAPADMIN_API_DELETE_GUARD`` setting (a dotted path to a
        ``Callable[[request, obj], bool]``); both must allow the delete.
        """
        return True

    @staticmethod
    def _derive_es_field_mapping(field) -> dict | None:
        """Best-fit ES mapping for one Django model field (es_auto_mapping)."""
        # Most-specific classes first — Email/Slug/URL subclass CharField,
        # DateTimeField subclasses DateField, ImageField subclasses FileField.
        if isinstance(field, (
            models.EmailField,
            models.SlugField,
            models.URLField,
            models.UUIDField,
            models.GenericIPAddressField,
            models.FileField,
            models.DurationField,
            models.TimeField,
        )):
            return {"type": "keyword"}
        if isinstance(field, (models.CharField, models.TextField)):
            return {
                "type": "text",
                "fields": {"raw": {"type": "keyword", "ignore_above": 256}},
            }
        if isinstance(field, models.BooleanField):
            return {"type": "boolean"}
        if isinstance(field, models.DecimalField):
            return {"type": "scaled_float", "scaling_factor": 100}
        if isinstance(field, models.FloatField):
            return {"type": "double"}
        if isinstance(field, models.ForeignKey):  # covers OneToOneField
            return {"type": "long"}
        if isinstance(field, models.IntegerField):
            return {"type": "long"}
        if isinstance(field, models.DateTimeField) or isinstance(field, models.DateField):
            return {"type": "date"}
        if isinstance(field, models.JSONField):
            return {"type": "object"}
        return None

    @classmethod
    def get_es_mapping(cls) -> dict | None:
        """The effective ES mapping: explicit ``es_mapping``, optionally merged
        on top of the mapping auto-derived from model fields (``es_auto_mapping``)."""
        if not cls.es_auto_mapping:
            return cls.es_mapping

        derived: dict = {}
        for field in cls._meta.get_fields():
            if not getattr(field, "concrete", False) or field.many_to_many:
                continue
            if getattr(field, "primary_key", False):
                continue  # "id" is always mapped explicitly as the document id
            mapping = cls._derive_es_field_mapping(field)
            if mapping:
                derived[field.name] = mapping
        if cls.es_mapping:
            derived.update(cls.es_mapping)
        return derived

    def get_es_document(self) -> dict:
        doc = {"id": self.pk}
        mapping = type(self).get_es_mapping()
        if mapping:
            for field_name in mapping.keys():
                val = getattr(self, field_name, None)
                if hasattr(val, "pk"):
                    val = val.pk
                elif isinstance(val, (timedelta,)):
                    val = str(val)
                doc[field_name] = val
        return doc

    @classmethod
    def es_reindex_only_fields(cls) -> list[str] | None:
        """Field names a reindex queryset can ``.only(*fields)`` down to.

        :meth:`get_es_document` reads only the primary key plus the keys of
        :meth:`get_es_mapping`, so a bulk reindex has no need to fetch every column
        (a wide table's large ``TEXT`` bodies are dead weight). This returns the
        concrete local field names to load — the pk plus each mapped field — so the
        runner can restrict the queryset with ``.only()``.

        Returns ``None`` when the restriction can't be proven safe: a mapping key
        that isn't a concrete local field (a property, a relation-spanning path, a
        many-to-many) may itself read columns that ``.only()`` would defer, turning
        every row into an extra query. In that case the caller fetches all columns
        (today's behaviour). An empty mapping yields just the pk.
        """
        fields = [cls._meta.pk.name]
        mapping = cls.get_es_mapping()
        if not mapping:
            return fields
        for key in mapping:
            try:
                field = cls._meta.get_field(key)
            except FieldDoesNotExist:
                return None
            if not getattr(field, "concrete", False) or field.many_to_many:
                return None
            fields.append(field.name)
        return fields

    @classmethod
    def _ensure_es_index_and_mapping(cls):
        """
        Create index and update mapping if necessary. Called during post_migrate.
        """
        if not (cls.es_index_enabled or cls.es_storage_mode != EsStorageMode.DB_ONLY):
            return
        if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            return

        try:
            es = cls.get_es_client()
            index_name = cls.get_es_index_name()
            body = {"mappings": {"properties": {"id": {"type": "integer"}}}}
            effective_mapping = cls.get_es_mapping()
            if effective_mapping:
                body["mappings"]["properties"].update(effective_mapping)
            if cls.es_index_settings:
                # Index-level settings (analyzers, shards, …) apply on creation
                # only — put_mapping below cannot change them on a live index.
                body["settings"] = cls.es_index_settings

            if not es.indices.exists(index=index_name):
                es.indices.create(index=index_name, body=body)
            else:
                # Update existing mapping (only adds new fields)
                es.indices.put_mapping(index=index_name, body=body["mappings"])
        except Exception as exc:
            logger.warning(
                "es_ensure_index_failed",
                model=cls.__name__,
                index=cls.get_es_index_name(),
                error=str(exc),
            )

    def index_in_es(self) -> None:
        if (
            not (self.es_index_enabled or self.es_storage_mode != EsStorageMode.DB_ONLY)
            or not getattr(settings, "ELASTICSEARCH_ENABLED", False)
        ):
            return
        try:
            es = self.get_es_client()
            index_name = self.get_es_index_name()
            # Ensure index exists with mapping if provided
            self._ensure_es_index_and_mapping()
            es.index(index=index_name, id=self.pk, document=self.get_es_document())
        except Exception as exc:
            logger.warning(
                "es_index_document_failed",
                model=type(self).__name__,
                pk=self.pk,
                error=str(exc),
            )

    def delete_from_es(self) -> None:
        if (
            not (self.es_index_enabled or self.es_storage_mode != EsStorageMode.DB_ONLY)
            or not getattr(settings, "ELASTICSEARCH_ENABLED", False)
        ):
            return
        try:
            es = self.get_es_client()
            es.delete(index=self.get_es_index_name(), id=self.pk, ignore=[404])
        except Exception as exc:
            logger.warning(
                "es_delete_document_failed",
                model=type(self).__name__,
                pk=self.pk,
                error=str(exc),
            )

    @classmethod
    def _generate_es_only_pk(cls) -> int:
        # ES_ONLY models have no DB sequence, so we mint the id ourselves. A small
        # random range collides quickly (and would silently overwrite an existing ES
        # document), so we draw from the full 63-bit BigAutoField space and, when ES
        # is reachable, re-roll on the rare chance the id already exists.
        max_id = 9223372036854775807
        candidate = secrets.randbelow(max_id) + 1
        if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            return candidate
        try:
            es = cls.get_es_client()
            index_name = cls.get_es_index_name()
            for _attempt in range(5):
                if not es.exists(index=index_name, id=candidate):
                    return candidate
                candidate = secrets.randbelow(max_id) + 1
        except Exception as exc:
            logger.warning(
                "es_pk_existence_check_failed",
                model=cls.__name__,
                error=str(exc),
            )
        return candidate

    def save(self, *args, **kwargs):
        if self.es_storage_mode == EsStorageMode.ES_ONLY:
            # Skip DB save for ES_ONLY models
            if not self.pk:
                # Mint a collision-resistant id (no DB sequence exists for ES_ONLY).
                self.pk = self._generate_es_only_pk()
            self.index_in_es()
            return

        super().save(*args, **kwargs)
        if self.es_storage_mode == EsStorageMode.DUAL:
            self.index_in_es()

    def delete(self, *args, **kwargs):
        if self.es_storage_mode == EsStorageMode.ES_ONLY:
            # Skip DB delete for ES_ONLY models
            self.delete_from_es()
            return

        self.delete_from_es()  # For DUAL mode, ensure ES sync
        super().delete(*args, **kwargs)

    @classmethod
    def _es_search_fields(cls) -> list[str]:
        """Text-capable fields from ``es_mapping`` for full-text queries.

        Restricting the ``multi_match`` to text fields avoids ES parse errors
        when the mapping mixes in numeric/date/boolean fields. Falls back to
        ``["*"]`` when the mapping declares no text fields (or there is no
        mapping at all) so search keeps working on loosely-mapped indexes.
        """
        text_types = {"text", "match_only_text", "search_as_you_type"}
        fields = [
            name
            for name, mapping in (cls.get_es_mapping() or {}).items()
            if isinstance(mapping, dict) and mapping.get("type") in text_types
        ]
        return fields or ["*"]

    @staticmethod
    def _tag_search_backend(qs, backend: str):
        """Mark a search result with the backend that actually produced it.

        Read by the REST viewset for the ``X-Snap-Query-Backend`` header, so
        the header reflects reality even when ES failed and the DB fallback
        answered the query.
        """
        qs._snap_search_backend = backend
        return qs

    @classmethod
    def es_search(cls, query_string=None, limit=None):
        """
        Search for records. Uses Elasticsearch if enabled, falls back to DB.
        """
        limit = limit or 20
        use_es = (cls.es_index_enabled or cls.es_storage_mode != EsStorageMode.DB_ONLY) and getattr(
            settings, "ELASTICSEARCH_ENABLED", False
        )

        if use_es:
            try:
                es = cls.get_es_client()
                query = {
                    "multi_match": {
                        "query": query_string,
                        "fields": cls._es_search_fields(),
                        "fuzziness": "AUTO",
                        # Ignore type-mismatch parse errors (e.g. a text query
                        # hitting a numeric field) instead of failing the search.
                        "lenient": True,
                    }
                } if query_string else {"match_all": {}}
                query = cls._with_tenant_es_scope(query)
                response = es.search(
                    index=cls.get_es_index_name(),
                    body={
                        "query": query,
                        "size": limit,
                    },
                )
                hits = response.get("hits", {}).get("hits", [])

                if cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    # Return EsQuerySet built from ES data
                    results = []
                    for hit in hits:
                        data = hit["_source"]
                        obj = cls(**{k: v for k, v in data.items() if k != "id"})
                        obj.pk = data.get("id")
                        results.append(obj)
                    return cls._tag_search_backend(EsQuerySet(cls, results), "elasticsearch")

                pks = [hit["_source"]["id"] for hit in hits]
                preserved = models.Case(*[models.When(pk=pk, then=pos) for pos, pk in enumerate(pks)])
                return cls._tag_search_backend(
                    cls.objects.filter(pk__in=pks).order_by(preserved), "elasticsearch"
                )
            except Exception as exc:
                logger.warning(
                    "es_search_failed",
                    model=cls.__name__,
                    query=query_string,
                    fallback="empty" if cls.es_storage_mode == EsStorageMode.ES_ONLY else "db",
                    error=str(exc),
                )
                if cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    return cls._tag_search_backend(EsQuerySet(cls, []), "elasticsearch")

        # Fallback to DB search using search_fields (only for non-ES_ONLY)
        if cls.es_storage_mode == EsStorageMode.ES_ONLY:
            return cls._tag_search_backend(EsQuerySet(cls, []), "elasticsearch")

        query_string = query_string or ""
        search_fields = cls.get_admin_fields().search_fields
        q_objects = models.Q()
        for field in search_fields:
            if field == "id":
                try: q_objects |= models.Q(id=int(query_string))
                except ValueError: pass
                continue
            q_objects |= models.Q(**{f"{field}__icontains": query_string})

        if q_objects:
            return cls._tag_search_backend(cls.objects.filter(q_objects).distinct(), "database")
        return cls._tag_search_backend(cls.objects.all(), "database")

    @classmethod
    def snap_search(cls, query_string=None, limit=None):
        """Public alias for es_search — preferred entry point for external callers."""
        return cls.es_search(query_string=query_string, limit=limit)

    @classmethod
    def _resolve_es_term_field(cls, key: str) -> str:
        """Resolve an ``es_filter`` term key to its ES field path.

        Walks the effective ES mapping (:meth:`get_es_mapping`): a ``__`` in the
        key descends into an ``object``/``nested`` field's ``properties`` (so a
        JSON-mapped column can be filtered by key path, e.g. ``payload__status``),
        an exact-type leaf (keyword/boolean/numeric/date/ip) filters directly,
        and a ``text`` leaf is redirected to its keyword sub-field. Raises
        ``ValueError`` for an unknown field, a container without the requested
        sub-field, or an analysed ``text`` field that has no keyword sub-field.
        """
        node = cls.get_es_mapping() or {}
        parts = key.split("__")
        path: list[str] = []
        for i, part in enumerate(parts):
            if not isinstance(node, dict) or part not in node:
                resolved = ".".join(path + [part])
                raise ValueError(
                    f"{cls.__name__}.es_filter: unknown ES field {key!r} "
                    f"(no mapping for {resolved!r})"
                )
            field_def = node[part]
            path.append(part)
            if i < len(parts) - 1:
                node = field_def.get("properties")
                if node is None:
                    raise ValueError(
                        f"{cls.__name__}.es_filter: {'.'.join(path)!r} has no "
                        f"sub-fields to resolve {key!r}"
                    )
                continue
            ftype = field_def.get("type")
            if ftype in _ES_EXACT_FILTER_TYPES:
                return ".".join(path)
            if ftype == "text":
                subfields = field_def.get("fields") or {}
                for sub_name, sub_def in subfields.items():
                    if isinstance(sub_def, dict) and sub_def.get("type") in _ES_KEYWORD_SUBFIELD_TYPES:
                        return ".".join(path + [sub_name])
                raise ValueError(
                    f"{cls.__name__}.es_filter: field {key!r} is an analysed text "
                    f"field with no keyword sub-field; term filters need a keyword "
                    f"mapping (add fields={{'raw': {{'type': 'keyword'}}}} to its es_mapping)"
                )
            raise ValueError(
                f"{cls.__name__}.es_filter: field {key!r} of ES type {ftype!r} "
                f"is not term-filterable"
            )

    @classmethod
    def _tenant_es_term(cls) -> tuple[str, Any] | None:
        """``(es_field, value)`` enforcing this model's tenant scope on a direct
        Elasticsearch query, or ``None`` when nothing needs enforcing.

        The ES-native query methods (:meth:`es_search`, :meth:`es_filter`,
        :meth:`es_aggregate`, :meth:`es_count`, :meth:`es_scan`) build their
        own query body and, for ``ES_ONLY`` models, reconstruct objects
        straight from the ES response — they never reach
        :meth:`EsManager.get_queryset`'s scoping hook the way ``cls.objects``
        does. So the tenant constraint has to be forced into the query body
        itself here.

        ``None`` when :meth:`~snapadmin.tenancy.is_tenant_scoped` is false,
        or inside :func:`~snapadmin.tenancy.use_all_tenants`. With **no**
        tenant bound — including a tenant field this model's ES mapping does
        not carry at all — resolves to a term that can never match a real
        document (Elasticsearch's own ``_id`` field against a value no
        document will ever have), so Elasticsearch itself enforces the empty
        result: one fail-closed shape, not a second "return nothing" branch
        duplicated in every caller.
        """
        from snapadmin.tenancy import ALL_TENANTS, get_current_tenant, is_tenant_scoped, tenant_field_name

        if not is_tenant_scoped(cls):
            return None
        current = get_current_tenant()
        if current is ALL_TENANTS:
            return None
        if current is None:
            return ("_id", "__snapadmin_no_tenant_context__")
        field_name = tenant_field_name(cls)
        try:
            es_field = cls._resolve_es_term_field(field_name)
        except ValueError:
            logger.warning("tenant_field_not_es_mapped", model=cls.__name__, field=field_name)
            return ("_id", "__snapadmin_no_tenant_context__")
        return (es_field, current)

    @classmethod
    def _with_tenant_es_scope(cls, query: dict) -> dict:
        """Wrap a full-text ``query`` body (:meth:`es_search`'s shape) in the
        tenant filter clause from :meth:`_tenant_es_term`, unchanged when
        there is nothing to enforce."""
        term = cls._tenant_es_term()
        if term is None:
            return query
        field, value = term
        return {"bool": {"must": [query], "filter": [{"term": {field: value}}]}}

    @classmethod
    def _resolve_es_terms(cls, terms: dict) -> dict:
        """``**terms`` resolved through :meth:`_resolve_es_term_field`, with
        this model's tenant term (see :meth:`_tenant_es_term`) forced in —
        shared by :meth:`es_filter`, :meth:`es_aggregate`, :meth:`es_count`
        and :meth:`es_scan`. A caller-supplied term for the same ES field is
        overridden, never merged: tenant scoping is not something a query's
        own terms can widen.
        """
        resolved = {cls._resolve_es_term_field(key): value for key, value in terms.items()}
        tenant_term = cls._tenant_es_term()
        if tenant_term is not None:
            resolved[tenant_term[0]] = tenant_term[1]
        return resolved

    @classmethod
    def _es_db_fallback(cls, db_fallback: bool | None) -> bool:
        """Resolve the effective ES→DB fallback posture for a query method.

        ``None`` (the per-call default) defers to the project-wide
        ``SNAPADMIN_ES_DB_FALLBACK`` setting (itself defaulting to ``True`` =
        today's silent-fallback behaviour); an explicit ``bool`` overrides it.
        """
        if db_fallback is None:
            return bool(get_setting("SNAPADMIN_ES_DB_FALLBACK", True))
        return db_fallback

    @classmethod
    def _raise_es_unavailable(cls, method: str, cause: Exception | None) -> NoReturn:
        """Raise :class:`SnapEsUnavailable`, chaining the ES error when present."""
        raise SnapEsUnavailable(
            f"{cls.__name__}.{method}: Elasticsearch is unavailable and db_fallback is "
            f"disabled, so the database fallback was refused (pass db_fallback=True or set "
            f"SNAPADMIN_ES_DB_FALLBACK=True to allow it)"
        ) from cause

    @classmethod
    def es_filter(
        cls,
        *,
        query_string: str | None = None,
        limit: int | None = None,
        db_fallback: bool | None = None,
        **terms,
    ):
        """Structured Elasticsearch term filter — the counterpart to es_search().

        Each keyword argument is a term constraint on an ES-mapped field: a
        scalar builds a ``term`` clause, a list/tuple/set a ``terms`` clause, and
        every constraint runs in ES *filter* context (no relevance scoring,
        cacheable). Field names resolve through :meth:`_resolve_es_term_field`,
        so a ``text`` field targets its keyword sub-field automatically and a
        ``__`` path reaches into a JSON/``object`` mapping. An optional
        ``query_string`` is added as a scored ``must`` full-text ``multi_match``.

        Results mirror :meth:`es_search`: a pk-ordered database queryset for
        DUAL models, an :class:`EsQuerySet` of reconstructed objects for
        ES_ONLY. When Elasticsearch is disabled or the query fails, a DUAL model
        falls back to the equivalent database filter (failing closed to an empty
        result if a term field has no backing column); an ES_ONLY model returns
        an empty result.

        Set ``db_fallback=False`` to refuse that silent database fallback and
        raise :class:`SnapEsUnavailable` instead when Elasticsearch can't answer
        — the safe posture on a large, DB-unindexable table where the fallback
        scan is worse than a clear failure. ``None`` (the default) defers to the
        project-wide ``SNAPADMIN_ES_DB_FALLBACK`` setting (default ``True``).
        ES_ONLY models are unaffected (no database to fall back to).

        Raises ``ValueError`` for an unknown or non-term-filterable field, and
        :class:`SnapEsUnavailable` when ES is unavailable and the fallback is
        disabled.
        """
        # Resolve/validate every term up-front so a bad field raises regardless
        # of which backend ends up answering the query.
        resolved = cls._resolve_es_terms(terms)

        limit = limit or get_setting("SNAPADMIN_ES_SEARCH_LIMIT", 1000)
        fallback = cls._es_db_fallback(db_fallback)
        es_intended = cls.es_index_enabled or cls.es_storage_mode != EsStorageMode.DB_ONLY
        use_es = es_intended and getattr(settings, "ELASTICSEARCH_ENABLED", False)
        es_error: Exception | None = None

        if use_es:
            try:
                es = cls.get_es_client()
                response = es.search(
                    index=cls.get_es_index_name(),
                    body={
                        "query": cls._build_es_term_query(resolved, query_string),
                        "size": limit,
                    },
                )
                hits = response.get("hits", {}).get("hits", [])

                if cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    results = []
                    for hit in hits:
                        data = hit["_source"]
                        obj = cls(**{k: v for k, v in data.items() if k != "id"})
                        obj.pk = data.get("id")
                        results.append(obj)
                    return cls._tag_search_backend(EsQuerySet(cls, results), "elasticsearch")

                pks = [hit["_source"]["id"] for hit in hits]
                preserved = models.Case(*[models.When(pk=pk, then=pos) for pos, pk in enumerate(pks)])
                return cls._tag_search_backend(
                    cls.objects.filter(pk__in=pks).order_by(preserved), "elasticsearch"
                )
            except Exception as exc:
                logger.warning(
                    "es_filter_failed",
                    model=cls.__name__,
                    terms=list(terms),
                    fallback="empty" if cls.es_storage_mode == EsStorageMode.ES_ONLY
                    else ("db" if fallback else "raise"),
                    error=str(exc),
                )
                if cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    return cls._tag_search_backend(EsQuerySet(cls, []), "elasticsearch")
                es_error = exc

        # Database fallback — only meaningful for models with a table.
        if cls.es_storage_mode == EsStorageMode.ES_ONLY:
            return cls._tag_search_backend(EsQuerySet(cls, []), "elasticsearch")

        if es_intended and not fallback:
            cls._raise_es_unavailable("es_filter", es_error)

        try:
            qs = cls.objects.filter(**cls._es_orm_terms(terms))
            # Force field resolution now so an ES-only field (no DB column) fails
            # closed here rather than surprising the caller on later evaluation.
            str(qs.query)
        except FieldError:
            qs = cls.objects.none()
        return cls._tag_search_backend(qs, "database")

    @classmethod
    def _build_es_term_query(cls, resolved_terms: dict, query_string: str | None) -> dict:
        """Assemble the ES query body shared by es_filter/es_aggregate.

        ``resolved_terms`` maps already-resolved ES field paths to scalar (→
        ``term``) or list/tuple/set (→ ``terms``) values, run in filter context.
        A non-empty ``query_string`` is added as a scored ``must`` multi_match.
        """
        clauses = [
            ({"terms": {field: list(value)}}
             if isinstance(value, (list, tuple, set))
             else {"term": {field: value}})
            for field, value in resolved_terms.items()
        ]
        if query_string:
            return {"bool": {
                "filter": clauses,
                "must": [{
                    "multi_match": {
                        "query": query_string,
                        "fields": cls._es_search_fields(),
                        "fuzziness": "AUTO",
                        "lenient": True,
                    }
                }],
            }}
        if clauses:
            return {"bool": {"filter": clauses}}
        return {"match_all": {}}

    @classmethod
    def es_aggregate(
        cls,
        *fields: str,
        size: int | None = None,
        query_string: str | None = None,
        db_fallback: bool | None = None,
        **terms,
    ) -> dict[str, list[dict[str, object]]]:
        """Faceted ``terms`` aggregations — the counterpart to es_filter().

        For each positional ``fields`` name, run one Elasticsearch ``terms``
        aggregation and return its buckets as ``[{"key": …, "count": …}, …]``,
        keyed in the result by the original field name::

            Product.es_aggregate("status", "region", available=True)
            # {"status": [{"key": "paid", "count": 12}, …],
            #  "region": [{"key": "eu", "count": 30}, …]}

        Aggregation fields (and any ``**terms`` used to narrow the document set
        in filter context) resolve through :meth:`_resolve_es_term_field`, so a
        ``text`` field aggregates on its keyword sub-field and a ``__`` path
        reaches into a JSON/``object`` mapping. ``size`` caps the number of
        buckets per field (default :data:`_ES_DEFAULT_AGG_SIZE`); an optional
        ``query_string`` adds a scored full-text constraint.

        When Elasticsearch is disabled or the query fails, a DUAL model
        recomputes each facet over the database
        (``values(field).annotate(Count)``), failing closed to empty buckets for
        a field (or filter term) with no backing column; an ES_ONLY model
        returns empty buckets for every requested field.

        Set ``db_fallback=False`` to refuse that database recompute (a full-table
        ``GROUP BY`` on an unindexed column) and raise :class:`SnapEsUnavailable`
        instead when Elasticsearch can't answer; ``None`` (the default) defers to
        ``SNAPADMIN_ES_DB_FALLBACK`` (default ``True``). ES_ONLY models are
        unaffected.

        Raises ``ValueError`` if no field is given, if ``size`` is not positive,
        or for an unknown / non-term-filterable field, and
        :class:`SnapEsUnavailable` when ES is unavailable and the fallback is
        disabled.
        """
        if not fields:
            raise ValueError(f"{cls.__name__}.es_aggregate: at least one field is required")
        size = _ES_DEFAULT_AGG_SIZE if size is None else size
        if size < 1:
            raise ValueError(
                f"{cls.__name__}.es_aggregate: size must be a positive integer, got {size!r}"
            )
        # Resolve/validate every field and term up-front so a bad field raises
        # regardless of which backend ends up answering the query.
        resolved_fields = {name: cls._resolve_es_term_field(name) for name in fields}
        resolved_terms = cls._resolve_es_terms(terms)

        fallback = cls._es_db_fallback(db_fallback)
        es_intended = cls.es_index_enabled or cls.es_storage_mode != EsStorageMode.DB_ONLY
        use_es = es_intended and getattr(settings, "ELASTICSEARCH_ENABLED", False)
        es_error: Exception | None = None

        if use_es:
            try:
                aggs = {
                    name: {"terms": {"field": path, "size": size}}
                    for name, path in resolved_fields.items()
                }
                es = cls.get_es_client()
                response = es.search(
                    index=cls.get_es_index_name(),
                    body={
                        "query": cls._build_es_term_query(resolved_terms, query_string),
                        "size": 0,
                        "aggs": aggs,
                    },
                )
                aggregations = response.get("aggregations", {})
                return {
                    name: [
                        {"key": bucket["key"], "count": bucket["doc_count"]}
                        for bucket in aggregations.get(name, {}).get("buckets", [])
                    ]
                    for name in resolved_fields
                }
            except Exception as exc:
                logger.warning(
                    "es_aggregate_failed",
                    model=cls.__name__,
                    fields=list(fields),
                    fallback="empty" if cls.es_storage_mode == EsStorageMode.ES_ONLY
                    else ("db" if fallback else "raise"),
                    error=str(exc),
                )
                if cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    return {name: [] for name in resolved_fields}
                es_error = exc

        # Database fallback — only meaningful for models with a table.
        if cls.es_storage_mode == EsStorageMode.ES_ONLY:
            return {name: [] for name in resolved_fields}

        if es_intended and not fallback:
            cls._raise_es_unavailable("es_aggregate", es_error)

        try:
            base_qs = cls.objects.filter(**cls._es_orm_terms(terms))
            # Force field resolution now so an ES-only filter field (no DB
            # column) fails closed to empty facets rather than raising later.
            str(base_qs.query)
        except FieldError:
            return {name: [] for name in resolved_fields}

        result: dict[str, list[dict[str, object]]] = {}
        for name in resolved_fields:
            try:
                rows = (
                    base_qs.values(name)
                    .annotate(_snap_count=models.Count("pk"))
                    .order_by("-_snap_count", name)[:size]
                )
                result[name] = [
                    {"key": row[name], "count": row["_snap_count"]} for row in rows
                ]
            except FieldError:
                result[name] = []
        return result

    @classmethod
    def es_count(
        cls,
        *,
        query_string: str | None = None,
        db_fallback: bool | None = None,
        **terms,
    ) -> int:
        """Exact number of documents matching a structured ES term query.

        The counting counterpart to :meth:`es_filter`: every keyword argument is
        a term constraint (scalar → ``term``, list/tuple/set → ``terms``) run in
        ES *filter* context, and an optional ``query_string`` adds a scored
        full-text ``multi_match``. Field names resolve through
        :meth:`_resolve_es_term_field`, so a ``text`` field targets its keyword
        sub-field and a ``__`` path reaches into a JSON/``object`` mapping.

        Unlike :meth:`es_filter` — which caps its queryset at
        ``SNAPADMIN_ES_SEARCH_LIMIT`` and can never see past ES's
        ``index.max_result_window`` — ``es_count`` calls the Elasticsearch
        ``_count`` API, so it returns the *true* match total no matter how large
        the result set::

            Product.es_count(available=True)              # every in-stock product
            Product.es_count(query_string="laptop")       # matches, unbounded

        When Elasticsearch is disabled or the query fails, a DUAL model falls
        back to the equivalent database ``count()`` (failing closed to ``0`` if a
        term field has no backing column); an ES_ONLY model returns ``0``.

        Set ``db_fallback=False`` to refuse that database ``count()`` and raise
        :class:`SnapEsUnavailable` instead when Elasticsearch can't answer;
        ``None`` (the default) defers to ``SNAPADMIN_ES_DB_FALLBACK`` (default
        ``True``). ES_ONLY models are unaffected (they return ``0``).

        Raises ``ValueError`` for an unknown or non-term-filterable field, and
        :class:`SnapEsUnavailable` when ES is unavailable and the fallback is
        disabled.
        """
        # Resolve/validate every term up-front so a bad field raises regardless
        # of which backend ends up answering the query.
        resolved = cls._resolve_es_terms(terms)

        fallback = cls._es_db_fallback(db_fallback)
        es_intended = cls.es_index_enabled or cls.es_storage_mode != EsStorageMode.DB_ONLY
        use_es = es_intended and getattr(settings, "ELASTICSEARCH_ENABLED", False)
        es_error: Exception | None = None

        if use_es:
            try:
                es = cls.get_es_client()
                response = es.count(
                    index=cls.get_es_index_name(),
                    body={"query": cls._build_es_term_query(resolved, query_string)},
                )
                return int(response.get("count", 0))
            except Exception as exc:
                logger.warning(
                    "es_count_failed",
                    model=cls.__name__,
                    terms=list(terms),
                    fallback="zero" if cls.es_storage_mode == EsStorageMode.ES_ONLY
                    else ("db" if fallback else "raise"),
                    error=str(exc),
                )
                if cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    return 0
                es_error = exc

        # Database fallback — only meaningful for models with a table.
        if cls.es_storage_mode == EsStorageMode.ES_ONLY:
            return 0

        if es_intended and not fallback:
            cls._raise_es_unavailable("es_count", es_error)

        try:
            qs = cls.objects.filter(**cls._es_orm_terms(terms))
            # Force field resolution now so an ES-only term (no DB column) fails
            # closed to 0 rather than raising when count() hits the database.
            str(qs.query)
        except FieldError:
            return 0
        return qs.count()

    @classmethod
    def _es_orm_terms(cls, terms: dict) -> dict:
        """Translate es_filter/es_scan ``**terms`` to ORM filter kwargs.

        A list/tuple/set value becomes an ``__in`` lookup; a scalar stays an
        exact match. Shared by the database fallbacks of the ES query methods.
        """
        return {
            (f"{key}__in" if isinstance(value, (list, tuple, set)) else key):
                (list(value) if isinstance(value, (list, tuple, set)) else value)
            for key, value in terms.items()
        }

    @classmethod
    def es_scan(
        cls,
        *,
        query_string: str | None = None,
        page_size: int | None = None,
        db_fallback: bool | None = None,
        source: bool | None = None,
        limit: int | None = None,
        **terms,
    ) -> Iterator["SnapModel"] | Iterator[Any]:
        """Deep-scan iterator — yield every matching document, no ``from`` paging.

        Elasticsearch refuses a ``from + size`` deeper than
        ``index.max_result_window`` (10,000), so :meth:`es_search` /
        :meth:`es_filter` can never return more than that many hits. ``es_scan``
        walks the whole result set instead by paging with ``search_after`` over a
        stable ``id`` sort — one request of ``page_size`` documents per
        round-trip (default ``SNAPADMIN_ES_SEARCH_LIMIT``), lazily, so memory
        stays bounded no matter how large the match is. The ``id`` sort is kept
        deliberately: it is the cheapest *stable* ``search_after`` order because
        the primary key is unique, so no separate tiebreak is needed.

        Filtering is identical to :meth:`es_filter`: scalar/list ``**terms`` run
        in ES filter context (resolved through :meth:`_resolve_es_term_field`),
        and an optional ``query_string`` adds a scored full-text constraint.
        DUAL models yield database instances in cursor (``id``-ascending) order;
        ES_ONLY models yield objects reconstructed from the index.

        Pass ``source=False`` to stream **primary keys only**: the request sends
        ``"_source": false`` (ES never ships the document body), and each pk comes
        straight from the sort cursor — so a DUAL model skips the per-page
        ``in_bulk`` round-trip entirely (a pk indexed in ES but missing from the
        table is still yielded, since the database is never consulted). This is
        the cheap way to walk the pks of millions of matches. ``source=None`` (the
        default) or ``source=True`` keeps full object hydration, byte-identical to
        before.

        Pass ``limit=N`` to stop after ``N`` results; the ES request size is
        capped to what remains, so a limit below ``page_size`` never over-fetches.
        ``None`` (the default) streams the whole match.

        Fails safe: a DUAL model whose Elasticsearch is disabled — or unreachable
        *before* any document is streamed — walks the equivalent database filter
        with ``.iterator()`` instead (failing closed to nothing if a term has no
        backing column; ``source=False`` streams pks via ``values_list`` and
        ``limit`` still applies); an ES_ONLY model yields nothing. If ES fails
        *after* streaming has begun, the scan stops where it was rather than
        restarting on the database and double-emitting.

        Set ``db_fallback=False`` to refuse the database ``.iterator()`` fallback
        and raise :class:`SnapEsUnavailable` (during iteration) when Elasticsearch
        is unavailable *before* streaming begins; ``None`` (the default) defers to
        ``SNAPADMIN_ES_DB_FALLBACK`` (default ``True``). A *mid-stream* ES failure
        still stops rather than raising — the ``search_after`` cursor is already
        gone, so there is no database scan to suppress there. ES_ONLY models are
        unaffected.

        Raises ``ValueError`` (eagerly, before iteration) for a non-positive
        ``page_size`` / ``limit`` or an unknown / non-term-filterable field, and
        :class:`SnapEsUnavailable` (lazily, on iteration) when ES is unavailable
        and the fallback is disabled.
        """
        if page_size is not None and page_size < 1:
            raise ValueError(
                f"{cls.__name__}.es_scan: page_size must be a positive integer, got {page_size!r}"
            )
        if limit is not None and limit < 1:
            raise ValueError(
                f"{cls.__name__}.es_scan: limit must be a positive integer, got {limit!r}"
            )
        # Resolve/validate every term up-front so a bad field raises on the call,
        # not lazily on the first `next()` of the returned generator.
        resolved = cls._resolve_es_terms(terms)
        page_size = page_size or get_setting("SNAPADMIN_ES_SEARCH_LIMIT", 1000)
        fallback = cls._es_db_fallback(db_fallback)
        pk_only = source is False
        return cls._es_scan_iter(
            resolved, terms, query_string, page_size, fallback, pk_only, limit,
        )

    @classmethod
    def _es_scan_iter(
        cls,
        resolved: dict,
        terms: dict,
        query_string: str | None,
        page_size: int,
        fallback: bool,
        pk_only: bool,
        limit: int | None,
    ) -> Iterator["SnapModel"] | Iterator[Any]:
        es_intended = cls.es_index_enabled or cls.es_storage_mode != EsStorageMode.DB_ONLY
        use_es = es_intended and getattr(settings, "ELASTICSEARCH_ENABLED", False)
        es_error: Exception | None = None
        if use_es:
            produced = False
            try:
                for obj in cls._es_scan_via_es(resolved, query_string, page_size, pk_only, limit):
                    produced = True
                    yield obj
                return
            except Exception as exc:
                logger.warning(
                    "es_scan_failed",
                    model=cls.__name__,
                    terms=list(terms),
                    produced=produced,
                    fallback="none" if (produced or cls.es_storage_mode == EsStorageMode.ES_ONLY)
                    else ("db" if fallback else "raise"),
                    error=str(exc),
                )
                # Once any document has been streamed the search_after position
                # is gone, so a DB restart would double-emit — stop instead.
                if produced or cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    return
                es_error = exc

        # Database fallback — only meaningful for models with a table.
        if cls.es_storage_mode == EsStorageMode.ES_ONLY:
            return
        if es_intended and not fallback:
            cls._raise_es_unavailable("es_scan", es_error)
        try:
            qs = cls.objects.filter(**cls._es_orm_terms(terms)).order_by("pk")
            if pk_only:
                qs = qs.values_list("pk", flat=True)
            if limit is not None:
                qs = qs[:limit]
            # Force field resolution now so an ES-only term (no DB column) fails
            # closed to an empty scan rather than raising during iteration.
            str(qs.query)
        except FieldError:
            return
        yield from qs.iterator()

    @classmethod
    def _es_scan_via_es(
        cls,
        resolved: dict,
        query_string: str | None,
        page_size: int,
        pk_only: bool,
        limit: int | None,
    ) -> Iterator["SnapModel"] | Iterator[Any]:
        es = cls.get_es_client()
        index_name = cls.get_es_index_name()
        query = cls._build_es_term_query(resolved, query_string)
        search_after: list | None = None
        produced = 0
        while True:
            size = page_size
            if limit is not None:
                remaining = limit - produced
                if remaining <= 0:
                    return
                size = min(page_size, remaining)
            body: dict = {"query": query, "size": size, "sort": [{"id": "asc"}]}
            if pk_only:
                # Skip the document body — the pk is read from the sort cursor.
                body["_source"] = False
            if search_after is not None:
                body["search_after"] = search_after
            response = es.search(index=index_name, body=body)
            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                return
            # A page can exceed `size` only if ES ignored it; keep the stream at
            # the requested width so `limit` is exact.
            hits = hits[:size]

            if pk_only:
                for hit in hits:
                    # The sole sort key is `id`, so its value is the pk.
                    yield hit["sort"][0]
                    produced += 1
            elif cls.es_storage_mode == EsStorageMode.ES_ONLY:
                for hit in hits:
                    data = hit["_source"]
                    obj = cls(**{k: v for k, v in data.items() if k != "id"})
                    obj.pk = data.get("id")
                    yield obj
                    produced += 1
            else:
                pks = [hit["_source"]["id"] for hit in hits]
                by_pk = cls.objects.in_bulk(pks)
                for pk in pks:
                    if pk in by_pk:
                        yield by_pk[pk]
                        produced += 1

            search_after = hits[-1].get("sort")
            # A short page (fewer than requested) means the index is exhausted;
            # a missing cursor would make the next request restart from the top.
            if search_after is None or len(hits) < size:
                return

    @classmethod
    def _es_keyset_iter(cls, qs: models.QuerySet, chunk_size: int) -> Iterator["SnapModel"]:
        """Stream ``qs`` in bounded memory by paging a ``pk__gt`` keyset cursor.

        ``QuerySet.iterator()`` buffers the *entire* result set client-side on the
        mysqlclient backend (it has no true server-side cursor), so it can OOM a
        large table. Paging by ``pk__gt`` instead holds at most ``chunk_size`` rows
        at a time on every backend. Ordering is by primary key; each document is
        written under ``_id = pk``, so iteration order doesn't affect the result.
        """
        pk_attname = cls._meta.pk.attname
        qs = qs.order_by("pk")
        cursor = None
        while True:
            page = qs.filter(pk__gt=cursor) if cursor is not None else qs
            batch = list(page[:chunk_size])
            if not batch:
                return
            yield from batch
            cursor = getattr(batch[-1], pk_attname)

    @classmethod
    def es_reindex_all(cls, *, chunk_size: int = 500) -> dict:
        """Synchronise all records to the Elasticsearch index.

        Uses the bulk API (one round-trip per ``chunk_size`` documents). DB-backed
        models are streamed with a ``pk__gt`` keyset cursor (:meth:`_es_keyset_iter`)
        rather than ``QuerySet.iterator()``, so re-indexing millions of rows holds
        bounded memory on every backend — including mysqlclient, where
        ``.iterator()`` would buffer the whole result set client-side and OOM.
        """
        if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            return {"skipped": True, "reason": "Elasticsearch not available"}

        from elasticsearch import helpers

        es = cls.get_es_client()
        cls._ensure_es_index_and_mapping()
        index_name = cls.get_es_index_name()

        qs = cls.objects.all()
        # EsQuerySet (ES_ONLY models) is already materialised hits, single pass;
        # DB querysets stream in bounded memory via the keyset paginator.
        rows = iter(qs) if isinstance(qs, EsQuerySet) else cls._es_keyset_iter(qs, chunk_size)

        def actions():
            for obj in rows:
                yield {
                    "_index": index_name,
                    "_id": obj.pk,
                    "_source": obj.get_es_document(),
                }

        try:
            indexed, errors = helpers.bulk(
                es, actions(), chunk_size=chunk_size, raise_on_error=False
            )
        except Exception as exc:
            logger.warning("es_bulk_reindex_failed", model=cls.__name__, error=str(exc))
            return {"indexed": 0, "errors": [str(exc)]}

        if errors:
            logger.warning(
                "es_bulk_reindex_partial",
                model=cls.__name__,
                indexed=indexed,
                error_count=len(errors),
            )
            return {"indexed": indexed, "errors": errors}
        return {"indexed": indexed}

    # ------------------------------------------------------------------
    # GDPR / data-retention purge
    # ------------------------------------------------------------------

    @classmethod
    def _delete_pks_from_es(cls, pks: list) -> bool:
        """Remove the given primary keys from the ES index via a single bulk call.

        Used by the DUAL-mode purge: ``QuerySet.delete()`` is a bulk SQL DELETE
        that never calls ``Model.delete()``, so the ES mirror would otherwise be
        left behind. We collect the pks before the DB delete and clear them here
        with one bulk ``delete_by_query`` (an ``ids`` filter) rather than one
        ``es.delete()`` call per pk.

        Returns ``True`` when the ES mirror was cleared (or there was nothing to
        do), ``False`` when the ES delete failed. Callers must treat ``False``
        as a purge failure for this model's secondary store, not as success —
        the personal data may still be live and searchable via ES.
        """
        if not pks or not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            return True
        try:
            es = cls.get_es_client()
            index_name = cls.get_es_index_name()
            es.delete_by_query(
                index=index_name,
                body={"query": {"ids": {"values": list(pks)}}},
                ignore=[404],
            )
            return True
        except Exception as exc:
            logger.warning(
                "es_purge_delete_failed",
                model=cls.__name__,
                pk_count=len(pks),
                error=str(exc),
            )
            return False

    @classmethod
    def _purge_expired_es_only(cls, cutoff, retention_field, dry_run: bool) -> int:
        """Purge expired ES_ONLY documents via a range query on the retention field.

        ES_ONLY models have no DB table, so retention must run against the index
        directly. Requires the retention field to be mapped as a date in ES.
        """
        if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            return 0
        try:
            es = cls.get_es_client()
            index_name = cls.get_es_index_name()
            body = {"query": {"range": {retention_field: {"lt": cutoff.isoformat()}}}}
            if dry_run:
                resp = es.count(index=index_name, body=body)
                return resp.get("count", 0)
            resp = es.delete_by_query(index=index_name, body=body, ignore=[404])
            return resp.get("deleted", 0)
        except Exception as exc:
            logger.warning(
                "es_purge_query_failed",
                model=cls.__name__,
                retention_field=retention_field,
                error=str(exc),
            )
            return 0

    @classmethod
    def _purge_expired_files(cls, qs, purging_pks: set) -> None:
        """Delete :attr:`data_retention_files` storage objects for the rows in ``qs``.

        Called **before** ``qs.delete()`` (see :meth:`purge_expired`'s docstring)
        so a file-deletion failure leaves the row — and therefore the file's
        name — intact and the purge retryable on the next run. A missing file
        (already gone from storage) is treated as already-done, not a failure.

        A path still referenced by another **live** row is skipped — deleting it
        would orphan that other row's file — and only logged, never counted as a
        failure. ``purging_pks`` (every pk in this same purge batch) is excluded
        from that "still referenced" check: two expiring rows that happen to
        share one path must not skip each other forever just because each still
        sees the other one, not-yet-deleted, in the database.

        A genuine storage error raises :class:`SnapPurgeError`, mirroring the
        Elasticsearch-mirror failure below, so it reaches the caller's error
        report (``purge_expired_data``'s ``errors`` dict) instead of only a log
        line — the same rule #OPS2 applies to a task summary.
        """
        file_fields = getattr(cls, "data_retention_files", None) or []
        if not file_fields:
            return
        # One "still referenced elsewhere" check per distinct (field, path) —
        # the shared-file rule (#RET2c) costs one extra query per distinct path,
        # not one per row.
        checked: dict[tuple[str, str], bool] = {}
        failures: list[str] = []
        for row in qs.iterator():
            for field_name in file_fields:
                field_file = getattr(row, field_name, None)
                path = getattr(field_file, "name", "") if field_file else ""
                if not path:
                    continue
                key = (field_name, path)
                if key not in checked:
                    checked[key] = (
                        cls.objects.filter(**{field_name: path})
                        .exclude(pk__in=purging_pks)
                        .exists()
                    )
                if checked[key]:
                    logger.info(
                        "snapadmin.purge.file_shared_skip",
                        model=cls.__name__, field=field_name, path=path,
                    )
                    continue
                try:
                    if field_file.storage.exists(path):
                        field_file.storage.delete(path)
                except Exception as exc:
                    logger.error(
                        "snapadmin.purge.file_delete_failed",
                        model=cls.__name__, field=field_name, path=path, error=str(exc),
                    )
                    failures.append(f"{field_name}={path}: {exc}")
        if failures:
            raise SnapPurgeError(
                f"{cls.__name__}: {len(failures)} file(s) could not be deleted "
                "during retention purge (rows kept intact so the purge is "
                "retryable): " + "; ".join(failures)
            )

    @classmethod
    def purge_expired(cls, *, now=None, dry_run: bool = False) -> int:
        """Delete records past this model's ``data_retention_days`` (GDPR).

        Removes rows older than the retention window — measured on
        ``data_retention_field`` — from **every** storage layer the model uses:

        * ``DB_ONLY`` — bulk delete from the database.
        * ``DUAL``    — bulk delete from the database **and** the ES mirror.
        * ``ES_ONLY`` — delete the matching documents from Elasticsearch.

        When :attr:`data_retention_files` names storage-backed fields, their
        files are deleted **before** the rows — see :meth:`_purge_expired_files`
        for the shared-file and failure rules. ``ES_ONLY`` models never carry
        files (see the attribute's docstring: no DB table means no field to
        read a path from), so the file pass only runs for ``DB_ONLY``/``DUAL``.

        Returns the number of records purged (or that *would* be purged when
        ``dry_run=True``); returns ``0`` when retention is not configured. The
        count always reflects this model's own rows, never the cascade-inflated
        total that ``QuerySet.delete()`` reports when related rows are removed
        via ``on_delete=CASCADE``. ``dry_run=True`` touches nothing — no file is
        deleted and no row is counted as skipped — it only counts rows.

        For ``DUAL`` mode, raises :class:`SnapPurgeError` if the database delete
        succeeds but the Elasticsearch mirror cannot be cleared — the caller
        must not report that model as fully purged in that case. There is no
        two-phase commit across the DB and ES; the DB delete has already
        happened by the time this is raised, which is a known limitation of
        purging across heterogeneous stores.
        """
        retention_days = getattr(cls, "data_retention_days", None)
        if not retention_days or retention_days <= 0:
            return 0

        retention_field = getattr(cls, "data_retention_field", "created_at")
        now = now or timezone.now()
        cutoff = now - timedelta(days=retention_days)

        if cls.es_storage_mode == EsStorageMode.ES_ONLY:
            return cls._purge_expired_es_only(cutoff, retention_field, dry_run)

        # Retention is time-based, not tenant-based: an expired row is purged
        # regardless of which tenant it belongs to, so this sweep is one of
        # the few legitimately cross-tenant operations (#FUT1b) — it must see
        # every tenant's rows, never only the caller's (there usually is no
        # caller; this runs from a scheduled task with no request in hand).
        from snapadmin.tenancy import use_all_tenants

        with use_all_tenants():
            qs = cls.objects.filter(**{f"{retention_field}__lt": cutoff})
            if dry_run:
                return qs.count()

            purging_pks = set(qs.values_list("pk", flat=True))
            count = len(purging_pks)

            if cls.es_storage_mode == EsStorageMode.DUAL:
                cls._purge_expired_files(qs, purging_pks)
                qs.delete()
                if not cls._delete_pks_from_es(list(purging_pks)):
                    raise SnapPurgeError(
                        f"{cls.__name__}: {count} row(s) deleted from the database, "
                        "but the Elasticsearch mirror could not be cleared; personal "
                        "data may still be live and searchable via ES."
                    )
                return count

            cls._purge_expired_files(qs, purging_pks)
            qs.delete()
            return count

    # ------------------------------------------------------------------
    # Human-readable representation
    # ------------------------------------------------------------------

    def __str__(self):
        for attr in ["name", "alias"]:
            val = getattr(self, attr, None)
            if val: return str(val)
        for pair in [("first_name", "last_name"), ("firstname", "lastname")]:
            f, l = getattr(self, pair[0], None), getattr(self, pair[1], None)
            if f and l: return f"{l}, {f}"
        return super().__str__()

    @classmethod
    def get_admin_fields(cls) -> AdminFieldSets:
        meta_fields = {f.name: f for f in cls._meta.get_fields() if hasattr(f, "name") and not (f.one_to_many or f.one_to_one or f.many_to_many)}
        meta_fields_related = {f.name: f for f in cls._meta.get_fields() if hasattr(f, "name") and (f.many_to_one or f.many_to_many)}
        attr_fields = {fn: fo for fn, fo in cls.__dict__.items()}

        form_fields = [fn for fn, fo in meta_fields.items() if getattr(fo, SnapFieldAttributeEnum.SHOW_IN_FORM.value, None)]
        list_display = [fn for fn, fo in meta_fields.items() if getattr(fo, SnapFieldAttributeEnum.SHOW_IN_LIST.value, True)]
        search_fields = [fn for fn, fo in meta_fields.items() if getattr(fo, SnapFieldAttributeEnum.SEARCHABLE.value, False)]
        if "id" not in search_fields: search_fields.append("id")

        all_fields_for_readonly = {**meta_fields, **meta_fields_related}
        editable_fields = [fn for fn, fo in all_fields_for_readonly.items() if not getattr(fo, SnapFieldAttributeEnum.EDITABLE.value, False)]
        updatable_fields = [fn for fn, fo in all_fields_for_readonly.items() if not getattr(fo, SnapFieldAttributeEnum.UPDATABLE.value, True)]

        def dynamic_get_readonly_fields(self, request, obj=None):
            return [fn for fn, fo in all_fields_for_readonly.items() if fn in editable_fields or (fn in updatable_fields and obj and obj.pk)]

        # Generated callables (this one, the wysiwyg safe_html_<field> displays
        # below, and the SnapFunctionField displays further down) are stashed
        # here rather than written into cls.admin_overrides. admin_overrides is
        # the project's own dict; register_admin() merges this stash into
        # admin_attrs first and admin_overrides last, so a project override
        # always wins by construction instead of by who wrote into the shared
        # dict first (#ADM2a). Rebuilt from scratch on every call, so a re-run
        # (e.g. after a field's safe_html flag changes) always reflects the
        # current field state.
        generated_overrides: dict[str, Callable] = {"get_readonly_fields": dynamic_get_readonly_fields}

        list_filter = []
        for field_name, field in meta_fields.items():
            if not getattr(field, SnapFieldAttributeEnum.FILTERABLE.value, False): continue
            if isinstance(field, (models.DateField, models.DateTimeField, models.TimeField)): list_filter.append((field_name, RangeDateFilter))
            elif isinstance(field, (models.IntegerField, models.FloatField, models.DecimalField)): list_filter.append((field_name, RangeNumericFilter))
            elif isinstance(field, models.ForeignKey): list_filter.append((field_name, RelatedDropdownFilter))
            elif isinstance(field, models.CharField) and field.choices: list_filter.append((field_name, ChoicesDropdownFilter))
            else: list_filter.append(field_name)

        autocomplete_fields = [fn for fn, fo in meta_fields_related.items() if getattr(fo, SnapFieldAttributeEnum.AUTOCOMPLETE.value, True)]

        # Handle WYSIWYG fields for safe HTML rendering in list view
        wysiwyg_fields = [fn for fn, fo in meta_fields.items() if getattr(fo, "wysiwyg", False)]
        for fn in wysiwyg_fields:
            if fn in list_display:
                idx = list_display.index(fn)
                method_name = f"safe_html_{fn}"

                def make_wysiwyg_display(field_name):
                    field_obj = cls._meta.get_field(field_name)
                    @unfold_display(description=field_obj.verbose_name)
                    def _display(self, obj):
                        raw = getattr(obj, field_name, "") or ""
                        # Wysiwyg values are attacker-controllable HTML. Sanitize
                        # before mark_safe to prevent stored XSS in the changelist,
                        # unless the field explicitly trusts its content.
                        if getattr(field_obj, "safe_html", False):
                            return mark_safe(raw)
                        return mark_safe(sanitize_html(raw))
                    return _display

                generated_overrides[method_name] = make_wysiwyg_display(fn)
                list_display[idx] = method_name

        for attr_name, attr_value in attr_fields.items():
            if not isinstance(attr_value, snapfields.SnapFunctionField): continue
            method_name = f"SnapFunctionField{attr_name.capitalize()}"
            def _make_display_method(field):
                @unfold_display(description=getattr(field, "verbose_name", "") or getattr(field, "name", ""), header=True)
                def _display(self, obj):
                    val = field.get_display_value(obj)
                    if UNFOLD_INSTALLED:
                        return [val, None, None]
                    return val
                return _display
            generated_overrides[method_name] = _make_display_method(attr_value)
            list_display.append(method_name)

        if "id" in list_display: list_display.remove("id")
        list_display.insert(0, "id")
        cls._admin_generated_overrides = generated_overrides
        return AdminFieldSets(form_fields, list_display, search_fields, list_filter, autocomplete_fields)

    @classmethod
    def get_admin_media(cls) -> tuple[list[str], list[str]]:
        """The ``(js, css)`` asset lists ``register_admin()`` builds the admin's
        ``Media`` class from — theme-sheet selection, the ``connectivity.js`` /
        ``offline.js`` gating and de-duplication with ``js_admin_files`` /
        ``css_admin_files`` all included (#ADM2c). Public so a project
        overriding ``register_admin()`` can extend the real lists instead of
        copying a snapshot that rots at the next release.
        """
        jquery_extra = "" if settings.DEBUG else ".min"
        js = [
            f"admin/js/vendor/jquery/jquery{jquery_extra}.js",
            "admin/js/jquery.init.js",
            "snapadmin/js/jquery_bridge.js",
            "snapadmin/js/select2.min.js",
            "snapadmin/js/admin.js",
        ]
        # connectivity.js is opt-in and off by default (#JS2e/DECISIONS.md D18):
        # it polls /api/health/ and, on a confirmed-down backend, shows a warning
        # toast and blocks form submits so a user does not lose what they typed.
        # An always-on client for what is an opt-in offline layer was the bug —
        # a project with SNAPADMIN_REST_API_ENABLED=False (a documented, supported
        # setting) got a health poll that 404s forever and a bricked admin. Two
        # conditions must both hold before it loads: the setting is on, and at
        # least one registered model actually has offline_mode=True — otherwise
        # there is no offline layer for it to drive. It also owns
        # window.SnapAdminToast, which offline.js borrows for its own
        # "cached / will sync" toast, so it must load before offline.js when both
        # are present; see tests/test_offline.py::TestConnectivityJsInjection.
        if get_setting("SNAPADMIN_CONNECTIVITY_ENABLED", False) and _any_offline_capable_model():
            js.append("snapadmin/js/connectivity.js")

        css = ["snapadmin/css/select2.min.css", "snapadmin/css/admin.css"]
        # The two theme layers are mutually exclusive, and that is the whole
        # scoping mechanism: neither sheet carries a theme prefix, so exactly
        # one of them must reach the page. `admin-stock.css` gives Django's
        # built-in admin a modern form layout; loading it next to a theme
        # overrides the theme's own layout instead of complementing it.
        # `admin-unfold.css` fills the few gaps Unfold leaves. Both come after
        # `admin.css` so they win the cascade over the shared cosmetics.
        css.append(
            "snapadmin/css/admin-unfold.css" if UNFOLD_INSTALLED
            else "snapadmin/css/admin-stock.css"
        )

        extra_js = [cls.js_admin_files] if isinstance(cls.js_admin_files, str) else list(cls.js_admin_files)
        extra_css = [cls.css_admin_files] if isinstance(cls.css_admin_files, str) else list(cls.css_admin_files)
        final_js = list(dict.fromkeys(js + extra_js))
        if cls.offline_mode:
            final_js.append("snapadmin/js/offline.js")
        final_css = list(dict.fromkeys(css + extra_css))
        return final_js, final_css

    @classmethod
    def register_admin(cls) -> None:
        """Build and register this model's ``ModelAdmin`` from its Snap field flags.

        ``admin_overrides`` is merged in last, so it always wins over every
        attribute or method the generator itself produces — including the
        callables :meth:`get_admin_fields` stashes internally, such as
        ``get_readonly_fields`` and the wysiwyg ``safe_html_<field>`` display
        methods (#ADM2a).
        """
        if not cls.admin_enabled: return
        admin_fields = cls.get_admin_fields()
        form_fields = admin_fields.form_fields
        list_display = admin_fields.list_display
        search_fields = admin_fields.search_fields
        list_filter = admin_fields.list_filter
        autocomplete_fields = admin_fields.autocomplete_fields

        # Build fieldsets based on 'tab' and 'row' attributes
        tabs_map = {}
        untabbed_fields = []

        def group_fields_by_row(fields_list):
            grouped = []
            row_map = {}
            for fn in fields_list:
                try:
                    field_obj = cls._meta.get_field(fn)
                    row_name = getattr(field_obj, "row", None)
                    if row_name:
                        if row_name not in row_map:
                            row_map[row_name] = []
                            grouped.append(row_map[row_name])
                        row_map[row_name].append(fn)
                    else:
                        grouped.append(fn)
                except FieldDoesNotExist:
                    grouped.append(fn)

            # Convert multi-field rows to tuples for Django fieldsets
            final_grouped = []
            for item in grouped:
                if isinstance(item, list):
                    final_grouped.append(tuple(item))
                else:
                    final_grouped.append(item)
            return final_grouped

        for field_name in form_fields:
            try:
                field = cls._meta.get_field(field_name)
                tab_name = getattr(field, "tab", None)
                if tab_name:
                    tabs_map.setdefault(tab_name, []).append(field_name)
                else:
                    untabbed_fields.append(field_name)
            except FieldDoesNotExist:
                untabbed_fields.append(field_name)

        fieldsets = []
        if untabbed_fields:
            fieldsets.append((None, {"fields": group_fields_by_row(untabbed_fields)}))

        for tab_name, fields in tabs_map.items():
            fieldsets.append((tab_name, {
                "fields": group_fields_by_row(fields),
                "classes": ("tab",)
            }))

        final_js, final_css = cls.get_admin_media()

        # Auto-derive list_select_related from the ForeignKey columns actually shown
        # in the list view. Rendering an FK column (or a __str__ that walks it) without
        # this issues one extra query per row — the classic admin N+1. We only join the
        # FKs that appear in list_display, so we never pull relations we won't display.
        fk_field_names = {
            f.name for f in cls._meta.get_fields()
            if getattr(f, "many_to_one", False)
        }
        list_select_related = [fn for fn in list_display if fn in fk_field_names]

        A = DjangoAdminClassAttributeEnum
        admin_attrs = {
            A.LIST_DISPLAY.value: list_display,
            A.SEARCH_FIELDS.value: search_fields,
            A.LIST_FILTER.value: list_filter,
            A.AUTOCOMPLETE_FIELDS.value: autocomplete_fields,
            A.INLINES.value: cls.snap_inlines,
            # Newest-first default for the changelist. Applied here (not on the
            # base manager) so it never leaks into GROUP BY on aggregations; a
            # model's explicit Meta.ordering is honoured when set.
            "ordering": list(cls._meta.ordering) or ["-pk"],
            "list_select_related": list_select_related or False,
            "list_per_page": cls.list_per_page,
            "list_max_show_all": cls.list_max_show_all,
            "show_full_result_count": cls.show_full_result_count,
            # Fast, timeout-proof changelist count on huge tables.
            # Safe by construction: only estimates unfiltered, large PostgreSQL
            # tables, exact everywhere else (see snapadmin.pagination).
            "paginator": EstimatedCountPaginator,
            "formatted_id": formatted_id,
            A.MEDIA_CLASS.value: type(A.MEDIA_CLASS.value, (), {A.CSS_MEDIA.value: {A.ALL_MEDIA.value: final_css}, A.JS_MEDIA.value: final_js}),
        }

        if UNFOLD_INSTALLED:
            admin_attrs.update({
                "compressed_fields": cls.compressed_fields,
                "warn_unsaved_form": cls.warn_unsaved_form,
                "list_filter_submit": cls.list_filter_submit,
                "tabs": cls.admin_tabs,
            })

        if fieldsets:
            admin_attrs[A.FIELDSETS.value] = fieldsets
        else:
            admin_attrs[A.FIELDS.value] = form_fields

        def formfield_for_dbfield(self, db_field, request, **kwargs):
            if isinstance(db_field, (models.TextField, snapfields.SnapTextField)) and getattr(db_field, "wysiwyg", False):
                kwargs["widget"] = _wysiwyg_widget()
            return super(ModelAdmin, self).formfield_for_dbfield(db_field, request, **kwargs)

        def get_fieldsets(self, request, obj=None):
            # If we have rows, Unfold needs specific layout classes
            fs = super(ModelAdmin, self).get_fieldsets(request, obj)

            # PII masking: drop masked fields from the change form for
            # users without PII access, so raw values never reach an editable
            # input. The changelist shows them masked (see PIIMaskingAdminMixin).
            from snapadmin.masking import get_masked_fields, user_can_view_pii
            masked = set(get_masked_fields(cls._meta.app_label, cls._meta.model_name))
            if masked and not user_can_view_pii(request.user):
                filtered = []
                for name, opts in fs:
                    new_fields = []
                    for f in opts.get("fields", []):
                        if isinstance(f, tuple):
                            kept = tuple(x for x in f if x not in masked)
                            if kept:
                                new_fields.append(kept if len(kept) > 1 else kept[0])
                        elif f not in masked:
                            new_fields.append(f)
                    filtered.append((name, {**opts, "fields": new_fields}))
                fs = filtered

            if UNFOLD_INSTALLED:
                for name, opts in fs:
                    fields = opts.get("fields", [])
                    has_row = any(isinstance(f, tuple) for f in fields)
                    if has_row:
                        classes = list(opts.get("classes", []))
                        if "snap-field-row" not in classes:
                            classes.append("snap-field-row")
                        opts["classes"] = tuple(classes)
            return fs

        admin_attrs["formfield_for_dbfield"] = formfield_for_dbfield
        admin_attrs["get_fieldsets"] = get_fieldsets
        # Generated callables first, the project's own admin_overrides last —
        # merge order is the precedence rule (#ADM2a).
        admin_attrs.update(getattr(cls, "_admin_generated_overrides", {}))
        admin_attrs.update(getattr(cls, "admin_overrides", {}))

        # Ecosystem admin mixins come first in the MRO so their
        # behaviour (import/export, versioning, history, object perms) wraps
        # SnapAdmin's, which in turn wraps Django/Unfold's ModelAdmin.
        extra_mixins = tuple(getattr(cls, "admin_mixins", []) or [])
        parent_classes = extra_mixins + (PIIMaskingAdminMixin, SnapSaveMixin, ModelAdmin)
        admin_class = type(f"{cls.__name__}Admin", parent_classes, admin_attrs)
        try: admin.site.register(cls, admin_class)
        except admin.sites.AlreadyRegistered: pass

    @staticmethod
    def register_all_admins(app_label: str | None = None) -> None:
        from snapadmin.admin import APITokenAdmin, ErrorEventAdmin, SnapadminAuditLogAdmin
        try:
            admin.site.register(APIToken, APITokenAdmin)
        except admin.sites.AlreadyRegistered:
            pass
        try:
            admin.site.register(ErrorEvent, ErrorEventAdmin)
        except admin.sites.AlreadyRegistered:
            pass
        try:
            admin.site.register(SnapadminAuditLog, SnapadminAuditLogAdmin)
        except admin.sites.AlreadyRegistered:
            pass

        for model in apps.get_models():
            if not is_registered(model):
                continue
            # register_admin() is SnapModel's own generator — it reads the Snap
            # field flags to build fieldsets, filters and list columns. A plain
            # model registered with @snap_model has no such method (and no Snap
            # fields to read), so it keeps whatever admin the project wrote for
            # it by hand instead of being handed a generated one.
            if not hasattr(model, "register_admin"):
                continue
            if app_label is None or model._meta.app_label == app_label:
                model.register_admin()


# ===========================================================================
# Opting a plain django.db.models.Model in — @snap_model
# ===========================================================================

#: Sentinel for "this keyword was not passed", distinct from an explicit
#: ``None`` — which several of the settings below use as a meaningful value
#: ("no write allowlist", "no per-field lookup override"). Typed ``Any`` so the
#: public signature can still advertise each parameter's real type.
_UNSET: Any = object()


def _as_list(value: Any) -> Any:
    """Copy a sequence of names into a list, passing ``None``/``_UNSET`` through.

    The copy matters: the caller's list must not stay live inside the registry,
    where a later ``append`` would silently re-configure the model.
    """
    if value is None or value is _UNSET:
        return value
    return [str(item) for item in value]


def _as_lookup_map(value: Any) -> Any:
    """Copy a ``{field: [lookup, …]}`` mapping, passing ``None``/``_UNSET`` through."""
    if value is None or value is _UNSET:
        return value
    return {str(key): _as_list(item) for key, item in value.items()}


#: ``SnapModel`` class attributes with no ``@snap_model()`` keyword counterpart
#: yet — the model-side mirror of ``fields._SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS``
#: (#PAR1d). Each is tracked by a specific #RFC1g capability that needs more than
#: a keyword to retrofit (an attached manager, a shared purge classmethod, an
#: admin-generation refactor) — see the #RFC1g verdict table in
#: ``.claude/roadmap.md``. ``objects`` (the attached ``EsManager``) is
#: deliberately not tracked here: it is a manager instance, not a scalar/list
#: config value, so there is no sensible ``objects=`` keyword to add in the
#: first place. ``tests/test_snap_model_decorator.py``'s drift guard asserts
#: every name here still exists on ``SnapModel`` (catching a stale entry) and
#: is never accepted as a decorator keyword (catching an entry #RFC1g already
#: closed that nobody remembered to remove from this set).
_SNAP_MODEL_UNEXPOSED_ATTRIBUTES: frozenset[str] = frozenset({
    # Elasticsearch — needs EsMirrorMixin, not just a keyword (#RFC1g row 1).
    "es_index_enabled", "es_storage_mode", "es_index_name", "es_mapping",
    "es_index_settings", "es_auto_mapping", "es_query_routing",
    # GDPR retention — needs a shared purge_expired attachment (#RFC1g row 2).
    "data_retention_days", "data_retention_field", "data_retention_files",
    # Generated admin — needs register_admin()/get_admin_fields() refactored
    # onto get_model_meta() before these mean anything for a plain model
    # (#RFC1g row 3).
    "admin_enabled", "admin_sections", "admin_tabs", "snap_inlines", "admin_mixins",
    "js_admin_files", "css_admin_files",
    "compressed_fields", "warn_unsaved_form", "list_filter_submit",
    "list_per_page", "list_max_show_all", "show_full_result_count",
})


def snap_model(
    *,
    api_exclude_fields: Sequence[str] | None = _UNSET,
    api_write_fields: Sequence[str] | None = _UNSET,
    api_read_only: bool = _UNSET,
    api_http_method_names: Sequence[str] | None = _UNSET,
    api_filter_lookups: Mapping[str, Sequence[str]] | None = _UNSET,
    api_default_text_lookups: Sequence[str] | None = _UNSET,
    api_json_filters: Mapping[str, Sequence[str]] | None = _UNSET,
    offline_mode: bool = _UNSET,
    offline_cache_limit: int = _UNSET,
    search_fields: Sequence[str] | None = _UNSET,
    subject_path: str | None = _UNSET,
    is_data_subject: bool = _UNSET,
    subject_identifier: str | None = _UNSET,
) -> Callable[[type[models.Model]], type[models.Model]]:
    """Opt a plain ``django.db.models.Model`` into SnapAdmin, without subclassing.

    :class:`SnapModel` is the declarative route: subclass it, declare ``Snap*Field``
    fields, and SnapAdmin generates everything. This decorator is the other route,
    for a model layer you cannot or do not want to rewrite — a brownfield schema, a
    model whose base class belongs to a third-party package, fields from
    ``django-money``/``phonenumber_field``/``model-utils``. The model stays a plain
    Django model; the decorator only registers it and records its configuration::

        from django.db import models
        from snapadmin.models import snap_model

        @snap_model(
            api_write_fields=["name", "price"],   # mass-assignment allowlist
            api_exclude_fields=["cost_price"],    # never leaves the server
            search_fields=["name"],               # what ?search= matches on
        )
        class Product(models.Model):
            name = models.CharField(max_length=200)
            price = models.DecimalField(max_digits=10, decimal_places=2)
            cost_price = models.DecimalField(max_digits=10, decimal_places=2)

    From that point the model is a SnapAdmin model everywhere the question is asked
    (:func:`snapadmin.registry.is_registered`): the REST API mounts CRUD routes for
    it, the GraphQL schema gains a type, the offline endpoints and the system checks
    see it, and ``snapadmin_info`` inventories it. Adding no field and no attribute,
    the decorator needs no migration.

    **What it does not give you.** This is registration and metadata only — it
    attaches none of :class:`SnapModel`'s runtime machinery, and the surfaces that
    need that machinery skip a decorated plain model rather than half-work:

    * **No Elasticsearch.** No :class:`EsManager` / :class:`EsQuerySet`, so no
      ``es_search()``, no ``es_reindex_all()``, no mirroring on save, and no index
      created on ``post_migrate``. ``snapadmin_reindex`` will not select it. That is
      why this decorator accepts no ``es_*`` keywords: storing them would promise
      indexing that never happens.
    * **No retention purge.** No ``purge_expired()``, so neither the
      ``snapadmin_purge_expired_data`` command nor the ``purge_expired_data`` task
      touches it — hence no ``data_retention_days``/``data_retention_files``
      keyword either.
    * **No generated admin.** ``SnapModel.register_all_admins()`` skips it: without
      ``Snap*Field`` flags there is nothing to derive fieldsets, list columns or
      filters from. Register a ``ModelAdmin`` for it yourself, as usual.
    * **No admin niceties** that live on the base class — ``formatted_id``, the
      audit/PII ``save()`` hooks, ``admin_overrides``.

    Needing any of those means subclassing :class:`SnapModel` (or adding an
    explicit manager and hooks of your own); everything above stays reachable by
    doing so later, since both routes end up in the same registry.

    Every keyword mirrors the :class:`SnapModel` class attribute of the same name
    and is read back through :func:`snapadmin.registry.get_model_meta`. Only the
    keywords actually passed are recorded, so applying the decorator to a
    ``SnapModel`` subclass overrides exactly those and leaves the rest of the
    class-level configuration in place.

    ``subject_path``/``is_data_subject``/``subject_identifier`` are the one
    exception to "mirrors a ``SnapModel`` class attribute": :class:`SnapModel`
    declares **no** default for ``subject_path`` on purpose, so a subclass that
    never sets it is indistinguishable from one that never even considered it —
    ``check_subject_paths`` (``snapadmin.E011``) catches exactly that silence on
    *either* registration door. Declaring is mandatory once a model is
    registered at all; there is no safe implicit default for "does this model
    carry personal data reachable from a subject" the way there is for, say,
    ``api_read_only=False``.

    :param api_exclude_fields: Field names kept out of the REST serializer, the
        GraphQL type and the schema endpoint.
    :param api_write_fields: Mass-assignment allowlist; ``None`` (the default)
        leaves every non-excluded field writable.
    :param api_read_only: Serve the model over safe HTTP methods only.
    :param api_http_method_names: Explicit lowercase HTTP-verb allowlist; wins
        over ``api_read_only``.
    :param api_filter_lookups: Per-field query-filter lookups,
        ``{"name": ["exact", "icontains"]}``.
    :param api_default_text_lookups: Lookup set for every text field not named in
        ``api_filter_lookups``.
    :param api_json_filters: Filterable key-paths inside JSON columns,
        ``{"payload": ["a.b"]}``.
    :param offline_mode: Expose the model to the admin's offline cache.
    :param offline_cache_limit: How many recent rows the offline cache prefetches.
    :param search_fields: Field names DRF's ``?search=`` matches against. A plain
        model has no ``searchable=True`` Snap fields to derive this from, so
        without it ``?search=`` is a no-op for the model.
    :param subject_path: A forward-only, ``__``-joined ORM lookup path (at most
        three relation hops) from this model to the field carrying the GDPR
        subject's identifying value, e.g. ``"customer__email"``. ``None`` is a
        valid, explicit declaration ("this model carries nothing subject-scoped");
        never leave it undeclared. A path with zero ``__`` segments names a field
        on this model directly (the value-match case, e.g. an audit row storing
        a copied email).
    :param is_data_subject: Marks this model as a valid subject-access-request
        entry point — an operator may run "everything for the person identified
        by X" against it. Requires ``subject_identifier`` and
        ``subject_path == subject_identifier`` (enforced by
        ``check_subject_paths``, not merely documented).
    :param subject_identifier: The field name on *this* model holding the raw
        identifier value, required when ``is_data_subject=True``.
    :raises TypeError: if applied to anything that is not a ``models.Model``
        subclass.
    """
    meta: dict[str, Any] = {
        "api_exclude_fields": _as_list(api_exclude_fields),
        "api_write_fields": _as_list(api_write_fields),
        "api_read_only": api_read_only,
        "api_http_method_names": _as_list(api_http_method_names),
        "api_filter_lookups": _as_lookup_map(api_filter_lookups),
        "api_default_text_lookups": _as_list(api_default_text_lookups),
        "api_json_filters": _as_lookup_map(api_json_filters),
        "offline_mode": offline_mode,
        "offline_cache_limit": offline_cache_limit,
        "search_fields": _as_list(search_fields),
        "subject_path": subject_path,
        "is_data_subject": is_data_subject,
        "subject_identifier": subject_identifier,
    }
    given = {name: value for name, value in meta.items() if value is not _UNSET}

    def decorator(model: type[models.Model]) -> type[models.Model]:
        if not (isinstance(model, type) and issubclass(model, models.Model)):
            raise TypeError(
                "@snap_model can only decorate a django.db.models.Model subclass, "
                f"got {model!r}."
            )
        register(model, **given)
        logger.debug(
            "snap_model_registered",
            model=f"{model._meta.app_label}.{model.__name__}",
            settings=sorted(given),
        )
        return model

    return decorator


# ===========================================================================
# A computed column as a method — @snap_property
# ===========================================================================

def snap_property(
    *,
    verbose_name: str | None = None,
    show_in_list: bool = True,
    show_in_form: bool = True,
    safe_html: bool = False,
) -> Callable[[Callable[[models.Model], Any]], "snapfields.SnapFunctionField"]:
    """Turn a method into a computed, display-only admin column.

    The decorator form of :class:`~snapadmin.fields.SnapFunctionField` — the
    same computed column (no database column, no migration, HTML-escaped
    unless ``safe_html=True``) written as a method instead of a field
    assignment::

        class OrderItem(SnapModel):
            quantity = SnapPositiveIntegerField(...)
            price = SnapDecimalField(...)

            @snap_property(verbose_name="Line total")
            def line_total(self):
                return f"{self.quantity * self.price:.2f}"

    is exactly::

            line_total = SnapFunctionField(
                func=lambda obj: f"{obj.quantity * obj.price:.2f}",
                verbose_name="Line total",
            )

    ``func`` receives the model instance exactly as an ordinary (undecorated)
    method receives ``self``, so the method body needs no change to become a
    computed column.

    **Not a second rendering path — #RFC1d's own constraint.** The decorator
    builds the identical :class:`~snapadmin.fields.SnapFunctionField` instance
    the field form builds and stores it under the method's name, so it is
    picked up by the exact code that already scans a model's class attributes
    for one (:meth:`SnapModel.get_admin_fields`) — that enumeration is
    untouched. This works unmodified on *either* door: a
    :class:`SnapFunctionField` is not a ``models.Field`` (nothing in its MRO
    defines ``contribute_to_class``), so assigning one as a class attribute —
    whether the class is a :class:`SnapModel` subclass or a plain model
    decorated with :func:`snap_model` — triggers no Django field machinery
    either way. It never appears in ``_meta.get_fields()`` and produces no
    migration, on both routes.

    **What still differs between the two doors is the surface, not the
    computation.** A :class:`SnapModel` subclass renders the column immediately
    (``get_admin_fields()`` puts it in ``list_display``, escaped like any other
    ``safe_html=False`` value). A plain model decorated with :func:`snap_model`
    has no ``register_admin()``/``get_admin_fields()`` at all yet — that gap is
    #RFC1g3, not this task — so the same ``@snap_property`` there computes
    correctly (``field.get_display_value(instance)`` returns the right value
    today, right now) but has nowhere to display until #RFC1g3 ships. Recording
    the value here rather than promising a render that does not exist yet.

    :param verbose_name: Column heading. Defaults to the method name with
        underscores turned into spaces, matching Django's own field default.
    :param show_in_list: Include the column on the admin changelist. Default
        ``True``.
    :param show_in_form: Accepted for parity with :class:`SnapFunctionField`'s
        constructor; that field is never included in ``form_fields`` (it is
        not a real ``_meta`` field), so this has no observable effect today.
    :param safe_html: Trust the returned value as pre-sanitised HTML instead of
        escaping it before display. Default ``False``.
    """

    def decorator(func: Callable[[models.Model], Any]) -> "snapfields.SnapFunctionField":
        name = getattr(func, "__name__", "") or ""
        resolved_verbose_name = verbose_name if verbose_name is not None else name.replace("_", " ")
        return snapfields.SnapFunctionField(
            func=func,
            verbose_name=resolved_verbose_name,
            show_in_list=show_in_list,
            show_in_form=show_in_form,
            safe_html=safe_html,
        )

    return decorator


def reindexable_snapmodels() -> list[type["SnapModel"]]:
    """Every SnapModel that maintains an Elasticsearch index.

    A model qualifies when it opts into ES via ``es_index_enabled`` or a
    non-``DB_ONLY`` storage mode (``DUAL`` / ``ES_ONLY``). Shared by the
    ``snapadmin_reindex`` management command, the ``run_es_reindex`` task and the
    admin reindex API so all three agree on what "ES-enabled" means.

    ``es_reindex_all`` comes from :class:`SnapModel`, so a plain model registered
    with :func:`snap_model` can never qualify — it has no index to rebuild. The
    check is explicit rather than implied by the ES flags, because those flags
    can be set on any registered model.
    """
    return [
        model
        for model in apps.get_models()
        if is_registered(model)
        and hasattr(model, "es_reindex_all")
        and (
            get_model_meta(model, "es_index_enabled", False)
            or get_model_meta(model, "es_storage_mode", EsStorageMode.DB_ONLY) != EsStorageMode.DB_ONLY
        )
    ]


def run_reindex(*, chunk_size: int = 500) -> dict:
    """Bulk-reindex every ES-enabled SnapModel; return a per-model summary.

    Shared by the admin reindex API and the ``run_es_reindex`` Celery task so the
    synchronous and asynchronous paths behave identically. Each model's
    ``es_reindex_all`` summary is collected under its ``app_label.Model`` label;
    the top level reports how many models were indexed vs. errored.
    """
    results: dict[str, dict] = {}
    indexed_models = 0
    errored_models = 0
    for model in reindexable_snapmodels():
        label = f"{model._meta.app_label}.{model.__name__}"
        summary = model.es_reindex_all(chunk_size=chunk_size)
        results[label] = summary
        if summary.get("errors"):
            errored_models += 1
        elif not summary.get("skipped"):
            indexed_models += 1
    return {
        "models": len(results),
        "indexed_models": indexed_models,
        "errored_models": errored_models,
        "results": results,
    }


# ── Signals for Elasticsearch ──────────────────────────────────────────────

# Signals for Elasticsearch are now handled by SnapModel.save() and delete()
# to better support ES_ONLY mode and ensure correct transaction handling.
