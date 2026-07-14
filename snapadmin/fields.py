"""
snapadmin/fields.py

Custom field layer on top of Django's standard model fields.
...
"""

import typing
from enum import Enum

from django import forms
from django.db import models
from django.core import checks, validators
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
    WYSIWYG = "wysiwyg" # Added for Unfold/CKEditor integration
    SAFE_HTML = "safe_html" # Opt out of wysiwyg HTML sanitization (trusted content)
    TAB = "tab" # Added for Unfold fieldset tabs
    ROW = "row" # Group fields in one row


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

def _strip_auto_validator(deconstructed, auto_instance):
    """Drop the auto-injected validator from a field's ``deconstruct()`` output.

    Fields like :class:`SnapColorField` add their validator in ``__init__``. If
    ``deconstruct()`` also reports it, every reconstruction re-adds another copy,
    so the validator list grows on each migration and ``makemigrations`` never
    converges. Removing it here lets ``__init__`` re-add exactly one.

    Filtering is by *identity* (``is``), not class: it removes only the exact
    instance ``__init__`` built for this field, so a caller-supplied validator of
    the same class (passed via ``validators=[...]``) is left untouched.
    """
    name, path, args, kwargs = deconstructed
    validators = kwargs.get(DjangoFieldAttributeEnum.VALIDATORS.value)
    if validators:
        kept = [v for v in validators if v is not auto_instance]
        if kept:
            kwargs[DjangoFieldAttributeEnum.VALIDATORS.value] = kept
        else:
            kwargs.pop(DjangoFieldAttributeEnum.VALIDATORS.value, None)
    return name, path, args, kwargs


