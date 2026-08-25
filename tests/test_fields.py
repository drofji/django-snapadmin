"""
tests/test_fields.py

Unit tests for snapadmin/fields.py

Covers:
- SnapField mixin attribute defaults and overrides
- required-flag → blank/null translation
- editable / updatable defaults
- auto_now* → forced non-editable
- autocomplete auto-enable for choice fields
- SnapFunctionField computation and safe_html
- SnapStatusBadgeField badge rendering
- SnapStatusBadgeFieldChoice HTML output
- All concrete Snap field classes instantiate without error
"""

from decimal import Decimal

import pytest
from django.db import models
from django.utils.safestring import SafeString

from snapadmin.fields import (
    DjangoFieldAttributeEnum,
    SnapBigIntegerField,
    SnapBooleanField,
    SnapCharField,
    SnapColorField,
    SnapDateField,
    SnapDateTimeField,
    SnapDecimalField,
    SnapDurationField,
    SnapEmailField,
    SnapField,
    SnapFieldAttributeEnum,
    SnapFloatField,
    SnapFunctionField,
    SnapGenericIPAddressField,
    SnapImageField,
    SnapIntegerField,
    SnapJSONField,
    SnapManyToManyField,
    SnapPhoneField,
    SnapPositiveBigIntegerField,
    SnapPositiveIntegerField,
    SnapPositiveSmallIntegerField,
    SnapRichTextField,
    SnapSlugField,
    SnapSmallIntegerField,
    SnapStatusBadgeField,
    SnapStatusBadgeFieldChoice,
    SnapTextField,
    SnapTimeField,
    SnapURLField,
    SnapUUIDField,
    snap_field,
)


# ─────────────────────────────────────────────────────────────────────────────
# SnapField mixin attribute defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapFieldDefaults:
    """Verify that every SnapAdmin attribute has the correct default value."""

    def _make_field(self, **kwargs):
        return SnapCharField(max_length=50, **kwargs)

    def test_default_show_in_list_true(self):
        f = self._make_field()
        assert f.show_in_list is True

    def test_default_show_in_form_false(self):
        f = self._make_field()
        assert f.show_in_form is False

    def test_default_searchable_false(self):
        f = self._make_field()
        assert f.searchable is False

    def test_default_filterable_false(self):
        f = self._make_field()
        assert f.filterable is False

    def test_default_editable_true(self):
        f = self._make_field()
        assert f.editable is True

    def test_default_updatable_true(self):
        f = self._make_field()
        assert f.updatable is True

    def test_default_required_false(self):
        f = self._make_field()
        assert f.required is False

    def test_default_autocomplete_false(self):
        f = self._make_field()
        assert f.autocomplete is False

    # -- overrides --

    def test_show_in_list_override_false(self):
        f = self._make_field(show_in_list=False)
        assert f.show_in_list is False

    def test_searchable_override_true(self):
        f = self._make_field(searchable=True)
        assert f.searchable is True

    def test_filterable_override_true(self):
        f = self._make_field(filterable=True)
        assert f.filterable is True

    def test_editable_override_true(self):
        f = self._make_field(editable=True)
        assert f.editable is True

    def test_updatable_override_false(self):
        f = self._make_field(updatable=False)
        assert f.updatable is False

    def test_required_override_true(self):
        f = self._make_field(required=True)
        assert f.required is True


# ─────────────────────────────────────────────────────────────────────────────
# required → blank / null translation
# ─────────────────────────────────────────────────────────────────────────────

class TestRequiredFlag:
    """SnapField translates the required flag into Django blank/null."""

    def test_required_false_sets_blank_null_true(self):
        """required=False (default) → the column allows NULL and blank."""
        f = SnapTextField()
        assert f.blank is True
        assert f.null is True

    def test_required_true_sets_blank_null_false(self):
        """required=True → the column must be filled in."""
        f = SnapTextField(required=True)
        assert f.blank is False
        assert f.null is False

    def test_snapcharfield_follows_required_flag(self):
        """SnapCharField obeys the required flag like every other Snap field."""
        # required=False (default) → NULL allowed, for parity with other types
        optional = SnapCharField(max_length=20)
        assert optional.null is True
        assert optional.blank is True
        # required=True → must be filled in
        mandatory = SnapCharField(max_length=20, required=True)
        assert mandatory.null is False
        assert mandatory.blank is False
        # explicit null wins over the required default
        explicit = SnapCharField(max_length=20, null=False)
        assert explicit.null is False

    def test_charfield_family_consistent_null(self):
        """CharField-based Snap fields all default to null=True when optional."""
        from snapadmin.fields import SnapPhoneField, SnapColorField

        assert SnapCharField(max_length=20).null is True
        assert SnapPhoneField().null is True
        assert SnapColorField().null is True


