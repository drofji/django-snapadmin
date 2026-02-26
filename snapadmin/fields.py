"""
snapadmin/fields.py

Custom field layer on top of Django's standard model fields.
Every Snap field transparently passes standard Django kwargs through while
also accepting a set of SnapAdmin-specific attributes that drive automatic
admin generation (list visibility, search, filters, etc.).

Public API
----------
- SnapField                   : Mixin that adds SnapAdmin behaviour to any Django field
- SnapFunctionField           : A computed, non-database column for the admin list view
- SnapStatusBadgeField        : Computed column that renders a coloured HTML badge
- SnapStatusBadgeFieldChoice  : Configuration for a single badge variant
- Snap*                       : Concrete field classes (one per Django field type)
"""

import typing
from enum import Enum

from django import forms
from django.db import models
from django.core import validators
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from snapadmin import validators as snap_validators


# ===========================================================================
# Enums
# ===========================================================================

class SnapFieldAttributeEnum(str, Enum):
    """SnapAdmin-specific keyword arguments accepted by every SnapField."""

    SHOW_IN_LIST = "show_in_list"
    SHOW_IN_FORM = "show_in_form"
    SEARCHABLE = "searchable"
    FILTERABLE = "filterable"
    EDITABLE = "editable"
    REQUIRED = "required"
    UPDATABLE = "updatable"
    ALLOWED_EXTENSIONS = "allowed_extensions"
    ALLOWED_ENCODINGS = "allowed_encodings"
    MAX_SIZE_BYTES = "max_size_bytes"
    AUTOCOMPLETE = "autocomplete"


class DjangoFieldAttributeEnum(str, Enum):
    """Standard Django field kwargs referenced inside SnapAdmin logic."""

    BLANK = "blank"
    NULL = "null"
    CHOICES = "choices"
    MAX_LENGTH = "max_length"
    VALIDATORS = "validators"
    AUTO_NOW = "auto_now"
    AUTO_NOW_ADD = "auto_now_add"


# ===========================================================================
# Base mixin
# ===========================================================================