class SnapField:
    """
    Mixin that adds SnapAdmin metadata to any Django model field.
    """

    def _initializeSnapLogic(self, **kwargs) -> dict:
        kwargs = self.__handleRequiredFlag(**kwargs)
        kwargs = self.__applySnapDefaults(**kwargs)
        kwargs = self.__reinitializeAutocomplete(**kwargs)
        kwargs = self.__reinitializeAutoNow(**kwargs)

        # Store as instance attributes
        self.show_in_list = kwargs[SnapFieldAttributeEnum.SHOW_IN_LIST.value]
        self.show_in_form = kwargs[SnapFieldAttributeEnum.SHOW_IN_FORM.value]
        self.searchable = kwargs[SnapFieldAttributeEnum.SEARCHABLE.value]
        self.filterable = kwargs[SnapFieldAttributeEnum.FILTERABLE.value]
        self.editable = kwargs[SnapFieldAttributeEnum.EDITABLE.value]
        self.required = kwargs[SnapFieldAttributeEnum.REQUIRED.value]
        self.updatable = kwargs[SnapFieldAttributeEnum.UPDATABLE.value]
        self.autocomplete = kwargs[SnapFieldAttributeEnum.AUTOCOMPLETE.value]
        self.wysiwyg = kwargs.get(SnapFieldAttributeEnum.WYSIWYG.value, False)
        self.safe_html = kwargs.get(SnapFieldAttributeEnum.SAFE_HTML.value, False)
        self.tab = kwargs.get(SnapFieldAttributeEnum.TAB.value, None)
        self.row = kwargs.get(SnapFieldAttributeEnum.ROW.value, None)

        return kwargs

    def __applySnapDefaults(self, **kwargs) -> dict:
        snap_defaults = {
            SnapFieldAttributeEnum.SHOW_IN_LIST: True,
            SnapFieldAttributeEnum.SHOW_IN_FORM: False,
            SnapFieldAttributeEnum.SEARCHABLE: False,
            SnapFieldAttributeEnum.FILTERABLE: False,
            SnapFieldAttributeEnum.EDITABLE: True,
            SnapFieldAttributeEnum.REQUIRED: False,
            SnapFieldAttributeEnum.UPDATABLE: True,
            SnapFieldAttributeEnum.AUTOCOMPLETE: False,
            SnapFieldAttributeEnum.WYSIWYG: False,
            SnapFieldAttributeEnum.SAFE_HTML: False,
            SnapFieldAttributeEnum.TAB: None,
            SnapFieldAttributeEnum.ROW: None,
        }

        for enum_attr, default_value in snap_defaults.items():
            kwargs.setdefault(enum_attr.value, default_value)

        return kwargs

    @classmethod
    def __reinitializeAutocomplete(cls, **kwargs) -> dict:
        autocomplete_not_set = kwargs.get(SnapFieldAttributeEnum.AUTOCOMPLETE.value) is False
        has_choices = bool(kwargs.get(DjangoFieldAttributeEnum.CHOICES.value))

        if autocomplete_not_set and has_choices:
            kwargs[SnapFieldAttributeEnum.AUTOCOMPLETE.value] = True

        return kwargs

    @classmethod
    def __reinitializeAutoNow(cls, **kwargs) -> dict:
        if kwargs.get(DjangoFieldAttributeEnum.AUTO_NOW) or kwargs.get(DjangoFieldAttributeEnum.AUTO_NOW_ADD):
            kwargs[SnapFieldAttributeEnum.EDITABLE.value] = False
            kwargs[SnapFieldAttributeEnum.UPDATABLE.value] = False
        return kwargs

    @classmethod
    def __handleRequiredFlag(cls, **kwargs) -> dict:
        if kwargs.get(SnapFieldAttributeEnum.REQUIRED.value) is True:
            kwargs.setdefault(DjangoFieldAttributeEnum.BLANK.value, False)
            kwargs.setdefault(DjangoFieldAttributeEnum.NULL.value, False)
        else:
            kwargs.setdefault(DjangoFieldAttributeEnum.BLANK.value, True)
            kwargs.setdefault(DjangoFieldAttributeEnum.NULL.value, True)
        return kwargs

    @classmethod
    def handleDjangoKwargs(cls, **kwargs) -> dict:
        for snap_key in [e.value for e in SnapFieldAttributeEnum]:
            if snap_key == SnapFieldAttributeEnum.EDITABLE.value:
                continue
            kwargs.pop(snap_key, None)
        return kwargs

    def __init_subclass__(cls, **kwargs):
        # Snap fields derive `null`/`blank` from the `required` flag inside
        # __init__. But `required` is not a Django kwarg, so it never survives
        # deconstruct() — and Django's migration autodetector clones every
        # field via deconstruct()→reconstruct(). Without help, a reconstructed
        # `required=True` field loses that flag and reverts to the optional
        # default (null=True), producing wrong migrations for mandatory fields.
        # Force the resolved null/blank into the deconstructed kwargs so a
        # field always round-trips to the same column definition.
        super().__init_subclass__(**kwargs)
        base_deconstruct = getattr(cls, "deconstruct", None)
        # Non-database Snap fields (SnapFunctionField etc.) have no deconstruct
        # and never reach a migration — nothing to stabilise.
        if base_deconstruct is None:
            return

        def deconstruct(self):
            name, path, args, kw = base_deconstruct(self)
            if hasattr(self, "null"):
                kw["null"] = self.null
                kw["blank"] = self.blank
            return name, path, args, kw

        cls.deconstruct = deconstruct

        # Any class past the guard above is a real Django Field, so it also has
        # Field.check. Django resolves `check` to Field.check (which precedes this
        # mixin in every Snap field's MRO and does not call super()), so defining
        # `check` on the mixin directly would be silently shadowed. Wrap the
        # resolved `check` instead, mirroring the deconstruct wrapper above, so
        # the contradiction check composes with Django's own field checks.
        base_check = cls.check

        def check(self, **kwargs):
            errors = base_check(self, **kwargs)
            if getattr(self, "required", False) and getattr(self, "null", False):
                errors.append(checks.Error(
                    f"{self.name!r} sets required=True but is also nullable (null=True) — contradictory.",
                    hint="required=True already forces null=False/blank=False unless you explicitly "
                         "override them; remove the explicit null=True, or drop required=True if the "
                         "field should stay optional.",
                    obj=self,
                    id="snapadmin.E003",
                ))
            return errors

        cls.check = check


