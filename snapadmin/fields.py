"""
snapadmin/fields.py

Custom field layer on top of Django's standard model fields.
...
"""

import datetime
import decimal
import json
import types
import typing
from enum import Enum

from django import forms
from django.db import models
from django.db.backends.utils import format_number
from django.core import checks, validators
from django.core.exceptions import FieldDoesNotExist, FieldError, ImproperlyConfigured
from django.db.models.expressions import Col
from django.db.models.lookups import Lookup
from django.db.models.query_utils import DeferredAttribute
from django.utils.functional import cached_property
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from snapadmin import validators as snap_validators
from snapadmin.conf import get_setting


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
        Include the field on the add/change form. Default ``False``, or the project-wide
        ``SNAPADMIN_SHOW_IN_FORM_DEFAULT`` setting when set — an explicit value here always wins.
    ``searchable``
        Add to the admin search box, the REST ``?search=`` filter and — on a model
        mirrored to Elasticsearch — the search mapping. Default ``False``.
    ``filterable``
        Add a sidebar filter in the admin and a ``?field=`` query filter in the API.
        Default ``False``.
    ``editable``
        Allow changes through the form and the API. Default ``True``. The one
        Snap kwarg that is also a Django kwarg, deliberately: ``editable=False``
        sets ``Field.editable``, which is what makes the restriction hold in a
        hand-written ``ModelForm`` or DRF serializer too, not only in the admin
        SnapAdmin generates. It still adds no migration — the mixin's
        ``deconstruct()`` keeps it out, since it affects no column.
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
    nothing to detect. ``editable`` is the one that needs help to keep that
    promise: it is passed through to Django on purpose (see above), and
    ``Field.deconstruct()`` would report it, so the mixin's ``deconstruct()``
    wrapper drops it again — an ``editable=False`` already recorded by an
    earlier release's ``AlterField`` converges with no further migration.
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
            # Project-wide escape hatch for a codebase adopting SnapAdmin onto
            # models that never set show_in_form anywhere: without it, every
            # field falls back to False and register_admin() generates an
            # empty change form with no error (see snapadmin.checks.W015).
            # An explicit per-field show_in_form= still wins — this only
            # changes what an *unset* field resolves to.
            SnapFieldAttributeEnum.SHOW_IN_FORM: get_setting("SNAPADMIN_SHOW_IN_FORM_DEFAULT", False),
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
        #
        # `editable` goes the other way, and is the one Snap kwarg that is also
        # a Django one. The collision is deliberate — Django's `editable` is
        # what makes "no changes through the form or the API" true outside the
        # code SnapAdmin generates (it drops the field from every ModelForm,
        # skips it in full_clean(), and DRF maps it to read_only=True) — but
        # `Field.deconstruct()` emits it whenever it differs from the default,
        # and it affects no column. Left in, `editable=False` on 24 fields is 24
        # AlterFields that sqlmigrate renders as `(no-op)` and a red
        # `makemigrations --check`. Popping it on both sides of every
        # comparison also means a project that already generated such a
        # migration converges with no further migration at all (#EXT1c).
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
            kw.pop(SnapFieldAttributeEnum.EDITABLE.value, None)
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


