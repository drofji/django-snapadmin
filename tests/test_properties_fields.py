"""
tests/test_properties_fields.py

Property-based tests for Snap fields and ``snap_field()`` (#QA1d, part 3).

The package promises that its field kwargs **never add a database migration**
(rules: "New SnapField flags must round-trip through ``deconstruct()``
unchanged so ``makemigrations --check`` stays clean"). Django's migration
autodetector clones every field through ``deconstruct()`` → constructor →
``deconstruct()`` and compares the results, so the promise is really three
laws, checked here for every combination of Snap and Django kwargs
``hypothesis`` generates rather than for the handful the example suites name:

1. **Fixed point** — reconstructing a field from its own ``deconstruct()``
   output deconstructs identically. If not, ``makemigrations`` never converges.
2. **Snap kwargs are invisible** — no SnapAdmin-only name appears in
   ``deconstruct()``, and adding any of them (``required`` apart, which is
   schema-affecting by design) changes nothing ``deconstruct()`` reports.
3. **``required`` decides nullability** — unless ``null``/``blank`` are given
   explicitly, ``required=True`` means ``NOT NULL`` and ``required=False``
   means nullable, and that survives the round trip.

``snap_field()`` makes the same promise for a plain Django field it wraps, and
refuses any kwarg it does not know by name.
"""

from __future__ import annotations

import pytest
from django.db import models
from django.utils.module_loading import import_string
from hypothesis import given
from hypothesis import strategies as st

from snapadmin import fields as snap
from snapadmin.fields import (
    _SNAP_FIELD_WRAPPER_KWARGS,
    SnapFieldAttributeEnum,
    snap_field,
)

SNAP_ONLY_NAMES = frozenset(e.value for e in SnapFieldAttributeEnum)
UPLOAD_LIMITS = frozenset({"allowed_extensions", "allowed_encodings", "max_size_bytes"})

#: ``(field class, the Django kwargs it needs to exist)``.
FIELD_SPECS = [
    (snap.SnapCharField, {"max_length": 40}),
    (snap.SnapTextField, {}),
    (snap.SnapEmailField, {}),
    (snap.SnapSlugField, {}),
    (snap.SnapURLField, {}),
    (snap.SnapUUIDField, {}),
    (snap.SnapIntegerField, {}),
    (snap.SnapPositiveIntegerField, {}),
    (snap.SnapBigIntegerField, {}),
    (snap.SnapFloatField, {}),
    (snap.SnapDecimalField, {"max_digits": 10, "decimal_places": 2}),
    (snap.SnapBooleanField, {}),
    (snap.SnapDateField, {}),
    (snap.SnapDateTimeField, {}),
    (snap.SnapDateTimeField, {"auto_now": True}),
    (snap.SnapDateTimeField, {"auto_now_add": True}),
    (snap.SnapTimeField, {}),
    (snap.SnapDurationField, {}),
    (snap.SnapJSONField, {}),
    (snap.SnapGenericIPAddressField, {}),
    (snap.SnapPhoneField, {}),
    (snap.SnapColorField, {}),
    (snap.SnapRichTextField, {}),
    (snap.SnapFileField, {"upload_to": "uploads/"}),
    (snap.SnapFileField, {"upload_to": "uploads/", "allowed_extensions": ["pdf", "png"], "max_size_bytes": 1024}),
    (snap.SnapEncryptedCharField, {"max_length": 32}),
    (snap.SnapEncryptedCharField, {"max_length": 32, "blind_index": True}),
    (snap.SnapEncryptedTextField, {}),
]

#: Snap kwargs that describe the admin/API/search surface and must never reach
#: ``deconstruct()``. ``required`` is left out on purpose (law 3); so are the
#: three file-upload kwargs, which only a file field accepts.
METADATA_KWARGS = st.fixed_dictionaries(
    {},
    optional={
        "show_in_list": st.booleans(),
        "show_in_form": st.booleans(),
        "searchable": st.booleans(),
        "filterable": st.booleans(),
        "editable": st.booleans(),
        "updatable": st.booleans(),
        "autocomplete": st.booleans(),
        "tab": st.one_of(st.none(), st.text(max_size=12)),
        "row": st.one_of(st.none(), st.text(max_size=12)),
    },
)

DJANGO_KWARGS = st.fixed_dictionaries(
    {},
    optional={
        "verbose_name": st.text(max_size=20),
        "help_text": st.text(max_size=20),
        "db_index": st.booleans(),
        "db_comment": st.text(max_size=20),
    },
)


def _rebuild(field: models.Field) -> models.Field:
    _name, path, args, kwargs = field.deconstruct()
    return import_string(path)(*args, **kwargs)


def _is_auto_now(django_kwargs: dict) -> bool:
    return bool(django_kwargs.get("auto_now") or django_kwargs.get("auto_now_add"))


def _state(field: models.Field) -> tuple:
    """What the migration autodetector compares: everything but the name."""
    _name, path, args, kwargs = field.deconstruct()
    return path, list(args), kwargs


