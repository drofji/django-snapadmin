"""
snapadmin/models.py

Core module for SnapAdmin — an auto-registration layer on top of Django's built-in admin.
Provides:
  - SnapModel      : Abstract base model with automatic admin generation
  - SnapSaveMixin  : ModelAdmin mixin that logs detailed change history
  - formatted_id   : Display helper that renders IDs with faded leading zeros
"""

import secrets
import string
from datetime import timedelta
from enum import Enum

from django.apps import apps
from django.core.exceptions import ValidationError
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.conf import settings

from admin_auto_filters.filters import AutocompleteFilter
from rangefilter.filters import DateRangeFilter, NumericRangeFilter

from snapadmin import fields as snapfields

from snapadmin.fields import DjangoFieldAttributeEnum, SnapFieldAttributeEnum, SnapField


# ===========================================================================
# API Token Models
# ===========================================================================

def validate_allowed_models(value):
    """
    Validate that each entry in allowed_models is a valid 'app_label.ModelName'.
    """
    if not isinstance(value, list):
        raise ValidationError(_("Allowed models must be a list."))

    for item in value:
        if not isinstance(item, str) or "." not in item:
            raise ValidationError(
                _("Invalid model format: '%(item)s'. Expected 'app_label.ModelName'."),
                params={"item": item},
            )

        parts = item.split(".")
        if len(parts) != 2:
            raise ValidationError(
                _("Invalid model format: '%(item)s'. Expected 'app_label.ModelName'."),
                params={"item": item},
            )

        app_label, model_name = parts
        try:
            apps.get_model(app_label, model_name)
        except LookupError:
            raise ValidationError(
                _("Model '%(item)s' does not exist or is not registered."),
                params={"item": item},
            )


