"""
tests/test_admin_form_relations.py

A relation declared ``show_in_form=True`` reaches the generated change form, and
a reverse relation never does.

Regression cover for a defect the browser suite found (#QA1e) that had been
there since the first commit: the form's field list was built from the same
narrow list as the changelist's columns, which leaves every relation out except
a forward foreign key. So a ``SnapManyToManyField`` could not be edited in the
admin at all, and a ``SnapOneToOneField`` vanished from the form — the demo's
customer profile could only ever be saved without its customer. The relation
list had the opposite hole: built from ``get_fields()``, it took in *reverse*
many-to-many accessors (a ``Tag`` admin listed its products' accessor as an
autocomplete field) while ignoring ``autocomplete=True`` on a one-to-one.
"""

from decimal import Decimal

import pytest
from django.contrib import admin

from snapadmin import checks


class TestTheFormListsEveryForwardField:
    def test_a_many_to_many_is_in_the_form_in_declaration_order(self):
        from demo.apps.shop.models import Product

        assert Product.get_admin_fields().form_fields == [
            "category", "tags", "name", "price", "available", "description",
        ]

    def test_a_one_to_one_is_in_the_form(self):
        from demo.apps.shop.models import CustomerProfile

        assert CustomerProfile.get_admin_fields().form_fields == [
            "customer", "newsletter", "bio", "tax_id",
        ]

    def test_reverse_relations_never_reach_a_form(self):
        """Customer is the far end of a one-to-one (``profile``) and a foreign
        key (``order``); neither accessor is a field its form can edit."""
        from demo.apps.shop.models import Customer

        assert Customer.get_admin_fields().form_fields == [
            "first_name", "last_name", "email", "active",
        ]


class TestAutocompleteFieldsAreForwardRelationsOnly:
    def test_a_reverse_many_to_many_is_not_an_autocomplete_field(self):
        from demo.apps.shop.models import Tag

        assert admin.site._registry[Tag].autocomplete_fields == []

    def test_autocomplete_on_a_one_to_one_is_honoured(self):
        from demo.apps.shop.models import CustomerProfile

        assert admin.site._registry[CustomerProfile].autocomplete_fields == ["customer"]

    def test_autocomplete_on_a_foreign_key_is_unchanged(self):
        from demo.apps.shop.models import Order

        assert admin.site._registry[Order].autocomplete_fields == ["customer"]


@pytest.mark.django_db
class TestTheAdminSavesTheRelations:
    def test_the_tags_picked_in_the_add_form_are_saved(self, admin_client):
        from demo.apps.shop.models import Product, Tag

        sale = Tag.objects.create(name="sale")
        Tag.objects.create(name="clearance")

        response = admin_client.post(
            "/admin/demo/product/add/",
            {"name": "Laptop Pro", "price": "999.00", "available": "true", "tags": [sale.pk]},
        )

        assert response.status_code == 302, response.content.decode()[:2000]
        product = Product.objects.get()
        assert list(product.tags.values_list("name", flat=True)) == ["sale"]
        assert product.price == Decimal("999.00")

    def test_the_customer_picked_in_the_profile_form_is_linked(
        self, admin_client, customer, customer_inactive
    ):
        from demo.apps.shop.models import CustomerProfile

        response = admin_client.post(
            "/admin/demo/customerprofile/add/",
            {"customer": customer_inactive.pk, "bio": "Prefers email.", "tax_id": "DE123"},
        )

        assert response.status_code == 302, response.content.decode()[:2000]
        profile = CustomerProfile.objects.get()
        assert profile.customer_id == customer_inactive.pk
        assert profile.tax_id == "DE123"


class TestTheEmptyFormCheckCountsRelations:
    """``snapadmin.W015`` warns about a generated form with nothing in it. It
    reads the same field list the form does, so a form holding only a relation
    is not empty and must not be reported."""

    def test_a_form_whose_only_field_is_a_relation_is_not_reported(self, monkeypatch):
        from demo.apps.shop.models import CustomerProfile

        for name in ("newsletter", "bio", "tax_id"):
            monkeypatch.setattr(
                CustomerProfile._meta.get_field(name), "show_in_form", False, raising=False
            )

        assert checks.check_empty_admin_forms(None) == []

    def test_the_same_form_without_its_relation_is_reported(self, monkeypatch):
        from demo.apps.shop.models import CustomerProfile

        for name in ("customer", "newsletter", "bio", "tax_id"):
            monkeypatch.setattr(
                CustomerProfile._meta.get_field(name), "show_in_form", False, raising=False
            )

        result = checks.check_empty_admin_forms(None)
        assert [warning.id for warning in result] == ["snapadmin.W015"]
        assert "demo.CustomerProfile" in result[0].msg


class TestRelationsAFormCannotHoldStayOut:
    """Found in review of the fix above: widening the form's field list must not
    take in what Django refuses in a form, or the admin stops booting."""

    def test_a_many_to_many_through_its_own_model_is_left_out(self):
        """Django refuses such a field in fields/fieldsets (admin.E013)."""
        from django.db import models
        from django.test.utils import isolate_apps

        from snapadmin import fields as snap_fields
        from snapadmin.models import SnapModel

        with isolate_apps("demo"):
            class Club(SnapModel):
                name = snap_fields.SnapCharField(max_length=20, show_in_form=True)
                subject_path = None

                class Meta:
                    app_label = "demo"

            class Member(SnapModel):
                name = snap_fields.SnapCharField(max_length=20, show_in_form=True)
                clubs = snap_fields.SnapManyToManyField(
                    Club, through="Membership", show_in_form=True, blank=True
                )
                subject_path = None

                class Meta:
                    app_label = "demo"

            class Membership(models.Model):
                club = models.ForeignKey(Club, on_delete=models.CASCADE)
                member = models.ForeignKey(Member, on_delete=models.CASCADE)

                class Meta:
                    app_label = "demo"

            assert Member.get_admin_fields().form_fields == ["name"]

    def test_a_one_to_one_joins_autocomplete_only_when_it_asks(self):
        """A plain one-to-one never took part before, and would demand a
        searchable admin on its target (admin.E039/E040); the multi-table
        parent link is a one-to-one nobody declared at all."""
        from django.db import models
        from django.test.utils import isolate_apps

        from snapadmin import fields as snap_fields
        from snapadmin.models import SnapModel

        with isolate_apps("demo"):
            class Account(SnapModel):
                name = snap_fields.SnapCharField(max_length=20, show_in_form=True)
                subject_path = None

                class Meta:
                    app_label = "demo"

            class Settings(SnapModel):
                plain = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="+")
                asked = snap_fields.SnapOneToOneField(
                    Account, on_delete=models.CASCADE, related_name="+", autocomplete=True
                )
                subject_path = None

                class Meta:
                    app_label = "demo"

            class PremiumAccount(Account):
                tier = snap_fields.SnapCharField(max_length=10, show_in_form=True)

                class Meta:
                    app_label = "demo"

            assert Settings.get_admin_fields().autocomplete_fields == ["asked"]
            assert PremiumAccount.get_admin_fields().autocomplete_fields == []