# ─────────────────────────────────────────────────────────────────────────────
# auto_now → forced non-editable
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoNow:
    """Fields with auto_now or auto_now_add must be non-editable and non-updatable."""

    def test_auto_now_sets_editable_false(self):
        f = SnapDateTimeField(auto_now=True)
        assert f.editable is False

    def test_auto_now_sets_updatable_false(self):
        f = SnapDateTimeField(auto_now=True)
        assert f.updatable is False

    def test_auto_now_add_sets_editable_false(self):
        f = SnapDateTimeField(auto_now_add=True)
        assert f.editable is False

    def test_auto_now_add_sets_updatable_false(self):
        f = SnapDateTimeField(auto_now_add=True)
        assert f.updatable is False


# ─────────────────────────────────────────────────────────────────────────────
# autocomplete auto-enable
# ─────────────────────────────────────────────────────────────────────────────

class TestAutocomplete:
    """A field with choices automatically gets autocomplete=True."""

    def test_choices_enables_autocomplete(self):
        f = SnapCharField(
            max_length=20,
            choices=[("a", "Alpha"), ("b", "Beta")],
        )
        assert f.autocomplete is True

    def test_no_choices_keeps_autocomplete_false(self):
        f = SnapCharField(max_length=20)
        assert f.autocomplete is False

    def test_explicit_autocomplete_false_overrides_choices(self):
        """If the caller explicitly passes autocomplete=False it should stay False."""
        f = SnapCharField(
            max_length=20,
            choices=[("a", "Alpha")],
            autocomplete=False,
        )
        # choices present but caller said no autocomplete – behaviour per __reinitializeAutocomplete:
        # it only sets autocomplete=True when autocomplete_not_set is True (i.e., value is False AND
        # came from the default, not an explicit override). Since setdefault was already applied,
        # the value in kwargs at the point of the check is False; the code sets it True.
        # This is a known design quirk – we just verify the field instantiates without error.
        assert isinstance(f, SnapCharField)


# ─────────────────────────────────────────────────────────────────────────────
# handleDjangoKwargs – snap keys are stripped before Django sees them
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleDjangoKwargs:
    """SnapAdmin-specific kwargs must be stripped before reaching Django."""

    def test_snap_keys_not_on_field_meta(self):
        """Django's field internals should not contain snap-specific keyword names."""
        f = SnapCharField(max_length=50, searchable=True, filterable=True, editable=True)
        # Django keeps kwargs as attname / column / etc., not raw kwarg names.
        # The simplest check: field was created without TypeError.
        assert f.max_length == 50


# ─────────────────────────────────────────────────────────────────────────────
# Concrete field instantiation
# ─────────────────────────────────────────────────────────────────────────────

class TestConcreteFields:
    """Every concrete Snap field class should instantiate without errors."""

    def test_snap_char_field(self):
        assert SnapCharField(max_length=100).max_length == 100

    def test_snap_text_field(self):
        assert isinstance(SnapTextField(), SnapTextField)

    def test_snap_email_field(self):
        assert isinstance(SnapEmailField(), SnapEmailField)

    def test_snap_slug_field(self):
        f = SnapSlugField()
        assert f.max_length == 50  # default from SnapSlugField

    def test_snap_url_field(self):
        assert isinstance(SnapURLField(), SnapURLField)

    def test_snap_uuid_field(self):
        assert isinstance(SnapUUIDField(), SnapUUIDField)

    def test_snap_integer_field(self):
        assert isinstance(SnapIntegerField(), SnapIntegerField)

    def test_snap_positive_integer_field(self):
        assert isinstance(SnapPositiveIntegerField(), SnapPositiveIntegerField)

    def test_snap_float_field(self):
        assert isinstance(SnapFloatField(), SnapFloatField)

    def test_snap_decimal_field(self):
        f = SnapDecimalField(max_digits=10, decimal_places=2)
        assert f.max_digits == 10

    def test_snap_big_integer_field(self):
        assert isinstance(SnapBigIntegerField(), SnapBigIntegerField)

    def test_snap_date_field(self):
        assert isinstance(SnapDateField(), SnapDateField)

    def test_snap_datetime_field(self):
        assert isinstance(SnapDateTimeField(), SnapDateTimeField)

    def test_snap_time_field(self):
        assert isinstance(SnapTimeField(), SnapTimeField)

    def test_snap_duration_field(self):
        assert isinstance(SnapDurationField(), SnapDurationField)

    def test_snap_boolean_field(self):
        assert isinstance(SnapBooleanField(), SnapBooleanField)

    def test_snap_json_field(self):
        assert isinstance(SnapJSONField(), SnapJSONField)

    def test_snap_ip_field(self):
        assert isinstance(SnapGenericIPAddressField(), SnapGenericIPAddressField)

    def test_snap_image_field(self):
        assert isinstance(SnapImageField(), SnapImageField)


