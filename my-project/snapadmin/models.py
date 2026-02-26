"""
snapadmin/models.py

Core module for SnapAdmin — an auto-registration layer on top of Django's built-in admin.
Provides:
  - SnapModel      : Abstract base model with automatic admin generation
  - SnapSaveMixin  : ModelAdmin mixin that logs detailed change history
  - formatted_id   : Display helper that renders IDs with faded leading zeros
"""

from enum import Enum

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from admin_auto_filters.filters import AutocompleteFilter
from rangefilter.filters import DateRangeFilter, NumericRangeFilter

from snapadmin import fields as snapfields

from snapadmin.fields import DjangoFieldAttributeEnum, SnapFieldAttributeEnum, SnapModelAttributeEnum, SnapField


# ===========================================================================
# Enums
# ===========================================================================

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
    admin_sections  : Reserved for future fieldset grouping support.
    """

    admin_enabled = True
    js_admin_files = []
    css_admin_files = []
    admin_sections = []

    class Meta:
        abstract = True

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
        # TODO Future - Ускорить работу класса SnapModel
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
        #   • editable=False  → always read-only
        #   • updatable=False → read-only after the object already exists
        # --------------------------------------------------------------

        editable_fields = [
            fn for fn, fo in meta_fields.items()
            if not getattr(fo, SnapFieldAttributeEnum.EDITABLE.value, False)
        ]
        updatable_fields = [
            fn for fn, fo in meta_fields.items()
            if not getattr(fo, SnapFieldAttributeEnum.UPDATABLE.value, True)
        ]

        if 'customer' in attr_fields:
            print('CCCC', attr_fields['customer'].__dict__['field'].__dict__)

        def dynamic_get_readonly_fields(self, request, obj=None):
            readonly_fields = [
                fn for fn, fo in meta_fields.items()
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
