"""
tests/test_boolean_fields.py — a boolean with a default is a checkbox (#EXT3g).

Snap fields derive ``null=True`` from ``required=False``, so
``SnapBooleanField(default=False)`` became a nullable column rendered as a
three-way *Unknown / Yes / No* select — and a browser's checkbox value ``"on"``
parsed to *unknown*. Every boolean in the demo had that shape. Tests never saw it:
Django's test client posts ``"true"``, never what a browser sends.

The column stays as it is (changing ``null`` would be a migration in every
project); the form decides: a defaulted boolean whose ``null`` was only derived is
a checkbox, and a field that asked for ``null=True`` — or has no default — keeps
the three states.

Class guards: every boolean field of every registered model understands the
browser's checkbox encoding unless it is deliberately three-state, and submitting
a generated change form back exactly as a browser would changes nothing.
"""

from __future__ import annotations

import csv
from decimal import Decimal

import pytest
from django import forms
from django.apps import apps
from django.db import models

from snapadmin.fields import SnapBooleanField
from snapadmin.registry import is_registered
from tests.browser_forms import browser_payload, submit_like_a_browser


def _formfield(**kwargs) -> forms.Field:
    field = SnapBooleanField(**kwargs)
    field.name = "flag"
    return field.formfield()


class TestTheWidgetFollowsWhatTheFieldAskedFor:
    @pytest.mark.parametrize("kwargs", [{"default": False}, {"default": True}, {"default": False, "required": True}])
    def test_a_defaulted_boolean_is_a_checkbox(self, kwargs):
        formfield = _formfield(**kwargs)

        assert type(formfield) is forms.BooleanField
        assert isinstance(formfield.widget, forms.CheckboxInput)
        assert formfield.required is False

    @pytest.mark.parametrize("kwargs", [{"default": False, "null": True}, {}, {"default": None}])
    def test_an_explicitly_nullable_or_undefaulted_boolean_keeps_three_states(self, kwargs):
        formfield = _formfield(**kwargs)

        assert type(formfield) is forms.NullBooleanField

    def test_the_column_is_unchanged_so_no_migration_follows(self):
        field = SnapBooleanField(default=False)

        assert field.null is True
        assert field.deconstruct()[3]["null"] is True

    @pytest.mark.parametrize(("posted", "expected"), [({"flag": "on"}, True), ({}, False), ({"flag": "true"}, True)])
    def test_the_browser_encoding_is_read_as_meant(self, posted, expected):
        formfield = _formfield(default=False)

        assert formfield.clean(formfield.widget.value_from_datadict(posted, {}, "flag")) is expected


def _expects_three_states(field: models.BooleanField) -> bool:
    explicit_null = getattr(field, "_snap_null_explicit", True)
    return field.null and (explicit_null or not field.has_default() or field.get_default() is None)


class TestEveryBooleanOfEveryModel:
    """The class guard: a new boolean field — on any registered model, of any
    declaration style — must be drivable by a browser checkbox unless it is
    deliberately three-state."""

    def test_each_one_reads_a_checked_box_as_true_and_an_absent_one_as_false(self):
        failures = []
        checked = 0
        for model in apps.get_models():
            if not is_registered(model):
                continue
            for field in model._meta.concrete_fields:
                if not isinstance(field, models.BooleanField) or _expects_three_states(field):
                    continue
                checked += 1
                formfield = field.formfield()
                widget = formfield.widget
                on = formfield.clean(widget.value_from_datadict({field.name: "on"}, {}, field.name))
                off = formfield.clean(widget.value_from_datadict({}, {}, field.name))
                if (on, off) != (True, False):
                    failures.append(
                        f"review: {model._meta.label}.{field.name} reads a checked box as {on!r} and "
                        f"an unchecked one as {off!r} — a browser cannot drive this widget"
                    )

        assert checked >= 5, f"only {checked} boolean field(s) checked — the guard is not looking"
        assert not failures, "\n".join(failures)


@pytest.mark.django_db
class TestSubmittingAChangeFormUnchanged:
    def test_a_product_change_form_round_trips_and_its_checkbox_toggles(self, client, admin_user):
        from demo.apps.shop.models import Product

        client.force_login(admin_user)
        product = Product.objects.create(name="Lamp", price=Decimal("5.00"), available=True)
        url = f"/admin/demo/product/{product.pk}/change/"

        payload = browser_payload(client.get(url).content.decode(), "product_form")
        assert payload["available"] == ["on"]

        unchanged = submit_like_a_browser(client, url, form_id="product_form")
        assert unchanged.status_code == 302, unchanged.content[:500]
        product.refresh_from_db()
        assert (product.name, product.price, product.available) == ("Lamp", Decimal("5.00"), True)

        unticked = submit_like_a_browser(client, url, {"available": None}, "product_form")
        assert unticked.status_code == 302
        product.refresh_from_db()
        assert product.available is False

        ticked = submit_like_a_browser(client, url, {"available": ["on"]}, "product_form")
        assert ticked.status_code == 302
        product.refresh_from_db()
        assert product.available is True


@pytest.mark.django_db
class TestImportedBooleans:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [("true", True), ("TRUE", True), ("yes", True), ("on", True), ("1", True),
         ("false", False), ("No", False), ("off", False), ("0", False)],
    )
    def test_the_common_spellings_are_read(self, tmp_path, token, expected):
        from demo.apps.shop.models import Category
        from snapadmin.importing import run_import_job, start_import

        path = tmp_path / "in.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["name", "is_active"])
            writer.writeheader()
            writer.writerow({"name": f"Cat {token}", "is_active": token})

        summary = run_import_job(start_import(Category, file_path=str(path)), file_path=str(path))

        assert summary["outcome"] == "finished" and summary["failed"] == 0, summary
        assert Category.objects.get(name=f"Cat {token}").is_active is expected

    def test_a_word_that_means_nothing_is_a_row_error(self, tmp_path):
        from demo.apps.shop.models import Category
        from snapadmin.importing import run_import_job, start_import

        path = tmp_path / "in.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["name", "is_active"])
            writer.writeheader()
            writer.writerow({"name": "Maybe", "is_active": "perhaps"})

        summary = run_import_job(start_import(Category, file_path=str(path)), file_path=str(path))

        assert (summary["created"], summary["failed"]) == (0, 1)