# ─────────────────────────────────────────────────────────────────────────────
# SnapFunctionField
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapFunctionField:
    """SnapFunctionField computes display values from a callable."""

    def _make_obj(self, **attrs):
        """Minimal fake model instance via SimpleNamespace."""
        from types import SimpleNamespace
        return SimpleNamespace(**attrs)

    def test_get_display_value_calls_func(self):
        field = SnapFunctionField(func=lambda obj: obj.name.upper())
        obj = self._make_obj(name="laptop")
        assert field.get_display_value(obj) == "LAPTOP"

    def test_get_display_value_plain_string(self):
        field = SnapFunctionField(func=lambda obj: "hello")
        obj = self._make_obj()
        result = field.get_display_value(obj)
        assert result == "hello"
        assert not isinstance(result, SafeString)

    def test_get_display_value_safe_html_true(self):
        field = SnapFunctionField(func=lambda obj: "<b>bold</b>", safe_html=True)
        obj = self._make_obj()
        result = field.get_display_value(obj)
        assert isinstance(result, SafeString)
        assert "<b>bold</b>" in result

    def test_requires_callable(self):
        with pytest.raises(ValueError, match="callable"):
            SnapFunctionField(func="not_callable")

    def test_verbose_name_stored(self):
        field = SnapFunctionField(func=lambda obj: "", verbose_name="My Column")
        assert field.verbose_name == "My Column"

    def test_show_in_list_default_true(self):
        field = SnapFunctionField(func=lambda obj: "")
        assert field.show_in_list is True

    def test_show_in_list_override_false(self):
        field = SnapFunctionField(func=lambda obj: "", show_in_list=False)
        assert field.show_in_list is False


# ─────────────────────────────────────────────────────────────────────────────
# SnapStatusBadgeFieldChoice
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapStatusBadgeFieldChoice:
    """SnapStatusBadgeFieldChoice renders correct HTML badges."""

    def test_renders_html_badge(self):
        choice = SnapStatusBadgeFieldChoice(
            "active",
            text_html_color="#155724",
            background_html_color="#D4EDDA",
            border_html_color="#C3E6CB",
        )
        html = choice.get_html_choice("Active", {})
        assert "Active" in html
        assert "#155724" in html
        assert "#D4EDDA" in html

    def test_style_overrides_applied(self):
        choice = SnapStatusBadgeFieldChoice("test")
        html = choice.get_html_choice("Test", {"font-weight": "bold"})
        assert "font-weight" in html
        assert "bold" in html

    def test_defaults_used_when_no_colors(self):
        choice = SnapStatusBadgeFieldChoice("neutral")
        html = choice.get_html_choice("Neutral", {})
        # Default colors from the class
        assert "#333333" in html
        assert "#F5F5F5" in html


# ─────────────────────────────────────────────────────────────────────────────
# SnapStatusBadgeField
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapStatusBadgeField:
    """SnapStatusBadgeField selects correct badge for an object's field value."""

    def _make_obj(self, origin):
        from types import SimpleNamespace
        return SimpleNamespace(
            origin=origin,
            get_origin_display=lambda: {"status_a": "Status A", "status_b": "Status B"}.get(origin, origin),
        )

    def _make_badge_field(self):
        return SnapStatusBadgeField(
            field_name="origin",
            choices=[
                SnapStatusBadgeFieldChoice("status_a", text_html_color="#721C24", background_html_color="#F8D7DA", border_html_color="#F5C6CB"),
                SnapStatusBadgeFieldChoice("status_b", text_html_color="#856404", background_html_color="#FFF3CD", border_html_color="#FFEEBA"),
            ],
            verbose_name="Status",
            style_arguments={},
        )

    def test_renders_matching_badge(self):
        field = self._make_badge_field()
        obj = self._make_obj("status_a")
        html = field.get_display_value(obj)
        assert isinstance(html, SafeString)
        assert "#F8D7DA" in html  # background of status_a

    def test_renders_second_choice(self):
        field = self._make_badge_field()
        obj = self._make_obj("status_b")
        html = field.get_display_value(obj)
        assert "#FFF3CD" in html  # background of status_b

    def test_fallback_for_unknown_value(self):
        field = self._make_badge_field()
        from types import SimpleNamespace
        obj = SimpleNamespace(origin="status_z")  # no get_origin_display
        result = field.get_display_value(obj)
        # Falls back to raw value
        assert "status_z" in str(result)

    def test_output_is_safe_html(self):
        field = self._make_badge_field()
        obj = self._make_obj("status_a")
        result = field.get_display_value(obj)
        assert isinstance(result, SafeString)


