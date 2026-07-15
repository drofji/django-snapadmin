"""
snapadmin/models.py
Core module for SnapAdmin — an auto-registration layer on top of Django's built-in admin with Unfold integration.
"""

import hashlib
import secrets
import string
import uuid
from datetime import timedelta
from enum import Enum

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
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
from snapadmin.fields import DjangoFieldAttributeEnum, SnapFieldAttributeEnum, SnapField
from snapadmin.logging_config import get_logger
from snapadmin.pagination import EstimatedCountPaginator
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

    @classmethod
    def create_for_user(
        cls,
        user: AbstractBaseUser,
        token_name: str,
        allowed_models: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> "APIToken":
        expiration_date = None
        if expires_in_days is not None:
            expiration_date = timezone.now() + timedelta(days=expires_in_days)
        return cls.objects.create(user=user, token_name=token_name, allowed_models=allowed_models or [], expiration_date=expiration_date)

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


class SnapExportJob(models.Model):
    """A background CSV/JSON export of a model's rows.

    Created via ``POST /api/exports/``; a Celery task
    (``snapadmin.run_export``) fills it in chunk by chunk, updating
    ``processed_rows`` so ``GET /api/exports/<id>/`` can report live progress and
    an ETA. Fault-tolerant: the writer resumes from the ``cursor_pk`` /
    ``cursor_bytes`` checkpoint (not the ``processed_rows`` counter) so a retry
    never duplicates or skips a row. Cancellable: setting ``status`` to
    ``cancelled`` stops the task between chunks. See :mod:`snapadmin.exporting`.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        JSON = "json", "JSON"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    app_label = models.CharField(max_length=100, verbose_name=_("App Label"))
    model = models.CharField(max_length=100, verbose_name=_("Model"))
    export_format = models.CharField(max_length=8, choices=Format.choices, default=Format.CSV, verbose_name=_("Format"))
    filters = models.JSONField(default=dict, blank=True, verbose_name=_("Filters"), help_text=_("ORM field=value filters applied to the export queryset."))
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True, verbose_name=_("Status"))
    total_rows = models.PositiveIntegerField(default=0, verbose_name=_("Total Rows"))
    processed_rows = models.PositiveIntegerField(default=0, verbose_name=_("Processed Rows"), help_text=_("Rows written so far — drives progress-percent and ETA reporting."))
    # Crash-safe resume checkpoint (see snapadmin.exporting). cursor_pk is the
    # primary key of the last exported row, used for pk__gt cursor pagination on
    # resume (no OFFSET drift); cursor_bytes is the working file's byte length
    # confirmed at that pk, used to truncate any uncheckpointed tail on resume.
    # Stored as a string so any primary-key type (int / UUID / char) round-trips.
    cursor_pk = models.CharField(max_length=255, blank=True, default="", verbose_name=_("Resume Cursor (PK)"), help_text=_("Primary key of the last exported row; blank means start from the beginning."))
    cursor_bytes = models.PositiveBigIntegerField(default=0, verbose_name=_("Resume Byte Offset"), help_text=_("Byte length of the working file confirmed at cursor_pk."))
    file_name = models.CharField(max_length=255, blank=True, verbose_name=_("File Name"))
    error = models.TextField(blank=True, verbose_name=_("Error"))
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=_("Requested By"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("Created At"))
    started_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Started At"))
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Finished At"))

    class Meta:
        verbose_name = _("Export Job")
        verbose_name_plural = _("Export Jobs")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Export {self.app_label}.{self.model} [{self.status}] {self.processed_rows}/{self.total_rows}"

    def target_model(self):
        """Resolve the model being exported (raises LookupError if unknown)."""
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


class EsStorageMode(str, Enum):
    """Modes for Elasticsearch integration."""

    DB_ONLY = "db_only"  # Standard Django behavior
    DUAL = "dual"        # Save to both DB and ES, search via ES
    ES_ONLY = "es_only"  # Save/retrieve only via ES, no DB table needed


class EsQuerySet:
    """A lightweight mock QuerySet for Elasticsearch-only models."""

    def __init__(self, model, hits=None):
        from django.db.models.sql.query import Query
        self.model = model
        self._hits = hits if hits is not None else []
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
            return EsQuerySet(self.model, self._hits[k])
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
        return self._clone(new_hits)

    def exclude(self, *args, **kwargs):
        return self

    def order_by(self, *field_names):
        return self

    def select_related(self, *fields):
        return self

    def prefetch_related(self, *lookups):
        return self

    def _clone(self, hits=None):
        return EsQuerySet(self.model, hits if hits is not None else self._hits)

    def using(self, alias):
        return self

    def none(self):
        return self._clone([])

    def all(self):
        return self

    def get(self, *args, **kwargs):
        pk = kwargs.get("pk") or kwargs.get("id")
        if pk:
            try:
                es = self.model.get_es_client()
                hit = es.get(index=self.model.get_es_index_name(), id=str(pk))
                data = hit["_source"]
                obj = self.model(**{k: v for k, v in data.items() if k != "id"})
                obj.pk = data.get("id")
                return obj
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
        raise self.model.DoesNotExist

    def exists(self) -> bool:
        return bool(self._hits)

    @property
    def ordered(self) -> bool:
        return True


class EsManager(models.Manager):
    """Manager that uses Elasticsearch for ES_ONLY models."""

    def get_queryset(self):
        if getattr(self.model, "es_storage_mode", None) == EsStorageMode.ES_ONLY:
            limit = getattr(settings, "SNAPADMIN_ES_SEARCH_LIMIT", 1000)
            qs = self.model.es_search(limit=limit)
            if not isinstance(qs, EsQuerySet):
                return EsQuerySet(self.model, [])
            return qs
        # No default ordering is injected here. A default ``order_by("-pk")`` on
        # the base manager leaks into ``GROUP BY`` for ``.values().annotate()``
        # aggregations (Django appends ordering columns to the GROUP BY), which
        # silently returns one row per pk instead of per group. The "-pk" newest-
        # first default is applied in the presentation layers that need a stable
        # order instead (admin changelist ``ordering`` and the API list view).
        return super().get_queryset()


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
    return val  # pragma: no cover

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
    values in both views. Uses :mod:`snapadmin.masking`.
    """

    def _snap_masked_fields(self) -> list[str]:
        from snapadmin.masking import get_masked_fields
        return get_masked_fields(self.model._meta.app_label, self.model._meta.model_name)

    @staticmethod
    def _snap_mask_column(field_name):
        from snapadmin.masking import mask_value

        def column(obj):
            return mask_value(getattr(obj, field_name, None))

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
            self._snap_mask_column(name) if name in masked else name
            for name in display
        ]


