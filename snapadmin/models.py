"""
snapadmin/models.py
Core module for SnapAdmin — an auto-registration layer on top of Django's built-in admin with Unfold integration.
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

# Unfold imports
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    RangeDateFilter,
    RangeNumericFilter,
    TextFilter,
    RelatedDropdownFilter,
    ChoicesDropdownFilter,
)
from unfold.decorators import display as unfold_display
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
    def create_for_user(cls, user, token_name, allowed_models=None, expires_in_days=None):
        expiration_date = None
        if expires_in_days is not None:
            expiration_date = timezone.now() + timedelta(days=expires_in_days)
        return cls.objects.create(user=user, token_name=token_name, allowed_models=allowed_models or [], expiration_date=expiration_date)

# ===========================================================================
# Enums & Helpers
# ===========================================================================

class SnapModelAttributeEnum(str, Enum):
    ADMIN_OVERRIDES = "admin_overrides"

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
    return [mark_safe(f'<span class="faded-zeros">{leading}</span>{number}'), None, None]

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
            LogEntry.objects.log_action(
                user_id=request.user.id,
                content_type_id=ContentType.objects.get_for_model(obj).id,
                object_id=obj.pk,
                object_repr=str(obj),
                action_flag=CHANGE,
                change_message="\n".join(change_lines),
            )

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
                        LogEntry.objects.log_action(
                            user_id=request.user.id,
                            content_type_id=ContentType.objects.get_for_model(instance).id,
                            object_id=instance.pk,
                            object_repr=str(instance),
                            action_flag=CHANGE,
                            change_message="\n".join(change_lines),
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

    # Unfold specific
    compressed_fields = False
    warn_unsaved_form = True
    list_filter_submit = True
    admin_tabs = []

    # Elasticsearch integration
    es_index_enabled = False
    es_index_name = None
    es_mapping = None

    class Meta:
        abstract = True

    @classmethod
    def get_es_index_name(cls):
        return cls.es_index_name or f"snap_{cls._meta.app_label}_{cls._meta.model_name.lower()}"

    @classmethod
    def get_es_client(cls):
        from elasticsearch import Elasticsearch
        url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
        return Elasticsearch([url], request_timeout=5)

    def get_es_document(self):
        doc = {"id": self.pk}
        if self.es_mapping:
            for field_name in self.es_mapping.keys():
                val = getattr(self, field_name, None)
                if hasattr(val, "pk"): val = val.pk
                doc[field_name] = val
        return doc

    def index_in_es(self):
        if not self.es_index_enabled or not getattr(settings, "ELASTICSEARCH_ENABLED", False): return
        try:
            es = self.get_es_client()
            index_name = self.get_es_index_name()
            if not es.indices.exists(index=index_name):
                body = {}
                if self.es_mapping:
                    body["mappings"] = {"properties": self.es_mapping}
                    body["mappings"]["properties"]["id"] = {"type": "integer"}
                es.indices.create(index=index_name, body=body)
            es.index(index=index_name, id=self.pk, document=self.get_es_document())
        except Exception: pass

    def delete_from_es(self):
        if not self.es_index_enabled or not getattr(settings, "ELASTICSEARCH_ENABLED", False): return
        try:
            es = self.get_es_client()
            es.delete(index=self.get_es_index_name(), id=self.pk, ignore=[404])
        except Exception: pass

    @classmethod
    def snap_search(cls, query_string, limit=20):
        if cls.es_index_enabled and getattr(settings, "ELASTICSEARCH_ENABLED", False):
            try:
                es = cls.get_es_client()
                response = es.search(index=cls.get_es_index_name(), body={"query": {"multi_match": {"query": query_string, "fields": ["*"], "fuzziness": "AUTO"}}, "size": limit})
                hits = response.get("hits", {}).get("hits", [])
                pks = [hit["_source"]["id"] for hit in hits]
                preserved = models.Case(*[models.When(pk=pk, then=pos) for pos, pk in enumerate(pks)])
                return cls.objects.filter(pk__in=pks).order_by(preserved)
            except Exception: pass
        _, _, search_fields, _, _ = cls.get_admin_fields()
        q_objects = models.Q()
        for field in search_fields:
            if field == "id":
                try: q_objects |= models.Q(id=int(query_string))
                except ValueError: pass
                continue
            q_objects |= models.Q(**{f"{field}__icontains": query_string})
        return cls.objects.filter(q_objects).distinct()[:limit] if q_objects else cls.objects.all()[:limit]

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

        for attr_name, attr_value in attr_fields.items():
            if not isinstance(attr_value, snapfields.SnapFunctionField): continue
            method_name = f"SnapFunctionField{attr_name.capitalize()}"
            def _make_display_method(field):
                @unfold_display(description=getattr(field, "verbose_name", "") or getattr(field, "name", ""), header=True)
                def _display(self, obj): return [field.get_display_value(obj), None, None]
                return _display
            cls.admin_overrides.setdefault(method_name, _make_display_method(attr_value))
            list_display.append(method_name)

        if "id" in list_display: list_display.remove("id")
        list_display.insert(0, "id")
        return form_fields, list_display, search_fields, list_filter, autocomplete_fields

    @classmethod
    def register_admin(cls):
        if not cls.admin_enabled: return
        form_fields, list_display, search_fields, list_filter, autocomplete_fields = cls.get_admin_fields()

        # Build fieldsets based on 'tab' attribute
        tabs_map = {}
        untabbed_fields = []
        for field_name in form_fields:
            try:
                field = cls._meta.get_field(field_name)
                tab_name = getattr(field, "tab", None)
                if tab_name:
                    tabs_map.setdefault(tab_name, []).append(field_name)
                else:
                    untabbed_fields.append(field_name)
            except models.FieldDoesNotExist:
                untabbed_fields.append(field_name)

        fieldsets = []
        if untabbed_fields:
            fieldsets.append((None, {"fields": untabbed_fields}))

        for tab_name, fields in tabs_map.items():
            fieldsets.append((tab_name, {"fields": fields, "classes": ("tab",)}))

        BASE_JS = ["admin/js/vendor/jquery/jquery.js", "admin/js/jquery.init.js", "snapadmin/js/jquery_bridge.js", "snapadmin/js/select2.min.js", "snapadmin/js/admin.js"]
        BASE_CSS = ["snapadmin/css/select2.min.css", "snapadmin/css/admin.css"]
        extra_js = [cls.js_admin_files] if isinstance(cls.js_admin_files, str) else list(cls.js_admin_files)
        extra_css = [cls.css_admin_files] if isinstance(cls.css_admin_files, str) else list(cls.css_admin_files)
        final_js = list(dict.fromkeys(BASE_JS + extra_js))
        final_css = list(dict.fromkeys(BASE_CSS + extra_css))

        A = DjangoAdminClassAttributeEnum
        admin_attrs = {
            A.LIST_DISPLAY.value: list_display,
            A.SEARCH_FIELDS.value: search_fields,
            A.LIST_FILTER.value: list_filter,
            A.AUTOCOMPLETE_FIELDS.value: autocomplete_fields,
            A.INLINES.value: cls.snap_inlines,
            "compressed_fields": cls.compressed_fields,
            "warn_unsaved_form": cls.warn_unsaved_form,
            "list_filter_submit": cls.list_filter_submit,
            "tabs": cls.admin_tabs,
            "formatted_id": formatted_id,
            A.MEDIA_CLASS.value: type(A.MEDIA_CLASS.value, (), {A.CSS_MEDIA.value: {A.ALL_MEDIA.value: final_css}, A.JS_MEDIA.value: final_js}),
        }

        if fieldsets:
            admin_attrs[A.FIELDSETS.value] = fieldsets
        else:
            admin_attrs[A.FIELDS.value] = form_fields

        def formfield_for_dbfield(self, db_field, request, **kwargs):
            if isinstance(db_field, (models.TextField, snapfields.SnapTextField)) and getattr(db_field, "wysiwyg", False):
                kwargs["widget"] = CKEditor5Widget(config_name="extends")
            return super(ModelAdmin, self).formfield_for_dbfield(db_field, request, **kwargs)

        admin_attrs["formfield_for_dbfield"] = formfield_for_dbfield
        admin_attrs.update(getattr(cls, "admin_overrides", {}))

        admin_class = type(f"{cls.__name__}Admin", (SnapSaveMixin, ModelAdmin), admin_attrs)
        try: admin.site.register(cls, admin_class)
        except admin.sites.AlreadyRegistered: pass

    @staticmethod
    def register_all_admins(app_label=None):
        from snapadmin.admin import APITokenAdmin
        try:
            admin.site.register(APIToken, APITokenAdmin)
        except admin.sites.AlreadyRegistered:
            pass

        for model in apps.get_models():
            if issubclass(model, SnapModel) and model is not SnapModel:
                if app_label is None or model._meta.app_label == app_label:
                    model.register_admin()

@receiver(post_save)
def snap_model_post_save(sender, instance, **kwargs):
    if isinstance(instance, SnapModel): instance.index_in_es()

@receiver(post_delete)
def snap_model_post_delete(sender, instance, **kwargs):
    if isinstance(instance, SnapModel): instance.delete_from_es()