class TestDeconstructLaws:
    @given(spec=st.sampled_from(FIELD_SPECS), meta=METADATA_KWARGS, django=DJANGO_KWARGS, required=st.booleans())
    def test_a_rebuilt_field_deconstructs_identically(self, spec, meta, django, required):
        field_cls, base = spec
        field = field_cls(**base, **django, **meta, required=required)

        rebuilt = _rebuild(field)

        assert _state(rebuilt) == _state(field)
        assert _state(_rebuild(rebuilt)) == _state(field)

    @given(spec=st.sampled_from(FIELD_SPECS), meta=METADATA_KWARGS, django=DJANGO_KWARGS, required=st.booleans())
    def test_no_snap_only_name_reaches_deconstruct(self, spec, meta, django, required):
        field_cls, base = spec

        _name, _path, _args, kwargs = field_cls(**base, **django, **meta, required=required).deconstruct()

        # The documented exception: on SnapFileField/SnapImageField the three
        # upload limits are serialised the way Django serialises `validators=`
        # (f2a8edc — a clone must keep its limits), so they reach deconstruct()
        # exactly when they were given. Every other Snap-only name never does.
        upload_limits_given = set(base) & UPLOAD_LIMITS
        assert set(kwargs) & SNAP_ONLY_NAMES == upload_limits_given

    @given(
        extensions=st.one_of(st.none(), st.lists(st.from_regex(r"\A[a-z0-9]{1,5}\Z"), max_size=4)),
        encodings=st.one_of(st.none(), st.lists(st.sampled_from(["utf-8", "latin-1", "ascii"]), max_size=3)),
        max_size=st.one_of(st.none(), st.integers(min_value=1, max_value=10**9)),
    )
    def test_upload_limits_survive_the_round_trip(self, extensions, encodings, max_size):
        field = snap.SnapFileField(
            upload_to="u/",
            allowed_extensions=extensions,
            allowed_encodings=encodings,
            max_size_bytes=max_size,
        )

        rebuilt = _rebuild(field)

        assert _state(rebuilt) == _state(field)
        assert (
            rebuilt._snap_allowed_extensions,
            rebuilt._snap_allowed_encodings,
            rebuilt._snap_max_size_bytes,
        ) == (extensions, encodings, max_size)

    @given(spec=st.sampled_from(FIELD_SPECS), meta=METADATA_KWARGS, django=DJANGO_KWARGS, required=st.booleans())
    def test_metadata_kwargs_add_no_migration(self, spec, meta, django, required):
        field_cls, base = spec

        with_metadata = field_cls(**base, **django, **meta, required=required)
        without_metadata = field_cls(**base, **django, required=required)

        assert _state(with_metadata) == _state(without_metadata)

    @given(spec=st.sampled_from(FIELD_SPECS), meta=METADATA_KWARGS, required=st.booleans())
    def test_required_decides_nullability_and_survives_the_round_trip(self, spec, meta, required):
        field_cls, base = spec

        field = field_cls(**base, **meta, required=required)
        rebuilt = _rebuild(field)

        assert field.null is (not required)
        # Django's own contract: an auto_now/auto_now_add field is always
        # blank=True (the value is filled in, never typed), whatever is passed.
        assert field.blank is (True if _is_auto_now(base) else not required)
        assert (rebuilt.null, rebuilt.blank) == (field.null, field.blank)

    @given(
        spec=st.sampled_from(FIELD_SPECS),
        required=st.booleans(),
        null=st.booleans(),
        blank=st.booleans(),
    )
    def test_an_explicit_null_or_blank_wins_over_required(self, spec, required, null, blank):
        field_cls, base = spec

        field = field_cls(**base, required=required, null=null, blank=blank)

        expected = (null, True if _is_auto_now(base) else blank)
        assert (field.null, field.blank) == expected
        assert (_rebuild(field).null, _rebuild(field).blank) == expected


#: Plain Django fields ``snap_field()`` is documented to wrap.
PLAIN_FIELDS = st.sampled_from(
    [
        lambda: models.CharField(max_length=30),
        lambda: models.TextField(null=True),
        lambda: models.IntegerField(default=0),
        lambda: models.DecimalField(max_digits=8, decimal_places=3),
        lambda: models.BooleanField(default=False),
        lambda: models.DateTimeField(auto_now_add=True),
        lambda: models.JSONField(default=dict, blank=True),
        lambda: models.CharField(max_length=10, editable=False),
        lambda: models.DateField(auto_now=True),
        lambda: models.TimeField(auto_now_add=True),
    ]
)


class TestSnapFieldWrapperLaws:
    @given(make=PLAIN_FIELDS, meta=METADATA_KWARGS)
    def test_wrapping_sets_every_attribute_and_adds_no_migration(self, make, meta):
        before = _state(make())

        field = snap_field(make(), **meta)

        assert _state(field) == before
        auto_now = getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False)
        for name, value in meta.items():
            if name == "editable" and auto_now:
                # Parity with Django and Snap*Field: an auto_now field is read-only.
                assert field.editable is False
            else:
                assert getattr(field, name) == value

    @given(make=PLAIN_FIELDS, required=st.booleans())
    def test_required_only_ever_tightens(self, make, required):
        original = make()
        null_before, blank_before = original.null, original.blank

        field = snap_field(original, required=required)

        if required:
            assert (field.null, field.blank) == (False, False)
        else:
            assert (field.null, field.blank) == (null_before, blank_before)

    @given(
        make=PLAIN_FIELDS,
        name=st.from_regex(r"\A[a-z_]{1,20}\Z").filter(lambda n: n not in _SNAP_FIELD_WRAPPER_KWARGS),
    )
    def test_an_unknown_kwarg_is_refused_by_name(self, make, name):
        with pytest.raises(ValueError, match=f"unexpected keyword argument '{name}'"):
            snap_field(make(), **{name: True})

    @given(
        make=PLAIN_FIELDS,
        file_kwarg=st.sampled_from(["allowed_extensions", "allowed_encodings", "max_size_bytes"]),
    )
    def test_upload_kwargs_are_refused_on_a_field_that_is_not_a_file(self, make, file_kwarg):
        with pytest.raises(ValueError, match="only apply to a FileField or ImageField"):
            snap_field(make(), **{file_kwarg: ["pdf"]})