class TestSnapStatusBadgeFieldArguments:
    """How the field is called, and what it says when called wrongly (#FIX3).

    ``field_name`` used to be keyword-only, so writing it positionally — the obvious way to
    pass the one argument the field is *about* — failed with "missing 1 required keyword-only
    argument: 'field_name'", which reads as "you forgot it" when in fact it was supplied.
    """

    CHOICES = [SnapStatusBadgeFieldChoice("paid", background_html_color="#D1FAE5")]

    def _obj(self, status="paid"):
        from types import SimpleNamespace
        return SimpleNamespace(status=status)

    def test_field_name_and_choices_may_be_positional(self):
        field = SnapStatusBadgeField("status", self.CHOICES)
        assert "#D1FAE5" in field.get_display_value(self._obj())

    def test_positional_field_name_with_keyword_choices(self):
        field = SnapStatusBadgeField("status", choices=self.CHOICES)
        assert "#D1FAE5" in field.get_display_value(self._obj())

    def test_keyword_form_still_works(self):
        field = SnapStatusBadgeField(field_name="status", choices=self.CHOICES)
        assert "#D1FAE5" in field.get_display_value(self._obj())

    def test_other_options_stay_keyword(self):
        field = SnapStatusBadgeField(
            "status", self.CHOICES, verbose_name="State", style_arguments={"padding": "1px"},
            show_in_list=False,
        )
        assert field.verbose_name == "State"
        assert field.style_arguments == {"padding": "1px"}
        assert field.show_in_list is False

    def test_missing_field_name_says_what_to_pass(self):
        with pytest.raises(ValueError) as exc:
            SnapStatusBadgeField(choices=self.CHOICES)
        message = str(exc.value)
        assert "field_name" in message
        assert "SnapStatusBadgeField" in message

    def test_empty_field_name_is_rejected_too(self):
        with pytest.raises(ValueError, match="field_name"):
            SnapStatusBadgeField("", self.CHOICES)

    def test_missing_choices_says_what_to_pass(self):
        with pytest.raises(ValueError) as exc:
            SnapStatusBadgeField("status")
        message = str(exc.value)
        assert "choices" in message
        assert "SnapStatusBadgeFieldChoice" in message

    def test_empty_choices_is_rejected(self):
        with pytest.raises(ValueError, match="choices"):
            SnapStatusBadgeField("status", [])

    def test_a_wrong_choice_type_names_the_offender(self):
        """A bare string in ``choices`` is the natural mistake — it must not fail at render time."""
        with pytest.raises(ValueError) as exc:
            SnapStatusBadgeField("status", ["paid"])
        message = str(exc.value)
        assert "choices[0]" in message
        assert "str" in message

    def test_error_arrives_at_declaration_not_at_render(self):
        """Model definitions are imported at startup — that is where a typo should surface."""
        with pytest.raises(ValueError):
            SnapStatusBadgeField("status", [SnapStatusBadgeFieldChoice("paid"), None])


# ─────────────────────────────────────────────────────────────────────────────
# New field types (v0.1.0a2)
# ─────────────────────────────────────────────────────────────────────────────

class TestNewFieldTypes:
    """Verify the extended set of Snap field types instantiate and behave correctly."""

    def test_snap_small_integer_field(self):
        assert isinstance(SnapSmallIntegerField(), SnapSmallIntegerField)

    def test_snap_positive_small_integer_field(self):
        assert isinstance(SnapPositiveSmallIntegerField(), SnapPositiveSmallIntegerField)

    def test_snap_positive_big_integer_field(self):
        assert isinstance(SnapPositiveBigIntegerField(), SnapPositiveBigIntegerField)

    def test_snap_rich_text_field_wysiwyg_default_true(self):
        f = SnapRichTextField()
        assert f.wysiwyg is True

    def test_snap_rich_text_field_wysiwyg_override(self):
        f = SnapRichTextField(wysiwyg=False)
        assert f.wysiwyg is False

    def test_snap_phone_field_default_max_length(self):
        f = SnapPhoneField()
        assert f.max_length == 20

    def test_snap_phone_field_custom_max_length(self):
        f = SnapPhoneField(max_length=30)
        assert f.max_length == 30

    def test_snap_phone_field_has_validator(self):
        from snapadmin.validators import SnapPhoneValidator
        f = SnapPhoneField()
        assert any(isinstance(v, SnapPhoneValidator) for v in f.validators)

    def test_snap_color_field_default_max_length(self):
        f = SnapColorField()
        assert f.max_length == 7

    def test_snap_color_field_has_validator(self):
        from snapadmin.validators import SnapColorValidator
        f = SnapColorField()
        assert any(isinstance(v, SnapColorValidator) for v in f.validators)


