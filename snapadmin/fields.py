"""
snapadmin/fields.py

Custom field layer on top of Django's standard model fields.
...
"""

import types
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
    AUTO_SANITIZE = "auto_sanitize" # Sanitize wysiwyg HTML on write (default: on)
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
    """Mixin that adds SnapAdmin metadata to any Django model field.

    Every ``Snap*Field`` is its Django counterpart plus this mixin, so it accepts
    all the usual Django kwargs *and* the ones below, which describe how the field
    should behave in the admin, the API and search::

        from snapadmin import fields as snap, models as snap_models

        class Product(snap_models.SnapModel):
            name  = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)
            price = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)

    ``show_in_list``
        Include the field as a column on the admin changelist. Default ``True``.
    ``show_in_form``
        Include the field on the add/change form. Default ``False``.
    ``searchable``
        Add to the admin search box, the REST ``?search=`` filter and — on a model
        mirrored to Elasticsearch — the search mapping. Default ``False``.
    ``filterable``
        Add a sidebar filter in the admin and a ``?field=`` query filter in the API.
        Default ``False``.
    ``editable``
        Allow changes through the form and the API. Default ``True``.
    ``required``
        ``True`` yields ``null=False, blank=False``; the default ``False`` yields
        ``null=True, blank=True``. Set it instead of the two Django kwargs so the
        database and the mirrored ES document stay in agreement.
    ``updatable``
        Allow the value to change after creation. ``False`` makes it write-once.
    ``autocomplete``
        Render a relation as an autocomplete widget instead of a full dropdown.
    ``tab`` / ``row``
        Lay the field out in a named form tab, or group it onto one row.
    ``wysiwyg`` / ``safe_html``
        Rich-text editing (needs the ``[wysiwyg]`` extra); ``safe_html=True`` opts
        the value out of HTML sanitisation, for trusted content only.
    ``allowed_extensions`` / ``allowed_encodings`` / ``max_size_bytes``
        Upload validation on file and image fields.

    **None of these add a database migration.** They are stripped in
    :meth:`handleDjangoKwargs` before Django sees the field and are absent from
    ``deconstruct()``, so adding or changing one leaves ``makemigrations`` with
    nothing to detect.
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
        self.auto_sanitize = kwargs.get(SnapFieldAttributeEnum.AUTO_SANITIZE.value, True)
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


#: The three file-upload kwargs: post-construction they cannot be set as a
#: plain attribute the way the others are — they build a
#: :class:`~snapadmin.validators.SnapFileValidator` instead (see
#: :func:`_attach_file_validator`), so :func:`snap_field` handles them apart
#: from the generic ``setattr`` loop.
_SNAP_FIELD_FILE_VALIDATOR_KWARGS: frozenset[str] = frozenset({
    SnapFieldAttributeEnum.ALLOWED_EXTENSIONS.value,
    SnapFieldAttributeEnum.ALLOWED_ENCODINGS.value,
    SnapFieldAttributeEnum.MAX_SIZE_BYTES.value,
})

#: Kwargs :func:`snap_field` may set — every name :meth:`SnapField._initializeSnapLogic`
#: (or :class:`SnapFileField`/:class:`SnapImageField`'s ``__init__``) stores on a
#: ``Snap*Field`` instance, so a reader (the searchable filter, the admin's
#: list/form/tab/row layout, the wysiwyg widget, a file upload validator, …)
#: cannot tell whether they came from a ``Snap*Field`` subclass or from this
#: wrapper. ``required`` and the three file-upload kwargs need extra handling
#: beyond a plain ``setattr`` — see :func:`_apply_required_flag` and
#: :func:`_attach_file_validator` — but are fully supported, not refused.
_SNAP_FIELD_WRAPPER_KWARGS: frozenset[str] = frozenset({
    SnapFieldAttributeEnum.SHOW_IN_LIST.value,
    SnapFieldAttributeEnum.SHOW_IN_FORM.value,
    SnapFieldAttributeEnum.SEARCHABLE.value,
    SnapFieldAttributeEnum.FILTERABLE.value,
    SnapFieldAttributeEnum.EDITABLE.value,
    SnapFieldAttributeEnum.REQUIRED.value,
    SnapFieldAttributeEnum.UPDATABLE.value,
    SnapFieldAttributeEnum.AUTOCOMPLETE.value,
    SnapFieldAttributeEnum.WYSIWYG.value,
    SnapFieldAttributeEnum.SAFE_HTML.value,
    SnapFieldAttributeEnum.AUTO_SANITIZE.value,
    SnapFieldAttributeEnum.TAB.value,
    SnapFieldAttributeEnum.ROW.value,
} | _SNAP_FIELD_FILE_VALIDATOR_KWARGS)

#: The parity drift guard (`tests/test_fields.py::TestSnapFieldWrapperDriftGuard`)
#: asserts ``set(SnapFieldAttributeEnum) - _SNAP_FIELD_WRAPPER_KWARGS`` equals
#: exactly this set. Empty today — #PAR1c closed the last four gaps, so every
#: ``SnapFieldAttributeEnum`` name is reachable from :func:`snap_field`. A future
#: ``SnapFieldAttributeEnum`` member must be added to ``_SNAP_FIELD_WRAPPER_KWARGS``
#: (wired into :func:`snap_field`) or listed here with a reason before the drift
#: guard passes again — never silently neither.
_SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS: frozenset[str] = frozenset()


def _apply_required_flag(field: models.Field, required: bool) -> None:
    """Mirror ``Snap*Field.__init__``'s ``required`` → ``null``/``blank`` derivation,
    post-construction.

    Unlike the metadata kwargs, ``required`` is schema-affecting by design — that
    is its entire purpose. ``required=True`` unconditionally tightens the field
    to ``null=False, blank=False``, exactly what a hand-built
    ``Snap*Field(required=True)`` produces; mutating them here, before the field
    is attached to a model class, needs no special ``deconstruct()`` handling,
    because Django's own ``deconstruct()`` already reports live ``null``/``blank``
    state (unlike ``required`` itself, which is derived and forgotten).

    ``required=False`` — the same value ``SnapField``'s own default already
    means — is deliberately a **no-op on ``null``/``blank``**, not a reset to
    ``True, True``: a caller who explicitly built the field with
    ``null=False, blank=False`` (Django's own default) did not ask
    ``snap_field()`` to reopen it. This is the one place the wrapper cannot
    replicate ``Snap*Field.__init__``'s ``setdefault``-based precedence (which
    only fills in ``null``/``blank`` when the caller did not already pass them)
    — by the time ``snap_field()`` runs, the field's ``null``/``blank`` are
    already concrete booleans with no record of whether they were explicit.
    ``required`` can only ever *tighten* a wrapped field, never loosen one.
    """
    field.required = required
    if required:
        field.null = False
        field.blank = False


def _attach_file_validator(
    field: models.Field,
    *,
    allowed_extensions=None,
    allowed_encodings=None,
    max_size_bytes=None,
) -> None:
    """Attach a :class:`~snapadmin.validators.SnapFileValidator`, post-construction,
    mirroring what :class:`SnapFileField`/:class:`SnapImageField` build in
    ``__init__`` — without the two ways that naive post-init validator
    attachment breaks:

    * ``Field.validators`` is a ``cached_property`` that Django already
      evaluates (and caches) during ``Field.__init__`` — appending to
      ``field._validators`` afterward, alone, is silently never consulted by
      ``full_clean()``. Popping the cached entry first forces it to recompute
      on next access, so the new validator is actually live.
    * The appended validator instance would otherwise show up in
      ``deconstruct()``'s ``validators=[...]`` kwarg, dirtying
      ``makemigrations``. Binding a ``deconstruct`` override that strips it by
      identity — the same :func:`_strip_auto_validator` helper
      ``SnapFileField``/``SnapImageField``/``SnapPhoneField``/``SnapColorField``
      already use — keeps the wrapper's "adds no database migration" promise.
    """
    validator = snap_validators.SnapFileValidator(
        allowed_extensions=allowed_extensions,
        allowed_encodings=allowed_encodings,
        max_size_bytes=max_size_bytes,
    )
    field.__dict__.pop(DjangoFieldAttributeEnum.VALIDATORS.value, None)
    field._validators = list(field._validators) + [validator]

    original_deconstruct = field.deconstruct

    def deconstruct(self):
        return _strip_auto_validator(original_deconstruct(), validator)

    field.deconstruct = types.MethodType(deconstruct, field)


def snap_field(field: models.Field, **kwargs: bool | str | None) -> models.Field:
    """Attach SnapAdmin metadata to a plain Django field instance, in place.

    A ``Snap*Field`` subclass is sugar over two things: an ordinary Django
    field, plus a handful of attributes (``searchable``, ``filterable``,
    ``show_in_list``, …) that every SnapAdmin reader looks up with
    ``getattr(field, "...", default)``. ``snap_field()`` sets exactly those
    attributes on a field instance you already have, so it works on **any**
    ``django.db.models.Field`` — including one SnapAdmin has never seen, from
    a third-party package (``django-money``, ``model-utils``,
    ``phonenumber_field``, …) or a brownfield model that cannot be rewritten
    onto the ``Snap*Field`` classes::

        from django.db import models
        from snapadmin.fields import snap_field

        class Product(models.Model):
            name = snap_field(models.CharField(max_length=255), searchable=True, filterable=True)

    Every reader keeps working unchanged, because there is nothing new to
    read: the attributes it sets are the same ones a ``Snap*Field`` stores on
    itself, not a second, parallel place to look. Returns ``field`` so the
    call composes inline with the field declaration, as above.

    Every :class:`SnapFieldAttributeEnum` name is accepted — full parity with
    what a ``Snap*Field`` constructor takes, including ``required`` (see
    :func:`_apply_required_flag`) and the file-upload trio
    ``allowed_extensions`` / ``allowed_encodings`` / ``max_size_bytes`` (see
    :func:`_attach_file_validator`, and note those three only make sense on a
    ``FileField``/``ImageField``). An unrecognised name — a typo — raises
    ``ValueError`` naming it, rather than silently doing nothing.

    Adds no database migration for every kwarg **except** ``required``: the
    metadata kwargs are mutated *after* ``Field.__init__`` already recorded its
    constructor arguments, so none of them can appear in ``deconstruct()``.
    ``required`` is the deliberate exception — it is schema-affecting by
    design, same as passing ``null=``/``blank=`` directly to the Django field,
    and **can** produce a migration the same way changing a ``Snap*Field``'s
    ``required`` would.

    ``wysiwyg=True`` also binds the same sanitize-on-write guarantee
    ``SnapRichTextField``/``SnapTextField(wysiwyg=True)`` get from
    :class:`SanitizedHtmlOnSaveMixin` — see :func:`_bind_wysiwyg_pre_save`.
    """
    for key in kwargs:
        if key not in _SNAP_FIELD_WRAPPER_KWARGS:
            raise ValueError(
                f"snap_field() got an unexpected keyword argument {key!r}; valid kwargs are: "
                f"{', '.join(sorted(_SNAP_FIELD_WRAPPER_KWARGS))}."
            )

    file_kwargs = {k: v for k, v in kwargs.items() if k in _SNAP_FIELD_FILE_VALIDATOR_KWARGS}
    if file_kwargs and not isinstance(field, models.FileField):
        raise ValueError(
            "snap_field(): allowed_extensions/allowed_encodings/max_size_bytes only apply to "
            f"a FileField or ImageField, got {type(field).__name__}."
        )
    if file_kwargs:
        _attach_file_validator(field, **file_kwargs)

    if SnapFieldAttributeEnum.REQUIRED.value in kwargs:
        _apply_required_flag(field, kwargs[SnapFieldAttributeEnum.REQUIRED.value])

    skip = _SNAP_FIELD_FILE_VALIDATOR_KWARGS | {SnapFieldAttributeEnum.REQUIRED.value}
    for key, value in kwargs.items():
        if key in skip:
            continue
        setattr(field, key, value)

    if getattr(field, "wysiwyg", False):
        _bind_wysiwyg_pre_save(field)
    return field


class SnapNotDatabaseField(SnapField):
    pass

class SnapCharField(models.CharField, SnapField):
    """Django ``CharField`` with SnapAdmin metadata. See :class:`SnapField`."""

    # No special null handling: like every other Snap field, `required=False`
    # (the default) yields null=True / blank=True, and `required=True` yields
    # null=False / blank=False. This keeps CharField data parity with the rest
    # of the field types (and with mirrored ES documents).
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

def _sanitize_wysiwyg_pre_save_value(field: models.Field, model_instance: models.Model, value):
    """Apply the wysiwyg sanitize-on-write rule to one ``pre_save`` value.

    Shared by :class:`SanitizedHtmlOnSaveMixin` (the ``Snap*Field`` route) and the
    :func:`snap_field` wrapper's bound ``pre_save`` (see
    :func:`_bind_wysiwyg_pre_save`), so both routes run the exact same sanitizer
    on the exact same rule — never two implementations that could drift apart.

    Deliberately narrow:

    * only fields with ``wysiwyg=True`` (plain text is not HTML, and sanitizing it would mangle
      characters like ``<`` that mean nothing but themselves);
    * ``safe_html=True`` (content the developer vouches for) and ``auto_sanitize=False`` opt out;
    * ``QuerySet.update()`` is **not** covered — Django never calls ``pre_save()`` for it, so a
      caller writing rich text that way sanitizes it themselves. Documented, and pinned by a test.
    """
    if not getattr(field, "wysiwyg", False) or not getattr(field, "auto_sanitize", True):
        return value
    if getattr(field, "safe_html", False) or not isinstance(value, str) or not value:
        return value
    from snapadmin.sanitize import sanitize_html

    cleaned = sanitize_html(value)
    if cleaned != value:
        # Keep the instance in hand consistent with the row just written; otherwise the
        # object the caller holds still carries markup the database no longer has.
        setattr(model_instance, field.attname, cleaned)
    return cleaned


def _bind_wysiwyg_pre_save(field: models.Field) -> None:
    """Give a plain, ``snap_field()``-wrapped field the same sanitize-on-write
    guarantee :class:`SanitizedHtmlOnSaveMixin` gives a ``Snap*Field`` subclass.

    A wrapped field's class never enters ``SanitizedHtmlOnSaveMixin``'s MRO — it
    is a plain ``django.db.models.Field`` instance, not a ``Snap*Field`` — so the
    mixin's ``pre_save`` never runs for it. This binds an equivalent ``pre_save``
    onto the *instance* instead, closing over the field's own original bound
    method (whatever ``pre_save`` its concrete Django field class already
    provides) so the wrapper still works on any field type, and delegating the
    actual sanitize rule to :func:`_sanitize_wysiwyg_pre_save_value` — the exact
    same function the class route uses, not a second sanitizer.

    Idempotent: calling this more than once on the same field instance (e.g. two
    :func:`snap_field` calls chained on it) binds the wrapper only once.
    """
    if getattr(field, "_snap_wysiwyg_pre_save_bound", False):
        return

    original_pre_save = field.pre_save

    def pre_save(self, model_instance, add):
        value = original_pre_save(model_instance, add)
        return _sanitize_wysiwyg_pre_save_value(self, model_instance, value)

    field.pre_save = types.MethodType(pre_save, field)
    field._snap_wysiwyg_pre_save_bound = True


class SanitizedHtmlOnSaveMixin:
    """Sanitize a wysiwyg field's HTML on the way into the database.

    Rendering was already sanitized, but only in the admin changelist — the column itself held
    whatever was written to it, so every other reader (a project template using ``|safe``, a
    frontend consuming the REST API, an export) still received the raw payload. Cleaning in
    ``pre_save`` moves the guarantee into storage and covers **every ORM write path** — admin
    form, DRF serializer, ``Model.save()``, ``bulk_create`` — instead of one rendering path. See
    :func:`_sanitize_wysiwyg_pre_save_value` for exactly what is and is not covered.

    This mixin comes **before** the Django field class in the bases: ``SnapField`` sits after it
    in the MRO, so a ``pre_save`` defined there would lose to ``models.Field.pre_save``.
    """

    def pre_save(self, model_instance, add):
        value = super().pre_save(model_instance, add)
        return _sanitize_wysiwyg_pre_save_value(self, model_instance, value)


class SnapTextField(SanitizedHtmlOnSaveMixin, models.TextField, SnapField):
    """Django ``TextField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapEmailField(models.EmailField, SnapField):
    """Django ``EmailField`` with SnapAdmin metadata. See :class:`SnapField`."""

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
    """Django ``IntegerField`` with SnapAdmin metadata. See :class:`SnapField`."""

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
    """Django ``DecimalField`` with SnapAdmin metadata. See :class:`SnapField`."""

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
    """Django ``DateTimeField`` with SnapAdmin metadata. See :class:`SnapField`."""

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
    """Django ``FileField`` with upload validation. See :class:`SnapField`."""

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
    """Django ``ImageField`` with upload validation. See :class:`SnapField`."""

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
    """Django ``BooleanField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapJSONField(models.JSONField, SnapField):
    """Django ``JSONField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapGenericIPAddressField(models.GenericIPAddressField, SnapField):
    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapForeignKey(models.ForeignKey, SnapField):
    """Django ``ForeignKey`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, to, on_delete=models.CASCADE, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(to=to, on_delete=on_delete, **self.handleDjangoKwargs(**kwargs))

class SnapOneToOneField(models.OneToOneField, SnapField):
    """Django ``OneToOneField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, to, on_delete=models.CASCADE, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(to=to, on_delete=on_delete, **self.handleDjangoKwargs(**kwargs))