class SnapField:
    """
    Mixin that adds SnapAdmin metadata to any Django model field.

    Usage
    -----
    Every concrete Snap field (e.g. SnapCharField) inherits both the
    corresponding Django field class and this mixin.  In ``__init__`` the
    field should call ``_initializeSnapLogic(**kwargs)`` first, then forward
    the filtered kwargs to Django via ``handleDjangoKwargs(**kwargs)``.

    Attribute defaults
    ------------------
    show_in_list  : True   — column appears in the admin changelist
    show_in_form  : False  — field appears in the admin change form
    searchable    : False  — field is included in admin search
    filterable    : False  — field gets a sidebar filter widget
    editable      : True   — field is editable in the admin form
    required      : False  — field may be left blank/null
    updatable     : True   — field may be changed after initial creation
    autocomplete  : False  — FK/M2M uses a Select2 autocomplete widget
    """

    # ------------------------------------------------------------------
    # Public entry point called by every Snap field's __init__
    # ------------------------------------------------------------------

    def _initializeSnapLogic(self, **kwargs) -> dict:
        """
        Run all SnapAdmin initialisation steps in sequence.
        Returns the kwargs dict with SnapAdmin keys consumed/adjusted.
        """
        kwargs = self.__handleRequiredFlag(**kwargs)
        kwargs = self.__applySnapDefaults(**kwargs)
        kwargs = self.__reinitializeAutocomplete(**kwargs)
        kwargs = self.__reinitializeAutoNow(**kwargs)

        # Store as instance attributes after all logic has run
        self.show_in_list = kwargs[SnapFieldAttributeEnum.SHOW_IN_LIST.value]
        self.show_in_form = kwargs[SnapFieldAttributeEnum.SHOW_IN_FORM.value]
        self.searchable = kwargs[SnapFieldAttributeEnum.SEARCHABLE.value]
        self.filterable = kwargs[SnapFieldAttributeEnum.FILTERABLE.value]
        self.editable = kwargs[SnapFieldAttributeEnum.EDITABLE.value]
        self.required = kwargs[SnapFieldAttributeEnum.REQUIRED.value]
        self.updatable = kwargs[SnapFieldAttributeEnum.UPDATABLE.value]
        self.autocomplete = kwargs[SnapFieldAttributeEnum.AUTOCOMPLETE.value]

        return kwargs

    # ------------------------------------------------------------------
    # Step 1: Apply defaults
    # ------------------------------------------------------------------

    def __applySnapDefaults(self, **kwargs) -> dict:
        """
        Apply sensible defaults to SnapAdmin-specific keys in kwargs.
        """
        snap_defaults = {
            SnapFieldAttributeEnum.SHOW_IN_LIST: True,
            SnapFieldAttributeEnum.SHOW_IN_FORM: False,
            SnapFieldAttributeEnum.SEARCHABLE: False,
            SnapFieldAttributeEnum.FILTERABLE: False,
            SnapFieldAttributeEnum.EDITABLE: True,
            SnapFieldAttributeEnum.REQUIRED: False,
            SnapFieldAttributeEnum.UPDATABLE: True,
            SnapFieldAttributeEnum.AUTOCOMPLETE: False,
        }

        for enum_attr, default_value in snap_defaults.items():
            kwargs.setdefault(enum_attr.value, default_value)

        return kwargs

    # ------------------------------------------------------------------
    # Step 2: Auto-enable autocomplete for fields with choices
    # ------------------------------------------------------------------

    @classmethod
    def __reinitializeAutocomplete(cls, **kwargs) -> dict:
        """
        Force autocomplete=True when a `choices` iterable is provided
        and the caller did not explicitly set autocomplete=False.
        """
        autocomplete_not_set = kwargs.get(SnapFieldAttributeEnum.AUTOCOMPLETE.value) is False
        has_choices = bool(kwargs.get(DjangoFieldAttributeEnum.CHOICES.value))

        if autocomplete_not_set and has_choices:
            kwargs[SnapFieldAttributeEnum.AUTOCOMPLETE.value] = True

        return kwargs

    # ------------------------------------------------------------------
    # Step 3: Mark auto-timestamp fields as non-editable
    # ------------------------------------------------------------------

    @classmethod
    def __reinitializeAutoNow(cls, **kwargs) -> dict:
        """
        Fields with auto_now or auto_now_add are managed by Django itself
        and must never be exposed as editable or updatable in the admin.
        """
        if kwargs.get(DjangoFieldAttributeEnum.AUTO_NOW) or kwargs.get(DjangoFieldAttributeEnum.AUTO_NOW_ADD):
            kwargs[SnapFieldAttributeEnum.EDITABLE.value] = False
            kwargs[SnapFieldAttributeEnum.UPDATABLE.value] = False

        return kwargs

    # ------------------------------------------------------------------
    # Helpers used by subclasses
    # ------------------------------------------------------------------

    @classmethod
    def __handleRequiredFlag(cls, **kwargs) -> dict:
        """
        Translate the SnapAdmin `required` flag into Django's blank/null pair.

        required=False  → blank=False, null=False  (default Django behaviour)
        required=True   → blank=True,  null=True   (allow empty in DB and forms)
        """
        if kwargs.get(SnapFieldAttributeEnum.REQUIRED.value) is True:
            kwargs.setdefault(DjangoFieldAttributeEnum.BLANK.value, False)
            kwargs.setdefault(DjangoFieldAttributeEnum.NULL.value, False)
        else:
            kwargs.setdefault(DjangoFieldAttributeEnum.BLANK.value, True)
            kwargs.setdefault(DjangoFieldAttributeEnum.NULL.value, True)

        return kwargs

    @classmethod
    def handleDjangoKwargs(cls, **kwargs) -> dict:
        """
        Prepare kwargs for Django's field __init__:
          1. Apply the required → blank/null translation.
          2. Strip all SnapAdmin-specific keys so Django doesn't reject them.

        Call this as the last step before ``super().__init__(**...)``
        """

        for snap_key in [e.value for e in SnapFieldAttributeEnum]:
            if snap_key == SnapFieldAttributeEnum.EDITABLE.value:
                continue
            kwargs.pop(snap_key, None)

        return kwargs