class SnapSaveMixin:
    def save_model(self, request, obj, form, change):
        if not change:
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
    admin_enabled = True
    js_admin_files = []
    css_admin_files = []
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
    # Left as None (the default), every text field uses the library default
    # lookup set.
    api_filter_lookups: dict[str, list[str]] | None = None

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

    @classmethod
    def is_concrete_subclass(cls, model: type) -> bool:
        return issubclass(model, SnapModel) and model is not SnapModel

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
        factory_path = getattr(settings, "SNAPADMIN_ES_CLIENT_FACTORY", None)
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
        _, _, search_fields, _, _ = cls.get_admin_fields()
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
    def es_reindex_all(cls, *, chunk_size: int = 500) -> dict:
        """Synchronise all records to the Elasticsearch index.

        Uses the bulk API (one round-trip per ``chunk_size`` documents) with a
        streaming ``.iterator()`` over the table, so re-indexing millions of
        rows neither floods ES with per-row requests nor loads the whole
        queryset into memory.
        """
        if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            return {"skipped": True, "reason": "Elasticsearch not available"}

        from elasticsearch import helpers

        es = cls.get_es_client()
        cls._ensure_es_index_and_mapping()
        index_name = cls.get_es_index_name()

        qs = cls.objects.all()
        # EsQuerySet (ES_ONLY models) has no .iterator(); DB querysets stream.
        rows = iter(qs) if isinstance(qs, EsQuerySet) else qs.iterator(chunk_size=chunk_size)

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
    def purge_expired(cls, *, now=None, dry_run: bool = False) -> int:
        """Delete records past this model's ``data_retention_days`` (GDPR).

        Removes rows older than the retention window — measured on
        ``data_retention_field`` — from **every** storage layer the model uses:

        * ``DB_ONLY`` — bulk delete from the database.
        * ``DUAL``    — bulk delete from the database **and** the ES mirror.
        * ``ES_ONLY`` — delete the matching documents from Elasticsearch.

        Returns the number of records purged (or that *would* be purged when
        ``dry_run=True``); returns ``0`` when retention is not configured. The
        count always reflects this model's own rows, never the cascade-inflated
        total that ``QuerySet.delete()`` reports when related rows are removed
        via ``on_delete=CASCADE``.

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

        qs = cls.objects.filter(**{f"{retention_field}__lt": cutoff})
        if dry_run:
            return qs.count()

        if cls.es_storage_mode == EsStorageMode.DUAL:
            pks = list(qs.values_list("pk", flat=True))
            count = len(pks)
            qs.delete()
            if not cls._delete_pks_from_es(pks):
                raise SnapPurgeError(
                    f"{cls.__name__}: {count} row(s) deleted from the database, "
                    "but the Elasticsearch mirror could not be cleared; personal "
                    "data may still be live and searchable via ES."
                )
            return count

        count = qs.count()
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
    def get_admin_fields(cls):
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

        if not hasattr(cls, SnapModelAttributeEnum.ADMIN_OVERRIDES.value): cls.admin_overrides = {}
        cls.admin_overrides["get_readonly_fields"] = dynamic_get_readonly_fields

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

                cls.admin_overrides[method_name] = make_wysiwyg_display(fn)
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
            cls.admin_overrides.setdefault(method_name, _make_display_method(attr_value))
            list_display.append(method_name)

        if "id" in list_display: list_display.remove("id")
        list_display.insert(0, "id")
        return form_fields, list_display, search_fields, list_filter, autocomplete_fields

    @classmethod
    def register_admin(cls) -> None:
        if not cls.admin_enabled: return
        form_fields, list_display, search_fields, list_filter, autocomplete_fields = cls.get_admin_fields()

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

        BASE_JS = ["admin/js/vendor/jquery/jquery.js", "admin/js/jquery.init.js", "snapadmin/js/jquery_bridge.js", "snapadmin/js/select2.min.js", "snapadmin/js/admin.js", "snapadmin/js/connectivity.js"]
        BASE_CSS = ["snapadmin/css/select2.min.css", "snapadmin/css/admin.css"]
        if UNFOLD_INSTALLED:
            # Unfold-specific overrides are opt-in: only layered on when the
            # Unfold theme is actually installed. Loaded after admin.css so its
            # `.unfold`-scoped rules win the cascade.
            BASE_CSS.append("snapadmin/css/admin-unfold.css")
        extra_js = [cls.js_admin_files] if isinstance(cls.js_admin_files, str) else list(cls.js_admin_files)
        extra_css = [cls.css_admin_files] if isinstance(cls.css_admin_files, str) else list(cls.css_admin_files)
        final_js = list(dict.fromkeys(BASE_JS + extra_js))
        if cls.offline_mode:
            final_js.append("snapadmin/js/offline.js")
        final_css = list(dict.fromkeys(BASE_CSS + extra_css))

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
            if issubclass(model, SnapModel) and model is not SnapModel:
                if app_label is None or model._meta.app_label == app_label:
                    model.register_admin()


def reindexable_snapmodels() -> list[type["SnapModel"]]:
    """Every SnapModel that maintains an Elasticsearch index.

    A model qualifies when it opts into ES via ``es_index_enabled`` or a
    non-``DB_ONLY`` storage mode (``DUAL`` / ``ES_ONLY``). Shared by the
    ``snapadmin_reindex`` management command, the ``run_es_reindex`` task and the
    admin reindex API so all three agree on what "ES-enabled" means.
    """
    return [
        model
        for model in apps.get_models()
        if issubclass(model, SnapModel)
        and model is not SnapModel
        and (
            getattr(model, "es_index_enabled", False)
            or getattr(model, "es_storage_mode", EsStorageMode.DB_ONLY) != EsStorageMode.DB_ONLY
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