class SnapManyToManyField(models.ManyToManyField, SnapField):
    """Django ``ManyToManyField`` with SnapAdmin metadata. See :class:`SnapField`."""

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

class SnapRichTextField(SanitizedHtmlOnSaveMixin, models.TextField, SnapField):
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
    """A computed, display-only column — no database column, no migration.

    ``func`` receives the model instance and returns what the admin should show::

        class Order(snap_models.SnapModel):
            total = snap.SnapFunctionField(
                func=lambda obj: f"{obj.quantity * obj.unit_price:.2f}",
                verbose_name="Total",
            )

    Pass ``safe_html=True`` only for markup you generate yourself; the returned
    value is escaped otherwise.
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
        value = self.func(obj)
        return mark_safe(value) if self.safe_html else value

class SnapStatusBadgeFieldChoice:
    """One coloured badge variant for :class:`SnapStatusBadgeField`.

    ``status_string`` is matched against the source field's value; the three
    colours style the badge drawn for it.
    """

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
    """Render another field's value as a coloured status badge in the changelist.

    Display-only, so it adds no database column and no migration::

        class Order(snap_models.SnapModel):
            status = snap.SnapCharField(max_length=20)
            status_badge = snap.SnapStatusBadgeField(
                field_name="status",
                choices=[
                    snap.SnapStatusBadgeFieldChoice("paid", "#065f46", "#d1fae5"),
                    snap.SnapStatusBadgeFieldChoice("refunded", "#991b1b", "#fee2e2"),
                ],
            )

    A value with no matching choice renders unstyled.
    """

    def __init__(self, field_name: str | None = None,
                 choices: typing.List[SnapStatusBadgeFieldChoice] | None = None, *,
                 verbose_name: str = None, style_arguments: dict = None, **kwargs):
        # Both may be written positionally: they are what the field *is*, and passing the
        # source field's name positionally is the obvious call. They used to be keyword-only,
        # so doing that failed with "missing 1 required keyword-only argument: 'field_name'" —
        # which reads as "you forgot it" about an argument that was in fact supplied.
        if not field_name:
            raise ValueError(
                "SnapStatusBadgeField requires 'field_name' — the model field whose value the "
                "badge renders, e.g. SnapStatusBadgeField('status', choices=[...])."
            )
        if not choices:
            raise ValueError(
                f"SnapStatusBadgeField('{field_name}') requires a non-empty 'choices' list of "
                "SnapStatusBadgeFieldChoice, one per value you want styled, e.g. "
                "choices=[SnapStatusBadgeFieldChoice('paid', '#065f46', '#d1fae5')]."
            )
        for index, choice in enumerate(choices):
            if not isinstance(choice, SnapStatusBadgeFieldChoice):
                raise ValueError(
                    f"SnapStatusBadgeField('{field_name}'): choices[{index}] is "
                    f"{type(choice).__name__}, expected SnapStatusBadgeFieldChoice — the colours "
                    "live on the choice object, so a bare value cannot be styled."
                )
        self.field_name = field_name
        self.choices = choices
        self.style_arguments = style_arguments or {}
        super().__init__(func=self._render_badge, verbose_name=verbose_name, safe_html=True, **kwargs)

    def _render_badge(self, obj) -> str:
        field_value = getattr(obj, self.field_name, "")
        display_method = getattr(obj, f"get_{self.field_name}_display", None)
        field_display = display_method() if display_method else field_value
        for choice in self.choices:
            if choice.status_string == field_value:
                return choice.get_html_choice(field_display, self.style_arguments)
        return field_display