# ===========================================================================
# Non-database field base
# ===========================================================================

class SnapNotDatabaseField(SnapField):
    """
    Base for fields that exist only in the admin UI and have no DB column.
    Currently used by SnapFunctionField and its subclasses.

    TODO: add ordering support via a DB expression proxy.
    """
    pass


# ===========================================================================
# Concrete field implementations
# ===========================================================================
# Each class simply:
#   1. Calls _initializeSnapLogic() to consume SnapAdmin kwargs
#   2. Applies any field-specific overrides
#   3. Passes the sanitised kwargs to Django's field __init__
# ===========================================================================

class SnapCharField(models.CharField, SnapField):
    def __init__(self, **kwargs):
        kwargs.setdefault(DjangoFieldAttributeEnum.NULL.value, False)
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapTextField(models.TextField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapEmailField(models.EmailField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapSlugField(models.SlugField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        kwargs.setdefault(DjangoFieldAttributeEnum.MAX_LENGTH.value, 50)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapURLField(models.URLField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapUUIDField(models.UUIDField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapIntegerField(models.IntegerField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapPositiveIntegerField(models.PositiveIntegerField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapFloatField(models.FloatField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapDecimalField(models.DecimalField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapBigIntegerField(models.BigIntegerField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapDateField(models.DateField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapDateTimeField(models.DateTimeField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapTimeField(models.TimeField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapDurationField(models.DurationField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapFileField(models.FileField, SnapField):
    """
    FileField extended with SnapAdmin file validation.

    Extra kwargs
    ------------
    allowed_extensions : list[str], optional
        Whitelist of file extensions (e.g. ['pdf', 'docx']).
    allowed_encodings  : list[str], optional
        Whitelist of detected file encodings.
    max_size_bytes     : int, optional
        Maximum allowed upload size in bytes.
    """

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)

        # Extract SnapAdmin-specific file constraints and build the validator
        allowed_extensions = kwargs.pop(SnapFieldAttributeEnum.ALLOWED_EXTENSIONS, None)
        allowed_encodings = kwargs.pop(SnapFieldAttributeEnum.ALLOWED_ENCODINGS, None)
        max_size_bytes = kwargs.pop(SnapFieldAttributeEnum.MAX_SIZE_BYTES, None)

        file_validator = snap_validators.SnapFileValidator(
            allowed_extensions=allowed_extensions,
            allowed_encodings=allowed_encodings,
            max_size_bytes=max_size_bytes,
        )
        kwargs.setdefault(DjangoFieldAttributeEnum.VALIDATORS.value, []).append(file_validator)

        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapImageField(models.ImageField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapBooleanField(models.BooleanField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapJSONField(models.JSONField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapGenericIPAddressField(models.GenericIPAddressField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapForeignKey(models.ForeignKey, SnapField):
    """
    ForeignKey field with SnapAdmin metadata support.

    The admin form widget width is constrained via the bundled admin.css
    using ``.select2-container { max-width: 100%; }`` so it never overflows
    the form panel on narrow screens.
    """
    def __init__(self, to, on_delete=models.CASCADE, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(to=to, on_delete=on_delete, **self.handleDjangoKwargs(**kwargs))


class SnapOneToOneField(models.OneToOneField, SnapField):
    def __init__(self, to, on_delete=models.CASCADE, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(to=to, on_delete=on_delete, **self.handleDjangoKwargs(**kwargs))


class SnapManyToManyField(models.ManyToManyField, SnapField):
    def __init__(self, to, on_delete=models.CASCADE, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        # M2M relations never have a null column — remove it before passing to Django
        kwargs.pop(DjangoFieldAttributeEnum.NULL, None)
        super().__init__(to=to, on_delete=on_delete, **self.handleDjangoKwargs(**kwargs))


# ===========================================================================
# Computed / virtual fields (no DB column)
# ===========================================================================

class SnapFunctionField(SnapNotDatabaseField):
    """
    A virtual column whose value is derived from a callable at display time.

    Parameters
    ----------
    func         : callable(obj) → any
        Called with the model instance; its return value is displayed.
    verbose_name : str, optional
        Column header shown in the admin changelist.
    show_in_list : bool
        Whether the column appears in the changelist (default: True).
    show_in_form : bool
        Whether the value appears in the change form (default: True).
    safe_html    : bool
        When True, the return value is marked as safe HTML (default: False).
    """

    def __init__(self, func, verbose_name=None, show_in_list=True,
                 show_in_form=True, safe_html=False, *args, **kwargs):
        if not callable(func):
            raise ValueError("SnapFunctionField requires a callable 'func'.")

        self.func = func
        self.verbose_name = verbose_name
        self.show_in_list = show_in_list
        self.show_in_form = show_in_form
        self.safe_html = safe_html

        super().__init__()

    def get_display_value(self, obj):
        """Evaluate ``func`` against the model instance and optionally mark as safe HTML."""
        value = self.func(obj)
        return mark_safe(value) if self.safe_html else value


# ===========================================================================
# Status badge helpers
# ===========================================================================

class SnapStatusBadgeFieldChoice:
    """
    Defines the visual appearance of one badge variant.

    Parameters
    ----------
    status_string        : str
        The raw field value this choice applies to.
    text_html_color      : str
        CSS color for the badge text (default: dark grey).
    background_html_color: str
        CSS background-color for the badge (default: light grey).
    border_html_color    : str
        CSS border color for the badge (default: mid grey).
    """

    def __init__(
            self,
            status_string: str,
            text_html_color: str = "#333333",
            background_html_color: str = "#F5F5F5",
            border_html_color: str = "#A9A9A9",
    ):
        self.status_string = status_string
        self.text_html_color = text_html_color
        self.background_html_color = background_html_color
        self.border_html_color = border_html_color

    def get_html_choice(self, field_display: str, style_overrides: dict) -> str:
        """
        Render the badge as a safe HTML ``<a>`` element.

        Parameters
        ----------
        field_display   : str
            Human-readable label shown inside the badge.
        style_overrides : dict
            Additional CSS properties that are merged on top of the defaults.
        """
        styles = {
            "color": self.text_html_color,
            "padding": "3px",
            "padding-left": "10px",
            "padding-right": "10px",
            "white-space": "nowrap",
            "border-radius": "25px",
            "background-color": self.background_html_color,
            "border": f"2px solid {self.border_html_color}",
        }
        styles.update(style_overrides)
        style_string = "; ".join(f"{k}: {v}" for k, v in styles.items())

        return format_html('<a style="{}">{}</a>', style_string, field_display)


class SnapStatusBadgeField(SnapFunctionField):
    """
    Virtual admin column that renders a field's value as a coloured pill badge.

    Parameters
    ----------
    field_name      : str
        Name of the model field whose value determines which badge to show.
    choices         : list[SnapStatusBadgeFieldChoice]
        Ordered list of badge configurations matched against the field value.
    verbose_name    : str, optional
        Column header.
    style_arguments : dict, optional
        Extra CSS properties applied to every badge rendered by this field.
    """

    def __init__(
            self,
            *args,
            field_name: str,
            choices: typing.List[SnapStatusBadgeFieldChoice],
            verbose_name: str = None,
            style_arguments: dict = None,
            **kwargs,
    ):
        self.field_name = field_name
        self.choices = choices
        self.style_arguments = style_arguments or {}

        super().__init__(
            func=self._render_badge,
            verbose_name=verbose_name,
            safe_html=True,
            *args,
            **kwargs,
        )

    def _render_badge(self, obj) -> str:
        """
        Look up the field value on ``obj``, find a matching choice, and
        return the rendered HTML badge (or the plain display value as fallback).
        """
        field_value = getattr(obj, self.field_name, "")
        display_method = getattr(obj, f"get_{self.field_name}_display", None)
        field_display = display_method() if display_method else field_value

        for choice in self.choices:
            if choice.status_string == field_value:
                return choice.get_html_choice(field_display, self.style_arguments)

        # No matching choice — fall back to the raw display value
        return field_display