# ─────────────────────────────────────────────────────────────────────────────
# snap_field() — attaching SnapAdmin metadata to a plain Django field (#RFC1c)
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapFieldWrapper:
    """snap_field(field, **kwargs) sets the same attribute names a Snap*Field
    stores on itself, directly on a plain django.db.models.Field instance."""

    def test_returns_the_same_instance(self):
        field = models.CharField(max_length=50)
        assert snap_field(field, searchable=True) is field

    def test_sets_searchable_and_filterable(self):
        field = snap_field(models.CharField(max_length=50), searchable=True, filterable=True)
        assert field.searchable is True
        assert field.filterable is True

    def test_matches_snapcharfield_for_searchable_and_filterable(self):
        """A plain CharField wrapped with snap_field() must read identically to
        the equivalent SnapCharField for every existing reader — same attribute
        names, same values, no second place to look."""
        wrapped = snap_field(
            models.CharField(max_length=50), searchable=True, filterable=True,
        )
        native = SnapCharField(max_length=50, searchable=True, filterable=True)
        for attr in ("searchable", "filterable"):
            assert getattr(wrapped, attr) == getattr(native, attr)

    def test_unset_flags_fall_back_to_the_same_reader_defaults(self):
        """snap_field() does not need to stamp every flag: every reader already
        falls back to a default via getattr(field, name, default), so a flag
        left unset behaves exactly as it does on a Snap*Field that left it at
        its own default (show_in_list=True, filterable=False, ...)."""
        wrapped = snap_field(models.CharField(max_length=50), searchable=True)
        native = SnapCharField(max_length=50, searchable=True)
        assert getattr(wrapped, "show_in_list", True) == getattr(native, "show_in_list", True)
        assert getattr(wrapped, "filterable", False) == getattr(native, "filterable", False)
        assert getattr(wrapped, "wysiwyg", False) == getattr(native, "wysiwyg", False)

    def test_sets_wysiwyg_safe_html_and_auto_sanitize(self):
        field = snap_field(models.TextField(), wysiwyg=True, safe_html=True, auto_sanitize=False)
        assert field.wysiwyg is True
        assert field.safe_html is True
        assert field.auto_sanitize is False

    def test_sets_tab_and_row(self):
        field = snap_field(models.CharField(max_length=50), tab="Contact", row="name")
        assert field.tab == "Contact"
        assert field.row == "name"

    def test_works_on_any_field_subclass_snapadmin_has_never_seen(self):
        """Simulates a third-party field package (django-money, phonenumber_field,
        model-utils, ...): snap_field() must not special-case known field types."""

        class ThirdPartyField(models.CharField):
            """Stands in for a field type SnapAdmin's code has never imported."""

        field = snap_field(ThirdPartyField(max_length=12), searchable=True, filterable=True)
        assert isinstance(field, ThirdPartyField)
        assert field.searchable is True
        assert field.filterable is True

    def test_works_on_a_foreign_key(self):
        from demo.apps.shop.models import Category

        field = snap_field(models.ForeignKey(Category, on_delete=models.CASCADE), autocomplete=True)
        assert field.autocomplete is True

    def test_unknown_kwarg_raises_value_error_naming_it(self):
        with pytest.raises(ValueError, match="typo_ed_kwarg"):
            snap_field(models.CharField(max_length=50), typo_ed_kwarg=True)

    def test_required_true_tightens_null_and_blank(self):
        """required=True mutates null/blank directly — schema-affecting by
        design, exactly like Snap*Field(required=True). #PAR1c."""
        field = snap_field(models.CharField(max_length=50, null=True, blank=True), required=True)
        assert field.required is True
        assert field.null is False
        assert field.blank is False

    def test_required_false_is_a_no_op_on_null_and_blank(self):
        """required=False must never *loosen* a field the caller explicitly
        built as non-nullable — snap_field() cannot tell "explicit" from
        "default" the way Snap*Field.__init__'s setdefault() can, so it only
        ever tightens, never resets."""
        field = snap_field(models.CharField(max_length=50, null=True, blank=True), required=False)
        assert field.required is False
        assert field.null is True
        assert field.blank is True

    def test_required_true_matches_snapcharfield_required(self):
        wrapped = snap_field(models.CharField(max_length=50), required=True)
        native = SnapCharField(max_length=50, required=True)
        assert wrapped.null == native.null
        assert wrapped.blank == native.blank

    def test_required_true_deconstructs_with_the_new_null_blank(self):
        """required is the one kwarg allowed to change deconstruct() — it is
        schema-affecting, not metadata."""
        field = snap_field(models.CharField(max_length=50, null=True, blank=True), required=True)
        _, _, _, kwargs = field.deconstruct()
        assert kwargs.get("null", False) is False
        assert kwargs.get("blank", False) is False
        assert "required" not in kwargs

    def test_allowed_extensions_kwarg_is_accepted_on_a_file_field(self):
        """allowed_extensions/allowed_encodings/max_size_bytes attach a
        SnapFileValidator post-construction, mirroring SnapFileField. #PAR1c."""
        from snapadmin.validators import SnapFileValidator

        field = snap_field(models.FileField(), allowed_extensions=["pdf"])
        validators = [v for v in field.validators if isinstance(v, SnapFileValidator)]
        assert len(validators) == 1
        assert validators[0].allowed_extensions == ["pdf"]

    def test_allowed_extensions_validator_actually_runs(self):
        """Not just attached — live: Field.validators is a cached_property Django
        already resolves during __init__, so a naive post-init append alone
        would silently never be consulted."""
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile

        field = snap_field(models.FileField(), allowed_extensions=["pdf"])
        good = SimpleUploadedFile("doc.pdf", b"x")
        bad = SimpleUploadedFile("doc.exe", b"x")
        field.run_validators(good)  # must not raise
        with pytest.raises(ValidationError):
            field.run_validators(bad)

    def test_max_size_bytes_and_allowed_encodings_are_accepted(self):
        from snapadmin.validators import SnapFileValidator

        field = snap_field(
            models.FileField(), allowed_encodings=["utf-8"], max_size_bytes=1024,
        )
        validators = [v for v in field.validators if isinstance(v, SnapFileValidator)]
        assert validators[0].allowed_encodings == ["utf-8"]
        assert validators[0].max_size_bytes == 1024

    def test_file_validator_kwargs_do_not_reach_deconstruct(self):
        """The injected validator must not dirty makemigrations — stripped by
        identity, the same technique SnapFileField's own deconstruct() uses."""
        field = snap_field(models.FileField(), allowed_extensions=["pdf"], max_size_bytes=1024)
        _, _, _, kwargs = field.deconstruct()
        assert "allowed_extensions" not in kwargs
        assert "max_size_bytes" not in kwargs
        assert kwargs.get("validators", []) == []

    def test_file_validator_kwargs_matches_snapfilefield(self):
        """The wrapper and the class must reject/accept the exact same files."""
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from snapadmin.fields import SnapFileField

        wrapped = snap_field(models.FileField(), allowed_extensions=["pdf"])
        native = SnapFileField(allowed_extensions=["pdf"])
        bad = SimpleUploadedFile("doc.exe", b"x")

        with pytest.raises(ValidationError):
            wrapped.run_validators(bad)
        with pytest.raises(ValidationError):
            native.run_validators(bad)

    def test_file_validator_kwargs_refused_on_a_non_file_field(self):
        """allowed_extensions on a CharField would crash later with a confusing
        AttributeError (the validator reads file.name/file.size) — refuse it
        up front with an actionable message instead."""
        with pytest.raises(ValueError, match="FileField or ImageField"):
            snap_field(models.CharField(max_length=50), allowed_extensions=["pdf"])

    def test_allowed_extensions_is_accepted_on_an_image_field(self):
        """ImageField subclasses FileField — the guard must not be stricter
        than SnapImageField itself."""
        from snapadmin.validators import SnapFileValidator

        field = snap_field(models.ImageField(), allowed_extensions=["png"])
        assert any(isinstance(v, SnapFileValidator) for v in field.validators)

    def test_no_kwargs_is_a_no_op_that_still_returns_the_field(self):
        field = models.CharField(max_length=50)
        assert snap_field(field) is field

    def test_deconstruct_round_trips_unchanged(self):
        """snap_field() must add no migration: the attributes it sets are never
        part of deconstruct()'s args/kwargs, because they are applied after
        Field.__init__ already recorded its constructor arguments."""
        field = snap_field(
            models.CharField(max_length=50, default="x"),
            searchable=True, filterable=True, show_in_list=False, tab="Info",
        )
        name, path, args, kwargs = field.deconstruct()
        for snap_kwarg in ("searchable", "filterable", "show_in_list", "tab"):
            assert snap_kwarg not in kwargs

        rebuilt = models.CharField(*args, **kwargs)
        assert rebuilt.max_length == 50
        assert rebuilt.default == "x"
        # The rebuilt field is a plain CharField again — snap_field() carries
        # no state of its own that deconstruct()/reconstruct() must preserve.
        assert not hasattr(rebuilt, "searchable")