def _generate_token_key() -> str:
    """
    Generate a cryptographically secure 40-character alphanumeric token.

    Returns:
        A random token string using letters and digits.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(40))


class APIToken(models.Model):
    """
    A named, user-scoped API token for authenticating against the SnapAdmin REST API.

    Fields:
        token_name      : Human-readable label for the token (e.g. "CI Pipeline Key").
        token_key       : The secret 40-character key presented in request headers.
        user            : Django auth user who owns this token.
        expiration_date : Optional expiry. When NULL the token never expires.
        allowed_models  : JSON list of "<app_label>.<ModelName>" strings this token
                          may access (empty list = all models allowed).
        is_active       : Soft-disable a token without deleting it.
        created_at      : Immutable creation timestamp.
        last_used_at    : Updated automatically on every authenticated request.

    Authentication header:
        Authorization: Token <token_key>
    """

    token_name = models.CharField(
        max_length=100,
        verbose_name=_("Token Name"),
        help_text=_("A descriptive name for this token (e.g. 'CI Pipeline', 'Read-only dashboard')."),
    )
    token_key = models.CharField(
        max_length=40,
        unique=True,
        default=_generate_token_key,
        verbose_name=_("Token Key"),
        help_text=_("Secret 40-character key. Treat like a password."),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="api_tokens",
        verbose_name=_("Owner"),
    )
    expiration_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Expiration Date"),
        help_text=_("Leave blank for a token that never expires."),
    )
    allowed_models = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_allowed_models],
        verbose_name=_("Allowed Models"),
        help_text=_(
            "List of 'app_label.ModelName' strings this token can access. "
            "An empty list grants access to all models."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Is Active"),
        help_text=_("Inactive tokens are rejected without being deleted."),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Used At"),
    )

    class Meta:
        verbose_name = _("API Token")
        verbose_name_plural = _("API Tokens")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.token_name} ({self.user.username})"

    # ── Validation helpers ───────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        """Return True if an expiration date is set and has passed."""
        if self.expiration_date is None:
            return False
        return timezone.now() > self.expiration_date

    @property
    def is_valid(self) -> bool:
        """Return True when the token is active and not expired."""
        return self.is_active and not self.is_expired

    def can_access_model(self, app_label: str, model_name: str) -> bool:
        """
        Check whether this token is permitted to access the given model.

        An empty ``allowed_models`` list means unrestricted access.

        Args:
            app_label:  The Django app label (e.g. "demo").
            model_name: The model class name (e.g. "Product").

        Returns:
            True if access is permitted.
        """
        if not self.allowed_models:
            return True
        return f"{app_label}.{model_name}" in self.allowed_models

    def touch(self) -> None:
        """Update ``last_used_at`` to the current time without loading the full object."""
        APIToken.objects.filter(pk=self.pk).update(last_used_at=timezone.now())

    # ── Factory helpers ──────────────────────────────────────────────────────

    @classmethod
    def create_for_user(
        cls,
        user: User,
        token_name: str,
        allowed_models: list = None,
        expires_in_days: int = None,
    ) -> "APIToken":
        """
        Convenience factory for creating tokens programmatically.

        Args:
            user:             The owner of the new token.
            token_name:       Human-readable name.
            allowed_models:   Optional model restriction list.
            expires_in_days:  If given, sets expiration_date this many days from now.

        Returns:
            The newly created APIToken instance (already saved).
        """
        expiration_date = None
        if expires_in_days is not None:
            expiration_date = timezone.now() + timedelta(days=expires_in_days)

        return cls.objects.create(
            user=user,
            token_name=token_name,
            allowed_models=allowed_models or [],
            expiration_date=expiration_date,
        )


# ===========================================================================
# Enums
# ===========================================================================

class SnapModelAttributeEnum(str, Enum):
    """Model-level attributes set/read by SnapAdmin internals."""

    ADMIN_OVERRIDES = "admin_overrides"


class EsStorageMode(str, Enum):
    """Modes for Elasticsearch integration."""

    DB_ONLY = "db_only"  # Standard Django behavior
    DUAL = "dual"        # Save to both DB and ES, search via ES
    ES_ONLY = "es_only"  # Save/retrieve only via ES, no DB table needed


class EsQuerySet:
    """A lightweight mock QuerySet for Elasticsearch-only models."""

    def __init__(self, model, hits=None):
        self.model = model
        self._hits = hits if hits is not None else []
        self.query = models.query.Query(model)  # Mock query for DRF
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
            except Exception:
                pass
        return len(self._hits), {self.model._meta.label: len(self._hits)}

    def filter(self, *args, **kwargs):
        return self

    def exclude(self, *args, **kwargs):
        return self

    def order_by(self, *field_names):
        return self

    def select_related(self, *fields):
        return self

    def prefetch_related(self, *lookups):
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
            except Exception:
                raise self.model.DoesNotExist
        raise self.model.DoesNotExist

    def exists(self):
        return bool(self._hits)

    @property
    def ordered(self):
        return True

    def none(self):
        return EsQuerySet(self.model, [])

    def all(self):
        return self

    def using(self, alias):
        return self


class EsManager(models.Manager):
    """Manager that uses Elasticsearch for ES_ONLY models."""

    def get_queryset(self):
        if getattr(self.model, "es_storage_mode", None) == EsStorageMode.ES_ONLY:
            qs = self.model.snap_search(limit=1000)
            if not isinstance(qs, EsQuerySet):
                return EsQuerySet(self.model, [])
            return qs
        return super().get_queryset()


class DjangoAdminClassAttributeEnum(str, Enum):
    """String keys used when constructing a dynamic ModelAdmin class."""

    FIELDS = "fields"
    LIST_DISPLAY = "list_display"
    SEARCH_FIELDS = "search_fields"
    LIST_FILTER = "list_filter"
    AUTOCOMPLETE_FIELDS = "autocomplete_fields"
    MEDIA_CLASS = "Media"
    CSS_MEDIA = "css"
    JS_MEDIA = "js"
    ALL_MEDIA = "all"
    MAX_SIZE_BYTES = "max_size_bytes"
    AUTOCOMPLETE = "autocomplete"
    INLINES = "inlines"


# ===========================================================================
# Display helpers
# ===========================================================================

@staticmethod
@admin.display(description="ID")
def formatted_id(obj):
    """
    Render a zero-padded 6-digit ID where leading zeros appear faded.

    Example: object with id=42 → '<span class="faded-zeros">0000</span>42'
    Requires the CSS class `.faded-zeros` to be defined in your admin stylesheet.
    """
    raw = f"{obj.id:06d}"

    # Split into leading zeros and the significant part
    significant_start = next((i for i, ch in enumerate(raw) if ch != "0"), len(raw))
    leading = raw[:significant_start]
    number = raw[significant_start:] or "0"

    return mark_safe(f'<span class="faded-zeros">{leading}</span>{number}')


# ===========================================================================
# Admin mixin — detailed change logging
# ===========================================================================

class SnapSaveMixin:
    """
    ModelAdmin mixin that writes human-readable field-level change records
    to Django's LogEntry table on every save.

    Works for both the main model form and any inline formsets.
    """

    # ------------------------------------------------------------------
    # Main model save
    # ------------------------------------------------------------------

    def save_model(self, request, obj, form, change):
        """
        After saving, log each changed field as 'field: old → new'.
        Skips logging when creating a new object (change=False).
        """
        if not change:
            super().save_model(request, obj, form, change)
            return

        # Collect human-readable change descriptions before saving
        change_lines = []
        for field_name in form.changed_data:
            old_val = form.initial.get(field_name)
            new_val = form.cleaned_data.get(field_name)

            if old_val != new_val:
                verbose = _(self.model._meta.get_field(field_name).verbose_name)
                change_lines.append(f"{verbose}: '{old_val}' -> '{new_val}'")

        super().save_model(request, obj, form, change)

        if change_lines:
            LogEntry.objects.log_action(
                user_id=request.user.id,
                content_type_id=ContentType.objects.get_for_model(obj).id,
                object_id=obj.pk,
                object_repr=str(obj),
                action_flag=CHANGE,
                change_message="\n".join(change_lines),
            )

    # ------------------------------------------------------------------
    # Inline / related model save
    # ------------------------------------------------------------------

    def save_related(self, request, form, formsets, change):
        """
        Mirror the same field-level logging for inline formsets.
        Failures are silently ignored so they never block a save.
        """
        for formset in formsets:
            if not formset.has_changed():
                continue

            for related_form in formset.forms:
                try:
                    instance = related_form.instance
                    if not (instance.pk and related_form.has_changed()):
                        continue

                    change_lines = []
                    for field_name in related_form.changed_data:
                        old_val = related_form.initial.get(field_name)
                        new_val = related_form.cleaned_data.get(field_name)

                        if old_val != new_val:
                            verbose = instance._meta.get_field(field_name).verbose_name
                            change_lines.append(f"{verbose}: '{old_val}' -> '{new_val}'")

                    if change_lines:
                        LogEntry.objects.log_action(
                            user_id=request.user.id,
                            content_type_id=ContentType.objects.get_for_model(instance).id,
                            object_id=instance.pk,
                            object_repr=str(instance),
                            action_flag=CHANGE,
                            change_message="\n".join(change_lines),
                        )
                except Exception:
                    pass  # Never let logging break a save

        super().save_related(request, form, formsets, change)


# ===========================================================================
# Base SnapModel
# ===========================================================================

class SnapModel(models.Model):
    """
    Abstract base model.  Subclasses automatically get a registered Django
    admin entry with list/search/filter/autocomplete wired up from field-level
    attributes declared via SnapField helpers.

    Class-level settings
    --------------------
    admin_enabled   : Set to False to skip admin registration entirely.
    js_admin_files  : Extra JS files appended to the admin Media class.
    css_admin_files : Extra CSS files appended to the admin Media class.
    snap_inlines    : List of Inline classes to include in the admin.
    admin_sections  : Reserved for future fieldset grouping support.
    """

    admin_enabled = True
    js_admin_files = []
    css_admin_files = []
    snap_inlines = []
    admin_sections = []

    objects = EsManager()

    # Elasticsearch integration
    es_index_enabled = False
    es_storage_mode = EsStorageMode.DB_ONLY
    es_index_name = None
    es_mapping = None  # Dict of field_name -> {"type": "..."}

    class Meta:
        abstract = True

    # ------------------------------------------------------------------
    # Elasticsearch methods
    # ------------------------------------------------------------------

    @classmethod
    def get_es_index_name(cls):
        return cls.es_index_name or f"snap_{cls._meta.app_label}_{cls._meta.model_name.lower()}"

    @classmethod
    def get_es_client(cls):
        from elasticsearch import Elasticsearch

        url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
        return Elasticsearch([url], request_timeout=5)

    def get_es_document(self):
        """
        Return a dictionary representing this object for Elasticsearch.
        Override this to customize the indexed data.
        """
        doc = {"id": self.pk}
        if self.es_mapping:
            for field_name in self.es_mapping.keys():
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
            if cls.es_mapping:
                body["mappings"]["properties"].update(cls.es_mapping)

            if not es.indices.exists(index=index_name):
                es.indices.create(index=index_name, body=body)
            else:
                # Update existing mapping (only adds new fields)
                es.indices.put_mapping(index=index_name, body=body["mappings"])
        except Exception:
            pass

    def index_in_es(self):
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
        except Exception:
            pass

    def delete_from_es(self):
        if (
            not (self.es_index_enabled or self.es_storage_mode != EsStorageMode.DB_ONLY)
            or not getattr(settings, "ELASTICSEARCH_ENABLED", False)
        ):
            return
        try:
            es = self.get_es_client()
            es.delete(index=self.get_es_index_name(), id=self.pk, ignore=[404])
        except Exception:
            pass

    def save(self, *args, **kwargs):
        if self.es_storage_mode == EsStorageMode.ES_ONLY:
            # Skip DB save for ES_ONLY models
            if not self.pk:
                # Generate a pseudo-random integer ID if not set
                import random

                self.pk = random.randint(100000, 999999)
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
    def snap_search(cls, query_string=None, limit=None):
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
                query = {"multi_match": {"query": query_string, "fields": ["*"], "fuzziness": "AUTO"}} if query_string else {"match_all": {}}
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
                    return EsQuerySet(cls, results)

                pks = [hit["_source"]["id"] for hit in hits]
                # Return queryset ordered by ES results
                preserved = models.Case(*[models.When(pk=pk, then=pos) for pos, pk in enumerate(pks)])
                return cls.objects.filter(pk__in=pks).order_by(preserved)
            except Exception:
                if cls.es_storage_mode == EsStorageMode.ES_ONLY:
                    return EsQuerySet(cls, [])

        # Fallback to DB search using search_fields (only for non-ES_ONLY)
        if cls.es_storage_mode == EsStorageMode.ES_ONLY:
            return EsQuerySet(cls, [])

        query_string = query_string or ""
        _, _, search_fields, _, _ = cls.get_admin_fields()
        q_objects = models.Q()
        for field in search_fields:
            if field == "id":
                try:
                    q_objects |= models.Q(id=int(query_string))
                except ValueError:
                    pass
                continue
            # Handle relational fields in search_fields if any (though they usually aren't here yet)
            q_objects |= models.Q(**{f"{field}__icontains": query_string})

        if q_objects:
            return cls.objects.filter(q_objects).distinct()
        return cls.objects.all()

    # ------------------------------------------------------------------
    # Human-readable representation
    # ------------------------------------------------------------------

    def __str__(self):
        """
        Return the most informative available string for this instance.
        Priority: name → alias → last/first name → last/firstname → pk.
        """
        if getattr(self, "name", None):
            return str(self.name)
        if getattr(self, "alias", None):
            return str(self.alias)
        if getattr(self, "first_name", None) and getattr(self, "last_name", None):
            return f"{self.last_name}, {self.first_name}"
        if getattr(self, "firstname", None) and getattr(self, "lastname", None):
            return f"{self.lastname}, {self.firstname}"
        return super().__str__()

    # ==================================================================
    # Admin field introspection
    # ==================================================================

    @classmethod
    def get_admin_fields(cls):
        """
        Inspect the model's fields and class attributes to derive five
        lists consumed by :meth:`register_admin`:

        Returns
        -------
        tuple
            (form_fields, list_display, search_fields, list_filter, autocomplete_fields)
        """

        # Separate scalar/FK fields from reverse/M2M relations
        meta_fields = {
            f.name: f
            for f in cls._meta.get_fields()
            if hasattr(f, "name") and not (f.one_to_many or f.one_to_one or f.many_to_many)
        }
        meta_fields_related = {
            f.name: f
            for f in cls._meta.get_fields()
            if hasattr(f, "name") and (f.many_to_one or f.many_to_many)
        }
        attr_fields = {
            fn: fo
            for fn, fo in cls.__dict__.items()
        }

        # --------------------------------------------------------------
        # Form fields — only fields explicitly flagged show_in_form=True
        # --------------------------------------------------------------
        form_fields = [
            fn for fn, fo in meta_fields.items()
            if getattr(fo, SnapFieldAttributeEnum.SHOW_IN_FORM.value, None)
        ]

        # --------------------------------------------------------------
        # List display — all fields except those flagged show_in_list=False
        # --------------------------------------------------------------
        list_display = [
            fn for fn, fo in meta_fields.items()
            if getattr(fo, SnapFieldAttributeEnum.SHOW_IN_LIST.value, True)
        ]

        # --------------------------------------------------------------
        # Search fields — flagged searchable=True, always include 'id'
        # --------------------------------------------------------------
        search_fields = [
            fn for fn, fo in meta_fields.items()
            if getattr(fo, SnapFieldAttributeEnum.SEARCHABLE.value, False)
        ]
        if "id" not in search_fields:
            search_fields.append("id")

        # --------------------------------------------------------------
        # Read-only fields — injected as a dynamic method on the class
        #   • editable=False  → always read-only in create AND update forms
        #   • updatable=False → read-only only after the object already exists
        #
        # NOTE: ForeignKey fields live in meta_fields_related, not meta_fields,
        # so we merge both dicts for the readonly check to cover all field types.
        all_fields_for_readonly = {**meta_fields, **meta_fields_related}

        editable_fields = [
            fn for fn, fo in all_fields_for_readonly.items()
            if not getattr(fo, SnapFieldAttributeEnum.EDITABLE.value, False)
        ]
        updatable_fields = [
            fn for fn, fo in all_fields_for_readonly.items()
            if not getattr(fo, SnapFieldAttributeEnum.UPDATABLE.value, True)
        ]

        if 'customer' in attr_fields:
            pass  # Reserved for future FK attribute inspection

        def dynamic_get_readonly_fields(self, request, obj=None):
            readonly_fields = [
                fn for fn, fo in all_fields_for_readonly.items()
                if fn in editable_fields or (fn in updatable_fields and obj and obj.pk)
            ]
            return readonly_fields

        if not hasattr(cls, SnapModelAttributeEnum.ADMIN_OVERRIDES.value):
            cls.admin_overrides = {}
        cls.admin_overrides["get_readonly_fields"] = dynamic_get_readonly_fields

        # --------------------------------------------------------------
        # List filters — type-aware: date ranges, numeric ranges,
        #                FK autocomplete, or plain choice filter
        # --------------------------------------------------------------
        list_filter = []
        for field_name, field in meta_fields.items():
            if not getattr(field, SnapFieldAttributeEnum.FILTERABLE.value, False):
                continue

            if isinstance(field, (models.DateField, models.DateTimeField, models.TimeField)):
                list_filter.append((field_name, DateRangeFilter))

            elif isinstance(field, (models.IntegerField, models.FloatField, models.DecimalField)):
                list_filter.append((field_name, NumericRangeFilter))

            elif isinstance(field, models.ForeignKey):
                # Dynamically create an AutocompleteFilter subclass for this FK
                filter_cls = type(
                    f"{field_name.capitalize()}Filter",
                    (AutocompleteFilter,),
                    {
                        "title": field.verbose_name or field_name,
                        "field_name": field_name,
                    },
                )
                if filter_cls not in list_filter:
                    list_filter.append(filter_cls)

            else:
                list_filter.append(field_name)

        # --------------------------------------------------------------
        # Autocomplete fields — FK/M2M fields with autocomplete=True
        # --------------------------------------------------------------
        autocomplete_fields = [
            fn for fn, fo in meta_fields_related.items()
            if getattr(fo, SnapFieldAttributeEnum.AUTOCOMPLETE.value, True)
        ]

        # --------------------------------------------------------------
        # SnapFunctionField — descriptor-declared computed columns
        # --------------------------------------------------------------
        for attr_name, attr_value in attr_fields.items():
            if not isinstance(attr_value, snapfields.SnapFunctionField):
                continue

            method_name = f"SnapFunctionField{attr_name.capitalize()}"

            def _make_display_method(field):
                """Closure so each loop iteration captures its own `field`."""

                @admin.display(description=getattr(field, "verbose_name", "") or getattr(field, "name", ""))
                def _display(self, obj):
                    return field.get_display_value(obj)

                return _display

            cls.admin_overrides.setdefault(method_name, _make_display_method(attr_value))
            list_display.append(method_name)

        # Always keep 'id' as the first column
        if "id" in list_display:
            list_display.remove("id")
        list_display.insert(0, "id")

        return form_fields, list_display, search_fields, list_filter, autocomplete_fields

    # ==================================================================
    # Admin registration
    # ==================================================================

    @classmethod
    def register_admin(cls):
        """
        Dynamically build and register a ModelAdmin for this model.

        Skips silently if:
        - ``admin_enabled`` is False
        - The model is already registered (AlreadyRegistered)
        """
        if not cls.admin_enabled:
            return

        form_fields, list_display, search_fields, list_filter, autocomplete_fields = cls.get_admin_fields()

        # ------------------------------------------------------------------
        # Static assets bundled with every SnapAdmin page
        # ------------------------------------------------------------------
        BASE_JS = [
            "admin/js/vendor/jquery/jquery.js",
            "admin/js/jquery.init.js",
            "snapadmin/js/jquery_bridge.js",
            "snapadmin/js/select2.min.js",
            "snapadmin/js/admin.js",
        ]
        BASE_CSS = [
            "snapadmin/css/select2.min.css",
            "snapadmin/css/admin.css",
        ]

        # Merge model-specific files, preserving order and removing duplicates
        extra_js = [cls.js_admin_files] if isinstance(cls.js_admin_files, str) else list(cls.js_admin_files)
        extra_css = [cls.css_admin_files] if isinstance(cls.css_admin_files, str) else list(cls.css_admin_files)

        final_js = list(dict.fromkeys(BASE_JS + extra_js))
        final_css = list(dict.fromkeys(BASE_CSS + extra_css))

        # ------------------------------------------------------------------
        # Assemble the ModelAdmin attribute dict
        # ------------------------------------------------------------------
        A = DjangoAdminClassAttributeEnum  # local alias for readability

        admin_attrs = {
            A.FIELDS.value: form_fields,
            A.LIST_DISPLAY.value: list_display,
            A.SEARCH_FIELDS.value: search_fields,
            A.LIST_FILTER.value: list_filter,
            A.AUTOCOMPLETE_FIELDS.value: autocomplete_fields,
            A.INLINES.value: cls.snap_inlines,
            "formatted_id": formatted_id,
            # Inline Media class with merged JS/CSS
            A.MEDIA_CLASS.value: type(A.MEDIA_CLASS.value, (), {
                A.CSS_MEDIA.value: {A.ALL_MEDIA.value: final_css},
                A.JS_MEDIA.value: final_js,
            }),
        }

        # Apply any overrides registered via get_admin_fields() or manually set
        admin_attrs.update(getattr(cls, "admin_overrides", {}))

        # Build the ModelAdmin class dynamically and register it
        admin_class = type(
            f"{cls.__name__}Admin",
            (SnapSaveMixin, admin.ModelAdmin),
            admin_attrs,
        )

        try:
            admin.site.register(cls, admin_class)
        except admin.sites.AlreadyRegistered:
            pass

    # ==================================================================
    # Batch registration
    # ==================================================================

    @staticmethod
    def register_all_admins(app_label=None):
        """
        Register admin pages for every SnapModel subclass found in the app registry.

        Parameters
        ----------
        app_label : str, optional
            When provided, only models belonging to this app are registered.
        """
        for model in apps.get_models():
            if issubclass(model, SnapModel) and model is not SnapModel:
                if app_label is None or model._meta.app_label == app_label:
                    model.register_admin()


# ── Signals for Elasticsearch ──────────────────────────────────────────────

# Signals for Elasticsearch are now handled by SnapModel.save() and delete()
# to better support ES_ONLY mode and ensure correct transaction handling.
