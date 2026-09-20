"""
tests/test_masking_hostile_settings.py

Masking settings written in the wrong shape (#QA1d, part 4 — hostile settings).

``SNAPADMIN_MASKED_FIELDS`` and ``SNAPADMIN_MASKING_RULES`` are hand-written
dicts, and a shape mistake in either is a PII decision, so two things must hold
for every shape, not only the documented one:

* **Nothing crashes.** Before #QA1d a number where a list belongs raised
  ``TypeError`` from every masked request *and* from ``manage.py check`` itself,
  so the check meant to report the mistake could not run.
* **Masking fails closed where intent is readable, and the check says so.** A
  bare string names one field (it was split into characters, masking nothing);
  a list under ``SNAPADMIN_MASKING_RULES`` names fields to mask with the
  built-in masker (it crashed). Where no field can be read out at all, nothing
  can be masked — and ``snapadmin.E028`` blocks the deploy instead of letting it
  go out silently unmasked.
"""

from __future__ import annotations

import pytest
from django.core import checks

from snapadmin.masking import get_masked_fields, get_masking_rules, mask_field


def _ids(messages) -> list[str]:
    return sorted(m.id for m in messages if m.id.startswith("snapadmin."))


def _masking_errors() -> list:
    from snapadmin.checks import check_masked_fields, check_masking_rules

    return check_masked_fields(None) + check_masking_rules(None)


@pytest.mark.django_db
class TestMaskedFieldsShapes:
    def test_a_bare_string_masks_that_one_field(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": "email"}

        assert get_masked_fields("demo", "customer") == ["email"]

    def test_a_bare_string_is_reported_once_as_the_wrong_shape(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": "email"}

        errors = _masking_errors()

        assert _ids(errors) == ["snapadmin.E028"]
        assert "SNAPADMIN_MASKED_FIELDS['demo.Customer'] must be a list of field names, got str" in errors[0].msg
        # The hint carries the fix — the shape both settings want, and what is
        # masked meanwhile. A mutation run found nothing noticed it going missing.
        assert "SNAPADMIN_MASKED_FIELDS = {'app.Model': ['field', ...]}" in errors[0].hint
        assert "SNAPADMIN_MASKING_RULES" in errors[0].hint

    @pytest.mark.parametrize("hostile", [5, 3.5, True, object()])
    def test_a_non_iterable_masks_nothing_crashes_nothing_and_blocks_the_deploy(self, settings, hostile):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": hostile}

        assert get_masked_fields("demo", "customer") == []
        errors = _masking_errors()
        assert _ids(errors) == ["snapadmin.E028"]
        assert errors[0].level == checks.ERROR

    def test_non_string_names_in_a_list_are_reported_and_skipped(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": ["email", 5, None]}

        assert get_masked_fields("demo", "customer") == ["email"]
        assert _ids(_masking_errors()) == ["snapadmin.E028", "snapadmin.E028"]

    @pytest.mark.parametrize("hostile", [["demo.Customer"], "demo.Customer", 5])
    def test_a_top_level_value_that_is_not_a_dict_is_reported_not_raised(self, settings, hostile):
        settings.SNAPADMIN_MASKED_FIELDS = hostile

        assert get_masked_fields("demo", "customer") == []
        errors = _masking_errors()
        assert _ids(errors) == ["snapadmin.E028"]
        assert "SNAPADMIN_MASKED_FIELDS must be a dict" in errors[0].msg

    def test_none_means_no_fields_and_is_not_a_shape_error(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": None}

        assert get_masked_fields("demo", "customer") == []
        assert _ids(_masking_errors()) == []

    def test_the_documented_shape_raises_no_e028(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": ["email", "phone"]}

        assert get_masked_fields("demo", "customer") == ["email", "phone"]
        assert "snapadmin.E028" not in _ids(_masking_errors())


@pytest.mark.django_db
class TestMaskingRulesShapes:
    def test_a_list_of_names_masks_them_with_the_built_in_masker(self, settings):
        settings.SNAPADMIN_MASKING_RULES = {"demo.Customer": ["email"]}

        assert get_masking_rules("demo", "customer") == {"email": {}}
        assert get_masked_fields("demo", "customer") == ["email"]
        assert mask_field("demo", "customer", "email", "alice@example.com") == "a***@example.com"

    def test_a_list_of_names_is_reported_as_the_wrong_shape(self, settings):
        settings.SNAPADMIN_MASKING_RULES = {"demo.Customer": ["email"]}

        errors = _masking_errors()

        assert _ids(errors) == ["snapadmin.E028"]
        assert "SNAPADMIN_MASKING_RULES['demo.Customer'] must be a dict of field -> rule, got list" in errors[0].msg

    @pytest.mark.parametrize("hostile", [5, None, 2.0])
    def test_a_non_iterable_rule_set_masks_nothing_and_is_reported(self, settings, hostile):
        settings.SNAPADMIN_MASKING_RULES = {"demo.Customer": hostile}

        assert get_masking_rules("demo", "customer") == {}
        assert _ids(_masking_errors()) == (["snapadmin.E028"] if hostile is not None else [])

    @pytest.mark.parametrize("hostile", [["demo.Customer"], "demo.Customer"])
    def test_a_top_level_value_that_is_not_a_dict_is_reported_not_raised(self, settings, hostile):
        settings.SNAPADMIN_MASKING_RULES = hostile

        assert get_masking_rules("demo", "customer") == {}
        errors = _masking_errors()
        assert _ids(errors) == ["snapadmin.E028"]
        assert "SNAPADMIN_MASKING_RULES must be a dict" in errors[0].msg

    def test_the_full_check_run_survives_every_hostile_shape(self, settings):
        """The regression that mattered most: ``manage.py check`` itself raised."""
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": 5}
        settings.SNAPADMIN_MASKING_RULES = {"demo.CustomerProfile": ["bio"]}

        ids = _ids(checks.run_checks())

        assert ids.count("snapadmin.E028") == 2