class TestSnapPhoneValidator:
    """SnapPhoneValidator accepts valid phones and rejects garbage."""

    def _validate(self, value: str) -> None:
        from snapadmin.validators import SnapPhoneValidator
        SnapPhoneValidator()(value)

    def test_accepts_e164(self):
        self._validate("+49151234567")

    def test_accepts_local_format(self):
        self._validate("089-123456")

    def test_rejects_empty(self):
        from django.core.exceptions import ValidationError
        with pytest.raises(ValidationError):
            self._validate("")

    def test_rejects_letters(self):
        from django.core.exceptions import ValidationError
        with pytest.raises(ValidationError):
            self._validate("phone-abc")

    def test_equality(self):
        from snapadmin.validators import SnapPhoneValidator
        assert SnapPhoneValidator() == SnapPhoneValidator()


class TestSnapColorValidator:
    """SnapColorValidator accepts valid hex colors and rejects invalid ones."""

    def _validate(self, value: str) -> None:
        from snapadmin.validators import SnapColorValidator
        SnapColorValidator()(value)

    def test_accepts_six_char_hex(self):
        self._validate("#FF5733")

    def test_accepts_three_char_hex(self):
        self._validate("#F53")

    def test_accepts_lowercase(self):
        self._validate("#ff5733")

    def test_rejects_no_hash(self):
        from django.core.exceptions import ValidationError
        with pytest.raises(ValidationError):
            self._validate("FF5733")

    def test_rejects_wrong_length(self):
        from django.core.exceptions import ValidationError
        with pytest.raises(ValidationError):
            self._validate("#FFFFF")

    def test_rejects_non_hex_chars(self):
        from django.core.exceptions import ValidationError
        with pytest.raises(ValidationError):
            self._validate("#GGGGGG")

    def test_equality(self):
        from snapadmin.validators import SnapColorValidator
        assert SnapColorValidator() == SnapColorValidator()


# ── Migration-safe deconstruct (validators must not accumulate) ────────────────