class SnapNotDatabaseField(SnapField):
    pass

class SnapCharField(models.CharField, SnapField):
    # No special null handling: like every other Snap field, `required=False`
    # (the default) yields null=True / blank=True, and `required=True` yields
    # null=False / blank=False. This keeps CharField data parity with the rest
    # of the field types (and with mirrored ES documents).
    def __init__(self, **kwargs):
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
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        allowed_extensions = kwargs.pop(SnapFieldAttributeEnum.ALLOWED_EXTENSIONS, None)
        allowed_encodings = kwargs.pop(SnapFieldAttributeEnum.ALLOWED_ENCODINGS, None)
        max_size_bytes = kwargs.pop(SnapFieldAttributeEnum.MAX_SIZE_BYTES, None)
        file_validator = snap_validators.SnapFileValidator(
            allowed_extensions=allowed_extensions,
            allowed_encodings=allowed_encodings,
            max_size_bytes=max_size_bytes,
        )
        # Keep the resolved config and the exact validator instance so
        # deconstruct() can re-serialise the limits (Django strips non-Django
        # kwargs) and strip precisely the auto validator by identity.
        self._snap_allowed_extensions = allowed_extensions
        self._snap_allowed_encodings = allowed_encodings
        self._snap_max_size_bytes = max_size_bytes
        self._snap_auto_validator = file_validator
        kwargs.setdefault(DjangoFieldAttributeEnum.VALIDATORS.value, []).append(file_validator)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    def deconstruct(self):
        # __init__ rebuilds the SnapFileValidator from the config kwargs below,
        # so strip the auto instance and re-emit the config as plain kwargs;
        # otherwise a reconstructed field silently loses its extension/size/
        # encoding limits and the validator list never converges.
        name, path, args, kwargs = _strip_auto_validator(super().deconstruct(), self._snap_auto_validator)
        if self._snap_allowed_extensions is not None:
            kwargs["allowed_extensions"] = self._snap_allowed_extensions
        if self._snap_allowed_encodings is not None:
            kwargs["allowed_encodings"] = self._snap_allowed_encodings
        if self._snap_max_size_bytes is not None:
            kwargs["max_size_bytes"] = self._snap_max_size_bytes
        return name, path, args, kwargs

