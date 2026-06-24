"""
snapadmin/models.py
Core module for SnapAdmin — an auto-registration layer on top of Django's built-in admin with Unfold integration.
"""

import secrets
import string
from datetime import timedelta
from enum import Enum

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
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

from django_ckeditor_5.widgets import CKEditor5Widget

from snapadmin import fields as snapfields
from snapadmin.fields import DjangoFieldAttributeEnum, SnapFieldAttributeEnum, SnapField


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

def _generate_token_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(40))

class APIToken(models.Model):
    token_name = models.CharField(max_length=100, verbose_name=_("Token Name"), help_text=_("A descriptive name for this token (e.g. 'CI Pipeline', 'Read-only dashboard')."))
    token_key = models.CharField(max_length=40, unique=True, default=_generate_token_key, verbose_name=_("Token Key"), help_text=_("Secret 40-character key. Treat like a password."))
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens", verbose_name=_("Owner"))
    expiration_date = models.DateTimeField(null=True, blank=True, verbose_name=_("Expiration Date"), help_text=_("Leave blank for a token that never expires."))
    allowed_models = models.JSONField(default=list, blank=True, validators=[validate_allowed_models], verbose_name=_("Allowed Models"), help_text=_("List of 'app_label.ModelName' strings this token can access."))
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"), help_text=_("Inactive tokens are rejected without being deleted."))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Last Used At"))

    class Meta:
        verbose_name = _("API Token")
        verbose_name_plural = _("API Tokens")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.token_name} ({self.user.username})"

    @property
    def is_expired(self) -> bool:
        if self.expiration_date is None:
            return False
        return timezone.now() > self.expiration_date

    @property
    def is_valid(self) -> bool:
        return self.is_active and not self.is_expired

    def can_access_model(self, app_label: str, model_name: str) -> bool:
        if not self.allowed_models: return True
        return f"{app_label}.{model_name}" in self.allowed_models

    def touch(self) -> None:
        APIToken.objects.filter(pk=self.pk).update(last_used_at=timezone.now())

    @classmethod
    def create_for_user(
        cls,
        user: "User",
        token_name: str,
        allowed_models: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> "APIToken":
        expiration_date = None
        if expires_in_days is not None:
            expiration_date = timezone.now() + timedelta(days=expires_in_days)
        return cls.objects.create(user=user, token_name=token_name, allowed_models=allowed_models or [], expiration_date=expiration_date)

# ===========================================================================
# Enums & Helpers
# ===========================================================================

class SnapModelAttributeEnum(str, Enum):
    ADMIN_OVERRIDES = "admin_overrides"

    
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
            except Exception:
                pass
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
            except Exception:
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
            qs = self.model.es_search(limit=1000)
            if not isinstance(qs, EsQuerySet):
                return EsQuerySet(self.model, [])
            return qs
        qs = super().get_queryset()
        if not qs.ordered:
            qs = qs.order_by("-pk")
        return qs
      

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
    raw = f"{obj.id:06d}"
    significant_start = next((i for i, ch in enumerate(raw) if ch != "0"), len(raw))
    leading = raw[:significant_start]
    number = raw[significant_start:] or "0"
    val = mark_safe(f'<span class="faded-zeros">{leading}</span>{number}')
    if UNFOLD_INSTALLED:
        return [val, None, None]
    return val  # pragma: no cover

# ===========================================================================
# Admin Mixin
# ===========================================================================

class SnapSaveMixin:
    def save_model(self, request, obj, form, change):
        if not change:
            super().save_model(request, obj, form, change)
            return
        change_lines = []
        for field_name in form.changed_data:
            old_val = form.initial.get(field_name)
            new_val = form.cleaned_data.get(field_name)
            if old_val != new_val:
                verbose = _(self.model._meta.get_field(field_name).verbose_name)
                change_lines.append(f"{verbose}: '{old_val}' -> '{new_val}'")
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

    # DSGVO / GDPR data retention
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
    def get_es_index_name(cls) -> str:
        return cls.es_index_name or f"snap_{cls._meta.app_label}_{cls._meta.model_name.lower()}"

    @classmethod
    def get_es_client(cls):
        from elasticsearch import Elasticsearch

        url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
        return Elasticsearch([url], request_timeout=5)

    def get_es_document(self) -> dict:
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
        except Exception:
            pass

    def delete_from_es(self) -> None:
        if (
            not (self.es_index_enabled or self.es_storage_mode != EsStorageMode.DB_ONLY)
            or not getattr(settings, "ELASTICSEARCH_ENABLED", False)
        ):
            return
        try:
            es = self.get_es_client()
            es.delete(index=self.get_es_index_name(), id=self.pk, ignore=[404])
        except Exception: pass

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
        except Exception:
            pass
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
                try: q_objects |= models.Q(id=int(query_string))
                except ValueError: pass
                continue
            q_objects |= models.Q(**{f"{field}__icontains": query_string})

        if q_objects:
            return cls.objects.filter(q_objects).distinct()
        return cls.objects.all()

    @classmethod
    def snap_search(cls, query_string=None, limit=None):
        """Public alias for es_search — preferred entry point for external callers."""
        return cls.es_search(query_string=query_string, limit=limit)

    @classmethod
    def es_reindex_all(cls) -> dict:
        """Synchronise all records to the Elasticsearch index."""
        if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            return {"skipped": True, "reason": "Elasticsearch not available"}

        es = cls.get_es_client()
        qs = cls.objects.all()
        indexed = 0

        for obj in qs:
            obj.index_in_es()
            indexed += 1

        return {"indexed": indexed}

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
                        return mark_safe(getattr(obj, field_name, ""))
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
            "list_select_related": list_select_related or False,
            "list_per_page": cls.list_per_page,
            "list_max_show_all": cls.list_max_show_all,
            "show_full_result_count": cls.show_full_result_count,
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
                kwargs["widget"] = CKEditor5Widget(config_name="extends")
            return super(ModelAdmin, self).formfield_for_dbfield(db_field, request, **kwargs)

        def get_fieldsets(self, request, obj=None):
            # If we have rows, Unfold needs specific layout classes
            fs = super(ModelAdmin, self).get_fieldsets(request, obj)
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

        parent_classes = (SnapSaveMixin, ModelAdmin)
        admin_class = type(f"{cls.__name__}Admin", parent_classes, admin_attrs)
        try: admin.site.register(cls, admin_class)
        except admin.sites.AlreadyRegistered: pass

    @staticmethod
    def register_all_admins(app_label: str | None = None) -> None:
        from snapadmin.admin import APITokenAdmin
        try:
            admin.site.register(APIToken, APITokenAdmin)
        except admin.sites.AlreadyRegistered:
            pass

        for model in apps.get_models():
            if issubclass(model, SnapModel) and model is not SnapModel:
                if app_label is None or model._meta.app_label == app_label:
                    model.register_admin()


# ── Signals for Elasticsearch ──────────────────────────────────────────────

# Signals for Elasticsearch are now handled by SnapModel.save() and delete()
# to better support ES_ONLY mode and ensure correct transaction handling.