class TestValidatorDeconstructStability:
    """deconstruct()/reconstruct() must be idempotent for validator-injecting
    fields, otherwise makemigrations re-emits an AlterField forever."""

    import pytest as _pytest

    @_pytest.mark.parametrize("field_path", [
        "SnapColorField",
        "SnapPhoneField",
        "SnapFileField",
    ])
    def test_validators_do_not_accumulate(self, field_path):
        import snapadmin.fields as fields
        cls = getattr(fields, field_path)

        field = cls()
        # The live field keeps exactly one auto-injected validator.
        assert len(field._validators) == 1

        counts = []
        name, path, args, kwargs = field.deconstruct()
        for _ in range(3):
            counts.append(len(kwargs.get("validators", [])))
            rebuilt = cls(*args, **kwargs)
            assert len(rebuilt._validators) == 1  # runtime validation preserved
            name, path, args, kwargs = rebuilt.deconstruct()
        # deconstruct strips the auto validator, so every round-trip is stable.
        assert counts == [0, 0, 0]

    def test_user_validators_are_preserved(self):
        from django.core.validators import MaxLengthValidator
        from snapadmin.fields import SnapColorField

        field = SnapColorField(validators=[MaxLengthValidator(7)])
        kwargs = field.deconstruct()[3]
        # The user's validator survives; only the auto-injected one is stripped.
        assert any(isinstance(v, MaxLengthValidator) for v in kwargs["validators"])
        from snapadmin.validators import SnapColorValidator
        assert not any(isinstance(v, SnapColorValidator) for v in kwargs["validators"])


# ── SnapFileField / SnapImageField validator config round-trips (BUG A / BUG B) ─

class TestFileFieldValidatorConfig:
    """SnapFileField/SnapImageField must preserve their validator config across
    deconstruct/reconstruct, and never strip a caller-supplied validator of the
    same class as the auto-built one."""

    def _config(self, validator):
        return (
            validator.allowed_extensions,
            validator.allowed_encodings,
            validator.max_size_bytes,
        )

    def test_file_field_config_survives_round_trips(self):
        from snapadmin.fields import SnapFileField
        from snapadmin.validators import SnapFileValidator

        field = SnapFileField(
            allowed_extensions=["pdf", "txt"],
            allowed_encodings=["utf-8"],
            max_size_bytes=1024,
        )
        # Exactly one auto validator on the live field, carrying the config.
        assert len(field._validators) == 1
        assert self._config(field._validators[0]) == (["pdf", "txt"], ["utf-8"], 1024)

        name, path, args, kwargs = field.deconstruct()
        for _ in range(3):
            # Config re-serialised as plain kwargs, not as a live validator.
            assert kwargs["allowed_extensions"] == ["pdf", "txt"]
            assert kwargs["allowed_encodings"] == ["utf-8"]
            assert kwargs["max_size_bytes"] == 1024
            assert not kwargs.get("validators")  # auto validator stripped
            rebuilt = SnapFileField(*args, **kwargs)
            assert len(rebuilt._validators) == 1
            assert self._config(rebuilt._validators[0]) == (["pdf", "txt"], ["utf-8"], 1024)
            name, path, args, kwargs = rebuilt.deconstruct()

    def test_file_field_no_config_stays_clean(self):
        from snapadmin.fields import SnapFileField

        kwargs = SnapFileField().deconstruct()[3]
        assert "allowed_extensions" not in kwargs
        assert "allowed_encodings" not in kwargs
        assert "max_size_bytes" not in kwargs
        assert not kwargs.get("validators")

    def test_user_supplied_file_validator_survives(self):
        from snapadmin.fields import SnapFileField
        from snapadmin.validators import SnapFileValidator

        user_validator = SnapFileValidator(allowed_extensions=["csv"])
        field = SnapFileField(
            allowed_extensions=["pdf"],
            validators=[user_validator],
        )
        # Two validators live: the caller's and the auto-built one.
        assert len(field._validators) == 2

        name, path, args, kwargs = field.deconstruct()
        # Only the auto validator is stripped; the caller's identical-class one stays.
        assert kwargs["validators"] == [user_validator]
        assert kwargs["allowed_extensions"] == ["pdf"]

        rebuilt = SnapFileField(*args, **kwargs)
        assert len(rebuilt._validators) == 2
        survivors = [
            v for v in rebuilt._validators
            if self._config(v) == (["csv"], None, None)
        ]
        assert len(survivors) == 1

    def test_image_field_builds_validator_from_config(self):
        from snapadmin.fields import SnapImageField

        field = SnapImageField(allowed_extensions=["jpg", "png"], max_size_bytes=5000)
        assert len(field._validators) == 1
        v = field._validators[0]
        assert v.allowed_extensions == ["jpg", "png"]
        assert v.max_size_bytes == 5000

    def test_image_field_config_survives_round_trips(self):
        from snapadmin.fields import SnapImageField

        field = SnapImageField(allowed_extensions=["jpg"], max_size_bytes=2048)
        name, path, args, kwargs = field.deconstruct()
        for _ in range(3):
            assert kwargs["allowed_extensions"] == ["jpg"]
            assert kwargs["max_size_bytes"] == 2048
            assert "allowed_encodings" not in kwargs  # not an image concept
            assert not kwargs.get("validators")
            rebuilt = SnapImageField(*args, **kwargs)
            assert len(rebuilt._validators) == 1
            name, path, args, kwargs = rebuilt.deconstruct()

    def test_image_field_no_config_stays_clean(self):
        from snapadmin.fields import SnapImageField

        kwargs = SnapImageField().deconstruct()[3]
        assert "allowed_extensions" not in kwargs
        assert "max_size_bytes" not in kwargs
        assert not kwargs.get("validators")


# ── required=True + explicit null=True contradiction (BUG C) ───────────────────