class SnapImageField(models.ImageField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        allowed_extensions = kwargs.pop(SnapFieldAttributeEnum.ALLOWED_EXTENSIONS, None)
        max_size_bytes = kwargs.pop(SnapFieldAttributeEnum.MAX_SIZE_BYTES, None)
        # allowed_encodings is a text-file concept and does not apply to images.
        image_validator = snap_validators.SnapFileValidator(
            allowed_extensions=allowed_extensions,
            max_size_bytes=max_size_bytes,
        )
        self._snap_allowed_extensions = allowed_extensions
        self._snap_max_size_bytes = max_size_bytes
        self._snap_auto_validator = image_validator
        kwargs.setdefault(DjangoFieldAttributeEnum.VALIDATORS.value, []).append(image_validator)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    def deconstruct(self):
        name, path, args, kwargs = _strip_auto_validator(super().deconstruct(), self._snap_auto_validator)
        if self._snap_allowed_extensions is not None:
            kwargs["allowed_extensions"] = self._snap_allowed_extensions
        if self._snap_max_size_bytes is not None:
            kwargs["max_size_bytes"] = self._snap_max_size_bytes
        return name, path, args, kwargs

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
    def __init__(self, to, on_delete=models.CASCADE, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(to=to, on_delete=on_delete, **self.handleDjangoKwargs(**kwargs))

class SnapOneToOneField(models.OneToOneField, SnapField):
    def __init__(self, to, on_delete=models.CASCADE, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(to=to, on_delete=on_delete, **self.handleDjangoKwargs(**kwargs))

class SnapManyToManyField(models.ManyToManyField, SnapField):
    def __init__(self, to, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        kwargs.pop(DjangoFieldAttributeEnum.NULL, None)
        super().__init__(to=to, **self.handleDjangoKwargs(**kwargs))

class SnapSmallIntegerField(models.SmallIntegerField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapPositiveSmallIntegerField(models.PositiveSmallIntegerField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapPositiveBigIntegerField(models.PositiveBigIntegerField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapRichTextField(models.TextField, SnapField):
    """TextField with wysiwyg=True preset - no extra argument needed."""

    def __init__(self, **kwargs):
        kwargs.setdefault(SnapFieldAttributeEnum.WYSIWYG.value, True)
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapPhoneField(models.CharField, SnapField):
    """CharField pre-wired with phone number validation and a sensible max_length."""

    def __init__(self, **kwargs):
        from snapadmin.validators import SnapPhoneValidator
        kwargs.setdefault(DjangoFieldAttributeEnum.MAX_LENGTH.value, 20)
        kwargs = self._initializeSnapLogic(**kwargs)
        cleaned = self.handleDjangoKwargs(**kwargs)
        cleaned.setdefault(DjangoFieldAttributeEnum.VALIDATORS.value, [])
        self._snap_auto_validator = SnapPhoneValidator()
        cleaned[DjangoFieldAttributeEnum.VALIDATORS.value].append(self._snap_auto_validator)
        super().__init__(**cleaned)

    def deconstruct(self):
        return _strip_auto_validator(super().deconstruct(), self._snap_auto_validator)

class SnapColorField(models.CharField, SnapField):
    """CharField pre-wired with hex color validation (#RRGGBB / #RGB)."""

    def __init__(self, **kwargs):
        from snapadmin.validators import SnapColorValidator
        kwargs.setdefault(DjangoFieldAttributeEnum.MAX_LENGTH.value, 7)
        kwargs = self._initializeSnapLogic(**kwargs)
        cleaned = self.handleDjangoKwargs(**kwargs)
        cleaned.setdefault(DjangoFieldAttributeEnum.VALIDATORS.value, [])
        self._snap_auto_validator = SnapColorValidator()
        cleaned[DjangoFieldAttributeEnum.VALIDATORS.value].append(self._snap_auto_validator)
        super().__init__(**cleaned)

    def deconstruct(self):
        return _strip_auto_validator(super().deconstruct(), self._snap_auto_validator)

class SnapFunctionField(SnapNotDatabaseField):
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
        value = self.func(obj)
        return mark_safe(value) if self.safe_html else value

class SnapStatusBadgeFieldChoice:
    def __init__(self, status_string: str, text_html_color: str = "#333333",
                 background_html_color: str = "#F5F5F5", border_html_color: str = "#A9A9A9"):
        self.status_string = status_string
        self.text_html_color = text_html_color
        self.background_html_color = background_html_color
        self.border_html_color = border_html_color

    def get_html_choice(self, field_display: str, style_overrides: dict) -> str:
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
    def __init__(self, *args, field_name: str, choices: typing.List[SnapStatusBadgeFieldChoice],
                 verbose_name: str = None, style_arguments: dict = None, **kwargs):
        self.field_name = field_name
        self.choices = choices
        self.style_arguments = style_arguments or {}
        super().__init__(func=self._render_badge, verbose_name=verbose_name, safe_html=True, *args, **kwargs)

    def _render_badge(self, obj) -> str:
        field_value = getattr(obj, self.field_name, "")
        display_method = getattr(obj, f"get_{self.field_name}_display", None)
        field_display = display_method() if display_method else field_value
        for choice in self.choices:
            if choice.status_string == field_value:
                return choice.get_html_choice(field_display, self.style_arguments)
        return field_display