def _detach_editable_from_deconstruct(field: models.Field) -> None:
    """Keep a wrapper-set ``editable`` out of ``deconstruct()``.

    :func:`snap_field`'s "adds no database migration" promise rests on its
    kwargs being applied *after* ``Field.__init__``. That holds for every
    attribute SnapAdmin invented, and fails for the one it shares with Django:
    ``Field.deconstruct()`` reports **live attribute state**, not the arguments
    the constructor was given, so a post-hoc ``setattr(field, "editable", ...)``
    lands in it exactly as a constructor kwarg would.

    Only a wrapper-set value is stripped. A caller who wrote
    ``snap_field(models.CharField(..., editable=False), searchable=True)`` meant
    Django's kwarg, migration included, and removing it from their history would
    be the same bug pointed the other way.
    """
    original_deconstruct = field.deconstruct

    def deconstruct(self):
        name, path, args, kwargs = original_deconstruct()
        kwargs.pop(SnapFieldAttributeEnum.EDITABLE.value, None)
        return name, path, args, kwargs

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

    Sets **only** the attributes actually passed — unlike a ``Snap*Field``
    subclass, this wrapper never applies a default for one that is left out
    (so ``SNAPADMIN_SHOW_IN_FORM_DEFAULT`` does not reach a field attached
    this way; pass ``show_in_form=`` explicitly if you need it raised here).

    Adds no database migration for every kwarg **except** ``required``: the
    metadata kwargs are mutated *after* ``Field.__init__``, and SnapAdmin
    invented every attribute name they set, so Django's ``deconstruct()`` has
    no reason to look at them. ``required`` is the deliberate exception — it is
    schema-affecting by design, same as passing ``null=``/``blank=`` directly
    to the Django field, and **can** produce a migration the same way changing
    a ``Snap*Field``'s ``required`` would.

    ``editable`` needs help to keep that promise, being the one name shared with
    Django: ``Field.deconstruct()`` reports live attribute state rather than
    constructor arguments, so it would emit a wrapper-set ``editable`` and turn
    a form-level flag into a no-op ``AlterField``. It is stripped — see
    :func:`_detach_editable_from_deconstruct`, which leaves an ``editable=``
    passed to the *Django* field's own constructor exactly where the caller put
    it.

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

    if SnapFieldAttributeEnum.EDITABLE.value in kwargs:
        _detach_editable_from_deconstruct(field)

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
    """Django ``SlugField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        kwargs.setdefault(DjangoFieldAttributeEnum.MAX_LENGTH.value, 50)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapURLField(models.URLField, SnapField):
    """Django ``URLField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapUUIDField(models.UUIDField, SnapField):
    """Django ``UUIDField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapIntegerField(models.IntegerField, SnapField):
    """Django ``IntegerField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapPositiveIntegerField(models.PositiveIntegerField, SnapField):
    """Django ``PositiveIntegerField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapFloatField(models.FloatField, SnapField):
    """Django ``FloatField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapDecimalField(models.DecimalField, SnapField):
    """Django ``DecimalField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapBigIntegerField(models.BigIntegerField, SnapField):
    """Django ``BigIntegerField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapDateField(models.DateField, SnapField):
    """Django ``DateField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapDateTimeField(models.DateTimeField, SnapField):
    """Django ``DateTimeField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapTimeField(models.TimeField, SnapField):
    """Django ``TimeField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapDurationField(models.DurationField, SnapField):
    """Django ``DurationField`` with SnapAdmin metadata. See :class:`SnapField`."""

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
    """Django ``GenericIPAddressField`` with SnapAdmin metadata. See :class:`SnapField`."""

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
    """Django ``SmallIntegerField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapPositiveSmallIntegerField(models.PositiveSmallIntegerField, SnapField):
    """Django ``PositiveSmallIntegerField`` with SnapAdmin metadata. See :class:`SnapField`."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

class SnapPositiveBigIntegerField(models.PositiveBigIntegerField, SnapField):
    """Django ``PositiveBigIntegerField`` with SnapAdmin metadata. See :class:`SnapField`."""

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

# ===========================================================================
# Encrypted fields (#CRYPT1c)
# ===========================================================================

# ── The blind index (#CRYPT1d) ───────────────────────────────────────────────

class _BlindIndexAttribute(DeferredAttribute):
    """Descriptor that derives the blind index from the live source value.

    The sibling column is derived data, and derived data goes stale. Computing
    it on read rather than trusting what was last written means it is correct
    for ``Model.validate_unique()`` — which runs *before* the save that would
    have refreshed it — and for any code that reads the attribute between an
    assignment and a save.

    Three fallbacks, all to the stored value: the source field was deferred
    (``.only(...)``), no keyset resolves yet, or the source holds something that
    cannot be encoded. None of them can silently write a wrong index: the value
    that reaches the column comes back through here at ``pre_save`` time, and a
    write with no keyset is refused outright by the encrypted field itself.
    """

    def __get__(self, instance, cls=None):
        if instance is not None:
            derived = self.field.derive_from_instance(instance)
            if derived is not _NO_INDEX:
                return derived
        return super().__get__(instance, cls)

    def __set__(self, instance, value) -> None:
        """Store the assigned value, and — crucially — make this a *data*
        descriptor.

        ``DeferredAttribute`` defines only ``__get__``, which makes it a
        non-data descriptor: an entry in ``instance.__dict__`` shadows it
        completely and ``__get__`` is never consulted again. Since
        ``Model.__init__`` writes every field into ``__dict__``, the derivation
        above would never run without this method. The assigned value is kept
        because it is what the fallbacks in ``__get__`` return — a row loaded
        with the source field deferred, or one loaded with no keyset, still
        reports the index the database holds. While the source *is* loaded the
        derived value wins, so the index can never be talked out of agreeing
        with the value it indexes."""
        instance.__dict__[self.field.attname] = value


#: Sentinel: "this instance cannot produce an index right now, use the stored one."
_NO_INDEX = object()


class SnapBlindIndexField(models.CharField):
    """The ``<field>_bi`` sibling column an encrypted field adds for equality lookups.

    Never declared by hand — a ``SnapEncrypted*Field(blind_index=True)`` adds it,
    and ``deconstruct()`` round-trips the pair so a migration rebuilds exactly
    what the model declared. It is its own class rather than a plain
    ``CharField`` for two reasons: it knows which field it indexes (so it can
    re-derive its value instead of trusting the last write), and its
    :meth:`contribute_to_class` is idempotent, which is what lets a historical
    model render from a migration that lists both the encrypted field and this
    one without ending up with the column twice.
    """

    descriptor_class = _BlindIndexAttribute

    #: Marker other layers detect the sibling by. An attribute rather than an
    #: ``isinstance`` check for the same reason ``is_snap_encrypted`` is one:
    #: a project's own subclass is recognised, and a module that must not
    #: import ``snapadmin.fields`` (the API filter builder runs while apps are
    #: still loading) can still tell. Emphatically *not* a ``hasattr`` test on
    #: ``source_field`` — third-party fields use that name too, and silently
    #: dropping their filters would be a bug nobody would think to look for.
    is_snap_blind_index: bool = True

    def __init__(self, *args, source_field: str = "", **kwargs):
        from snapadmin.encryption import blind_index

        self.source_field = source_field
        kwargs.setdefault("max_length", blind_index.INDEX_CHARS)
        kwargs.setdefault("null", True)
        kwargs.setdefault("blank", True)
        kwargs.setdefault("editable", False)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.source_field:
            kwargs["source_field"] = self.source_field
        return name, path, args, kwargs

    def contribute_to_class(self, cls, name, **kwargs) -> None:
        """Attach, unless a field of this name is already on the class.

        Django renders a historical model from a migration's full field list,
        which contains both the encrypted field and this sibling. The encrypted
        field contributes first and adds its own sibling; without this guard the
        explicit one would be added on top of it and the model would try to
        create the column twice.
        """
        if any(existing.name == name for existing in cls._meta.local_fields):
            return
        super().contribute_to_class(cls, name, **kwargs)

    def derive_from_instance(self, instance) -> object:
        """This row's index, derived from the source field's current value.

        Returns the ``_NO_INDEX`` sentinel when it cannot be derived, which
        makes the descriptor fall back to whatever the database holds.
        """
        if not self.source_field:
            return _NO_INDEX
        try:
            source = self.model._meta.get_field(self.source_field)
        except FieldDoesNotExist:
            # A hand-written sibling naming a field that is not there. Fall
            # back to the stored value rather than breaking attribute access;
            # #CRYPT1e's system check is what tells the developer about it.
            return _NO_INDEX
        if not getattr(source, "is_snap_encrypted", False):
            # A hand-written sibling pointed at an ordinary field. It has no
            # blind_index_of(), and raising AttributeError on every attribute
            # read would be a far worse answer than the stored value.
            # snapadmin.W020 is what tells the developer about it.
            return _NO_INDEX
        if source.attname not in instance.__dict__:
            return _NO_INDEX  # deferred — the stored value is the best answer
        return source.blind_index_of(instance.__dict__[source.attname])


class _BlindIndexLookup(Lookup):
    """Base for the lookups an encrypted field answers through its sibling column.

    Both rewrite the query completely: the left-hand side becomes the ``_bi``
    column and the right-hand side becomes one HMAC per key in the keyset. The
    SQL that reaches the database therefore contains no plaintext at all — not
    in a parameter, not in the query log, not in a slow-query report.

    Matching against *every* key, rather than only the active one, is what makes
    a rotation seamless: until ``snapadmin_encrypt_fields --rotate`` has
    converged the table, rows carry indexes under whichever key was active when
    they were last written, and a query that only tried the newest one would
    quietly stop finding them.
    """

    def get_prep_lookup(self):
        # Keep the caller's value untouched; each one is prepared individually
        # in as_sql() so `exact` and `in` share one code path.
        return self.rhs

    def _rhs_values(self) -> list:
        raise NotImplementedError  # pragma: no cover - both subclasses define it

    def as_sql(self, compiler, connection):
        from django.core.exceptions import EmptyResultSet

        field = self.lhs.output_field
        alias = getattr(self.lhs, "alias", None)
        if alias is None:
            raise FieldError(
                f"{field.name!r} is encrypted and can only be compared as a plain column "
                "reference, not through this expression."
            )

        sibling = field.model._meta.get_field(field.blind_index_name)
        lhs_sql, lhs_params = compiler.compile(Col(alias, sibling))

        candidates: list[str] = []
        for value in self._rhs_values():
            prepared = field.get_prep_value(value)
            if prepared is None:
                continue
            candidates.extend(field.blind_index_candidates(prepared))
        if not candidates:
            raise EmptyResultSet

        placeholders = ", ".join(["%s"] * len(candidates))
        return f"{lhs_sql} IN ({placeholders})", list(lhs_params) + candidates


class _BlindIndexExact(_BlindIndexLookup):
    lookup_name = "exact"

    def _rhs_values(self) -> list:
        return [self.rhs]


class _BlindIndexIn(_BlindIndexLookup):
    lookup_name = "in"

    def _rhs_values(self) -> list:
        # A subquery RHS (`field__in=Other.objects.values("x")`) cannot be
        # rewritten: the index has to be computed from the values themselves,
        # in Python, and a Query has none to offer. Without this guard it
        # surfaces as `TypeError: 'Query' object is not iterable` from inside
        # SQL compilation, which says nothing about encryption.
        if hasattr(self.rhs, "resolve_expression") or hasattr(self.rhs, "query"):
            raise FieldError(
                f"{self.lhs.output_field.name!r} is encrypted, so `__in` has to hash each "
                "value before it reaches the database — which means the values have to be "
                "in Python, not in a subquery. Evaluate the inner queryset first "
                "(`list(...)` or `.values_list(..., flat=True)`) and pass the result."
            )
        return list(self.rhs)


class SnapEncryptedField:
    """Mixin turning any Snap field into one that stores ciphertext.

    The deal it makes is narrow: **the column holds a ``snap1.`` envelope, the
    instance holds the field's ordinary Python value.** Nothing above the field
    changes — a ``SnapEncryptedDateField`` still renders a date picker, still
    validates a date, still hands your code a :class:`datetime.date`. Only the
    two ends of the database round trip are rewritten::

        class Patient(snap_models.SnapModel):
            name = snap.SnapCharField(max_length=200)
            ssn  = snap.SnapEncryptedCharField(max_length=32, show_in_list=False)

        Patient.objects.create(name="A. Wiese", ssn="123-45-6789")
        # the column now holds: snap1.2026-09.<nonce>.<ciphertext>

    **The column becomes text.** :meth:`db_type` returns the backend's text type
    regardless of the wrapped field, because a ciphertext is not a number, a
    date or a JSON document — it is 100-odd characters of base64url. The
    declared type still drives validation, the form widget and the Python value
    you get back, which is the whole point of subclassing the real field rather
    than making everything a ``TextField`` and leaving conversion to the caller.
    :meth:`db_check` is suppressed for the same reason: a numeric ``CHECK``
    constraint would reject every value the column will ever hold.

    **Encryption happens at the last possible moment** — :meth:`get_db_prep_save`,
    the single choke point every write goes through (``save()``,
    ``bulk_create()``, ``bulk_update()`` and ``QuerySet.update()`` all land
    here), and decryption in :meth:`from_db_value`, which every read goes
    through including ``values()``, ``values_list()`` and ``refresh_from_db()``.

    **Never twice.** A value that already parses as an envelope is written
    verbatim instead of being encrypted again. Double encryption is the one
    failure in this feature that is both silent and permanent: nothing raises,
    the row simply stops being readable, and by the time anyone notices, the
    backup holding the single-encrypted version has aged out. The guard is
    deliberately strict — a string merely *starting* with ``snap1.`` is not
    enough, it has to parse — so a user typing something envelope-shaped into a
    text box is still treated as the plaintext it is.

    **NULL stays NULL.** ``None`` is written as SQL ``NULL`` and read back as
    ``None``; it is never encrypted, because a nullable column whose NULLs were
    ciphertext could not be queried with ``__isnull`` at all. The empty string
    *is* encrypted like any other value, so ``""`` and ``None`` stay distinct
    after a round trip.

    **What you give up.** The database can no longer see the value, so it cannot
    compare, order or index it: ``icontains``, ``gt``, ``startswith`` and
    ``ORDER BY`` are impossible rather than merely unsupported, and a plain
    ``exact`` compares against a ciphertext that carries a fresh random nonce —
    it would match nothing, every time. Those lookups raise instead of silently
    returning an empty queryset (#CRYPT1d), and ``blind_index=True`` is the
    opt-in route back to equality matching.

    **Fixtures keep the ciphertext.** :meth:`value_to_string` emits the envelope
    rather than the plaintext, so ``dumpdata`` output is no more sensitive than
    the database it came from, and ``loaddata`` stores it back unchanged through
    the never-twice guard. A dump is therefore only readable where the keyset
    is — which is the correct answer, and the same one ``pg_dump`` already
    gives.
    """

    #: Encrypted fields stay off the changelist unless asked for. Two reasons,
    #: and the second is the one that bites: a column of secrets is a poor
    #: default in a screen people leave open, and a changelist column is
    #: *sortable*, which on ciphertext orders rows by their base64url envelope
    #: — silently, since ordering never passes through the lookup guard.
    #: :meth:`snapadmin.admin_gen` also drops encrypted fields from
    #: ``sortable_by`` so an explicit ``show_in_list=True`` cannot reintroduce
    #: the sorting half.
    _SNAP_DEFAULT_SHOW_IN_LIST = False

    #: Marker every other layer detects an encrypted field by
    #: (``snapadmin.encryption.keys.has_encrypted_fields``, the system checks,
    #: the leak guards). An attribute rather than an ``isinstance`` test, so a
    #: project's own subclass is recognised and so ``checks.py`` never has to
    #: import the cipher — and with it the ``[encryption]`` extra — merely to
    #: run ``manage.py check``.
    is_snap_encrypted: bool = True

    def _initializeSnapLogic(self, **kwargs) -> dict:
        """Flip the ``show_in_list`` default before the shared logic reads it.

        It has to happen here rather than in ``__init__``: every concrete field
        calls ``_initializeSnapLogic()`` and then ``handleDjangoKwargs()``,
        which strips the Snap-only kwargs — by the time ``__init__`` further up
        the MRO runs, ``show_in_list`` is long gone. An explicit
        ``show_in_list=True`` still wins; this only changes what an *unset*
        field resolves to.
        """
        kwargs.setdefault(
            SnapFieldAttributeEnum.SHOW_IN_LIST.value, self._SNAP_DEFAULT_SHOW_IN_LIST
        )
        return super()._initializeSnapLogic(**kwargs)

    def __init__(self, *args, blind_index: bool = False, **kwargs):
        self.blind_index = blind_index
        super().__init__(*args, **kwargs)
        # Captured before contribute_to_class moves it: with a blind index the
        # uniqueness belongs on the sibling column (the ciphertext column is
        # unique by construction — every row has its own nonce), but
        # deconstruct() must still report what the model declared so the
        # migration rebuilds the same pair.
        self._declared_unique = self._unique

    #: Set by :meth:`contribute_to_class` to the sibling column's name, or left
    #: ``None`` when ``blind_index`` is off. Public: the lookups and the
    #: management commands both ask the field for it rather than re-deriving
    #: the naming convention.
    blind_index_name: str | None = None

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.blind_index:
            kwargs["blind_index"] = True
            if getattr(self, "_declared_unique", False):
                # Restored because contribute_to_class cleared the live flag.
                kwargs["unique"] = True
        return name, path, args, kwargs

    def contribute_to_class(self, cls, name, **kwargs) -> None:
        """Attach to the model, adding the ``<name>_bi`` sibling when asked."""
        super().contribute_to_class(cls, name, **kwargs)
        if not self.blind_index or cls._meta.abstract:
            return
        self.blind_index_name = f"{name}_bi"
        declared_unique = getattr(self, "_declared_unique", False)
        # A unique constraint on the ciphertext column would be satisfied by
        # every row — the nonce guarantees it — so it moves to the index, where
        # it means what the model said it meant.
        self._unique = False
        sibling = SnapBlindIndexField(
            source_field=name,
            unique=declared_unique,
            db_index=not declared_unique,
        )
        cls.add_to_class(self.blind_index_name, sibling)

    def blind_index_of(self, value) -> str | None:
        """This value's stored index, or ``None`` when one cannot be derived.

        ``None`` in, ``None`` out — a NULL row has no index, which keeps
        ``__isnull`` honest. Everything else that can go wrong here (no keyset
        yet, a value the field cannot encode) also yields ``None`` rather than
        raising: this runs on attribute *reads*, where raising would turn a
        misconfiguration into an error in a place nobody can act on it. The
        write path refuses loudly in its own right, so no wrong index can reach
        the column.
        """
        from snapadmin.encryption import blind_index

        if value is None or not self.blind_index:
            return None
        try:
            return blind_index.index_for_write(
                self.encode_plaintext(value), aad=self.encryption_aad()
            )
        except (ImproperlyConfigured, TypeError, ValueError, decimal.InvalidOperation):
            return None

    def blind_index_candidates(self, value) -> list[str]:
        """Every index *value* could be stored under — one per key in the keyset."""
        from snapadmin.encryption import blind_index

        return blind_index.index_candidates(
            self.encode_plaintext(value), aad=self.encryption_aad()
        )

    # ── Lookups ─────────────────────────────────────────────────────────────

    #: The only lookups an encrypted column can answer honestly. ``isnull``
    #: always works because NULL is never encrypted; ``exact`` and ``in`` work
    #: only through a blind index, and are refused without one rather than
    #: compared against a ciphertext that carries a fresh nonce and would match
    #: nothing.
    _INDEXED_LOOKUPS = {"exact": "_BlindIndexExact", "in": "_BlindIndexIn"}

    def _refuse(self, name: str) -> None:
        supported = "`__isnull`" + (
            ", `__exact` and `__in`" if self.blind_index
            else " (add blind_index=True for `__exact` and `__in`)"
        )
        raise FieldError(
            f"{self.name!r} is an encrypted field, so the database cannot evaluate "
            f"`__{name}` on it — the column holds ciphertext, and comparing, ordering or "
            "pattern-matching it would either error or, far worse, match nothing at all "
            f"and look like an empty table. Supported here: {supported}. Substring search, "
            "ordering and range queries are not possible on an encrypted column; keep a "
            "separate unencrypted column for what you need to query on."
        )

    def get_lookup(self, lookup_name: str):
        """Answer, or refuse with an explanation.

        Refusing here rather than returning ``None`` is deliberate: ``None``
        sends Django on to ``try_transform()``, whose own error talks about
        unsupported lookups in general and says nothing about why *this* field
        cannot answer.
        """
        if lookup_name == "isnull":
            return super().get_lookup(lookup_name)
        if self.blind_index and lookup_name in self._INDEXED_LOOKUPS:
            return globals()[self._INDEXED_LOOKUPS[lookup_name]]
        self._refuse(lookup_name)

    def get_transform(self, lookup_name: str):
        """Refuse transforms too — and this is not covered by :meth:`get_lookup`.

        Django only consults ``get_lookup()`` for the *final* name in a chain.
        ``joined__year__gt`` goes straight to ``try_transform('year')``, and the
        ``gt`` that follows is then applied to the transform's own output field
        — an ``IntegerField`` that knows nothing about encryption. Without this,
        that filter would compile happily and compare a year against base64url
        ciphertext, which is the silent-empty-result failure this whole guard
        exists to prevent. The same applies to a key transform on an encrypted
        ``JSONField``.
        """
        self._refuse(lookup_name)

    def get_internal_type(self) -> str:
        """``TextField`` — what the *column* is, not what the field means.

        This is not cosmetic. Every backend picks its read converters off the
        internal type (``sqlite3`` alone installs one for ``DateTimeField``,
        ``DateField``, ``TimeField``, ``DecimalField``, ``BooleanField`` and
        ``UUIDField``), and each of those converters would be handed a base64url
        envelope and would fail on it before :meth:`from_db_value` ever ran.
        Declaring the column for what it is keeps the read path clean; the
        declared field class still supplies validation, the widget and the
        Python type.
        """
        return "TextField"

    def db_type(self, connection) -> str:
        """The backend's text type — a ciphertext is text, whatever the field is."""
        return connection.data_types["TextField"]

    def db_check(self, connection) -> None:
        """No column constraint: every check would be about the plaintext."""
        return None

    def rel_db_type(self, connection) -> str:
        """A relation pointing here would still point at a text column."""
        return self.db_type(connection)

    def encryption_aad(self) -> str:
        """This field's additional authenticated data — ``app.model.field``.

        Binding the ciphertext to the column it lives in is what stops a value
        being lifted out of one column and pasted into another. The cost, stated
        here because it is invisible until it bites: **renaming the app, the
        model or the field changes this string**, and rows written under the old
        name stop decrypting until they are re-encrypted with
        ``manage.py snapadmin_encrypt_fields``. Plan a rename accordingly.
        """
        from snapadmin.encryption.cipher import aad_for

        model = getattr(self, "model", None)
        if model is None:
            raise ImproperlyConfigured(
                f"{type(self).__name__} is not attached to a model yet, so it has no "
                "app/model/field identity to bind its ciphertext to. Encrypted fields "
                "can only encrypt once they are declared on a model."
            )
        return aad_for(model._meta.app_label, model._meta.model_name, self.name)

    # ── plaintext ⇄ text ────────────────────────────────────────────────────
    #
    # Every member of the family stores text, so each one says how its natural
    # Python value becomes a string and how it comes back. The default is the
    # identity pair used by the character-based fields; the numeric, date and
    # JSON members override it.

    def encode_plaintext(self, value) -> str:
        """The field's Python value as the string that gets encrypted."""
        return str(value)

    def decode_plaintext(self, text: str):
        """The decrypted string back as the field's Python value."""
        return text

    def _already_encrypted(self, value) -> bool:
        """Whether *value* is a stored envelope that must not be re-encrypted.

        Strict on purpose: ``looks_encrypted()`` alone accepts anything shaped
        like ``snapN.a.b.c``, which a user could type into a text box. Requiring
        a successful parse means only something genuinely produced by the cipher
        takes the pass-through path.
        """
        from snapadmin.encryption.cipher import DecryptionError, Envelope, looks_encrypted

        if not looks_encrypted(value):
            return False
        try:
            Envelope.parse(value)
        except DecryptionError:
            return False
        return True

    def get_db_prep_save(self, value, connection):
        """Encrypt on the way to the column — the write choke point.

        Mirrors :meth:`django.db.models.Field.get_db_prep_save`, including its
        expression guard: ``bulk_update`` hands the whole ``CASE`` expression
        through here, and an expression compiles itself. Without the guard its
        ``str()`` would be encrypted and stored as if it were the value — which
        is exactly as bad as it sounds, and entirely silent.
        """
        if hasattr(value, "as_sql"):
            return value
        return self.get_db_prep_value(value, connection, prepared=False)

    def get_db_prep_value(self, value, connection, prepared=False):
        """Turn one Python value into the ciphertext the column stores.

        ``prepared=True`` marks the **lookup** right-hand side, which is left
        alone: encrypting it would produce a fresh random nonce that matches no
        row, turning every query into a silently empty result. Encrypted fields
        refuse those lookups outright instead (see ``blind_index``), which is a
        far better answer than an empty queryset.
        """
        from snapadmin.encryption.cipher import encrypt

        if value is None or prepared or hasattr(value, "as_sql"):
            return value
        if self._already_encrypted(value):
            return value
        return encrypt(self.encode_plaintext(value), aad=self.encryption_aad())

    def from_db_value(self, value, expression, connection):
        """Decrypt on the way out — every read path funnels through here."""
        from snapadmin.encryption.cipher import decrypt

        if value is None:
            return None
        return self.decode_plaintext(decrypt(value, aad=self.encryption_aad()))

    def value_to_string(self, obj) -> str | None:
        """Serialise for ``dumpdata`` as ciphertext, never as the plaintext."""
        from snapadmin.encryption.cipher import encrypt

        value = self.value_from_object(obj)
        if value is None:
            return None
        if self._already_encrypted(value):
            return value
        return encrypt(self.encode_plaintext(value), aad=self.encryption_aad())

    def to_python(self, value):
        """Leave a stored envelope opaque; convert anything else as usual.

        This is what makes ``loaddata`` work without a keyset: the deserialiser
        hands the envelope straight back to :meth:`get_db_prep_save`, which
        recognises it and stores it verbatim. Nothing is decrypted on the way
        in, so a fixture can be restored into an environment that cannot read
        it yet.
        """
        if self._already_encrypted(value):
            return value
        return super().to_python(value)


class SnapEncryptedCharField(SnapEncryptedField, models.CharField, SnapField):
    """Encrypted single-line text. ``max_length`` bounds the *plaintext*.

    The column is text, so ``max_length`` is optional here (unlike Django's
    ``CharField``) and constrains only what the application may store.
    """

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    def check(self, **kwargs):
        # fields.E120 ("CharFields must define a max_length") is about the
        # column width, and this field's column is text — there is nothing to
        # size. Everything else CharField checks still applies.
        return [
            error for error in super().check(**kwargs)
            if not (error.id == "fields.E120" and self.max_length is None)
        ]


class SnapEncryptedTextField(SnapEncryptedField, models.TextField, SnapField):
    """Encrypted multi-line text."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapEncryptedEmailField(SnapEncryptedField, models.EmailField, SnapField):
    """Encrypted email address — still validated as an address on the way in.

    The usual reason to encrypt an email address is that it identifies a person.
    Note that ``filter(email="...")`` cannot work on ciphertext; pair it with
    ``blind_index=True`` if you need to look rows up by address.
    """

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))


class SnapEncryptedJSONField(SnapEncryptedField, models.JSONField, SnapField):
    """Encrypted JSON document — encrypted whole, so no key lookups.

    The entire document is one ciphertext, which means the database cannot
    reach inside it: ``data__key`` transforms and containment lookups are
    impossible, not merely slow. Encrypt a JSON field when the *document* is the
    secret; keep the queryable keys in their own columns.
    """

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    def encode_plaintext(self, value) -> str:
        return json.dumps(value, cls=self.encoder)

    def decode_plaintext(self, text: str):
        return json.loads(text, cls=self.decoder)


class SnapEncryptedIntegerField(SnapEncryptedField, models.IntegerField, SnapField):
    """Encrypted integer. Sums, ordering and range filters are not available."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    @cached_property
    def validators(self) -> list:
        """Django's ``IntegerField`` range validators do not apply here.

        ``IntegerField.validators`` derives its min/max from the *column's*
        integer range (``connection.ops.integer_field_range``). This column is
        text and has no such range, so the derived limits would be a fiction —
        and looking them up under the ``TextField`` internal type raises
        ``KeyError`` outright. Everything a caller declared (``validators=[...]``,
        ``MinValueValidator``, …) still applies.
        """
        return [*self.default_validators, *self._validators]

    def encode_plaintext(self, value) -> str:
        return str(int(value))

    def decode_plaintext(self, text: str) -> int:
        return int(text)


class SnapEncryptedDecimalField(SnapEncryptedField, models.DecimalField, SnapField):
    """Encrypted decimal — quantised to ``decimal_places`` exactly as the
    unencrypted field would be, so switching a column to this type does not
    silently start keeping extra digits."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    def encode_plaintext(self, value) -> str:
        return format_number(self.to_python(value), self.max_digits, self.decimal_places)

    def decode_plaintext(self, text: str) -> decimal.Decimal:
        return decimal.Decimal(text)


class SnapEncryptedDateField(SnapEncryptedField, models.DateField, SnapField):
    """Encrypted date. Stored ISO-8601; ``__year``/``__gte`` are not available."""

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    def encode_plaintext(self, value) -> str:
        return self.to_python(value).isoformat()

    def decode_plaintext(self, text: str) -> datetime.date:
        return datetime.date.fromisoformat(text)


class SnapEncryptedDateTimeField(SnapEncryptedField, models.DateTimeField, SnapField):
    """Encrypted timestamp, stored ISO-8601 with its offset.

    ``auto_now`` / ``auto_now_add`` still work, but think twice: a timestamp is
    usually something you want to filter and order by, and neither is possible
    once the column is ciphertext.
    """

    def __init__(self, **kwargs):
        kwargs = self._initializeSnapLogic(**kwargs)
        super().__init__(**self.handleDjangoKwargs(**kwargs))

    def encode_plaintext(self, value) -> str:
        return self.get_prep_value(value).isoformat()

    def decode_plaintext(self, text: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(text)


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