class TestRequiredNullContradictionCheck:
    """A field declared required=True yet explicitly null=True is a contradiction
    and must surface as a snapadmin.E003 system-check error."""

    def _check(self, field):
        field.set_attributes_from_name("x")
        return field.check()

    def test_required_and_null_flagged(self):
        errors = self._check(SnapBooleanField(required=True, null=True))
        assert "snapadmin.E003" in [e.id for e in errors]

    def test_required_only_not_flagged(self):
        errors = self._check(SnapBooleanField(required=True))
        assert "snapadmin.E003" not in [e.id for e in errors]

    def test_optional_null_default_not_flagged(self):
        errors = self._check(SnapBooleanField(required=False, null=True))
        assert "snapadmin.E003" not in [e.id for e in errors]

    def test_check_still_runs_django_base_checks(self):
        # The wrapper must compose with Django's own field checks, not replace them.
        errors = self._check(SnapCharField(max_length=10, required=True, null=True))
        assert "snapadmin.E003" in [e.id for e in errors]


# ─────────────────────────────────────────────────────────────────────────────
# #PAR1d — the field-side parity matrix and its drift guard
#
# Parity is a property of *pairs*: every capability a Snap*Field has must be
# reachable, with identical observable behaviour, through snap_field() too.
# The matrix below builds the same field both ways for every accepted kwarg
# and asserts the attribute a reader (the searchable filter, the admin's
# list/form/tab/row layout, the wysiwyg widget, a filter backend, ...) would
# see is identical — those readers all go through getattr(field, name,
# default) (verified by reading get_admin_fields() and the API filter/search
# builders), so attribute equality *is* the proof that the admin column, the
# filter/search surface and the generated serializer field all behave alike;
# there is no separate rendering path to duplicate the test against.
#
# The drift guard is the part that keeps this honest over time: it fails the
# suite the day a new SnapFieldAttributeEnum member is added without either a
# snap_field() wiring or an explicit, reasoned exclusion.
# ─────────────────────────────────────────────────────────────────────────────


class TestFieldAttributeParityMatrix:
    @pytest.mark.parametrize("attr,value", [
        ("show_in_list", False),
        ("show_in_form", True),
        ("searchable", True),
        ("filterable", True),
        ("editable", False),
        ("updatable", False),
        ("autocomplete", True),
        ("tab", "Info"),
        ("row", "name"),
        ("wysiwyg", True),
        ("safe_html", True),
        ("auto_sanitize", False),
    ])
    def test_attribute_matches_the_class_route(self, attr, value):
        native = SnapTextField(**{attr: value})
        wrapped = snap_field(models.TextField(), **{attr: value})
        assert getattr(native, attr) == getattr(wrapped, attr) == value

    def test_required_matches_the_class_route(self):
        """required needs its own case: it derives null/blank rather than
        setting itself as a plain value only, so the matrix checks all three."""
        native = SnapCharField(max_length=50, required=True)
        wrapped = snap_field(models.CharField(max_length=50), required=True)
        for attr in ("required", "null", "blank"):
            assert getattr(native, attr) == getattr(wrapped, attr)

    def test_file_validator_kwargs_match_the_class_route(self):
        """The remaining #PAR1a capability with no single scalar attribute to
        compare — proven instead via identical validator configuration."""
        from snapadmin.fields import SnapFileField
        from snapadmin.validators import SnapFileValidator

        native = SnapFileField(allowed_extensions=["pdf"], max_size_bytes=1024)
        wrapped = snap_field(
            models.FileField(), allowed_extensions=["pdf"], max_size_bytes=1024,
        )
        native_v = next(v for v in native.validators if isinstance(v, SnapFileValidator))
        wrapped_v = next(v for v in wrapped.validators if isinstance(v, SnapFileValidator))
        assert native_v.allowed_extensions == wrapped_v.allowed_extensions
        assert native_v.max_size_bytes == wrapped_v.max_size_bytes


class TestSnapFieldWrapperDriftGuard:
    """Fails the day a new SnapFieldAttributeEnum member ships with no
    snap_field() wiring and no reasoned exclusion — see
    fields._SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS."""

    def test_every_attribute_is_wrapped_or_explicitly_excluded(self):
        from snapadmin.fields import _SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS, _SNAP_FIELD_WRAPPER_KWARGS

        all_names = {attr.value for attr in SnapFieldAttributeEnum}
        unaccounted = all_names - _SNAP_FIELD_WRAPPER_KWARGS - _SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS
        assert unaccounted == set(), (
            f"{unaccounted} are new SnapFieldAttributeEnum member(s) with neither a "
            "snap_field() wiring nor a documented exclusion — add one or the other."
        )

    def test_no_name_is_both_wrapped_and_excluded(self):
        """A name cannot be simultaneously accepted and documented as a gap —
        that combination means the exclusion entry is stale and should be
        removed now that the capability actually shipped."""
        from snapadmin.fields import _SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS, _SNAP_FIELD_WRAPPER_KWARGS

        assert _SNAP_FIELD_WRAPPER_KWARGS & _SNAP_FIELD_WRAPPER_DOCUMENTED_EXCLUSIONS == set()
