"""
tests/test_audit_object_repr_masking.py

An audit row's ``object_repr`` is ``str(instance)`` at the moment of the change —
for a customer, ``"Smith, Alice"``. On a model with masked fields that label is
the very data the masking hides everywhere else, and it reached every audit
surface raw: the changelist column, the change form, the row's own ``__str__``
(the change page's title and breadcrumb), the admin search, the timeline header
and the SIEM export. A reader allowed the audit log but not the customer's PII
read the name there.

The rule pinned here: on a model with at least one masked field, the label is
shown only to a viewer who may see *every* masked field of that model raw;
anybody else — and every surface with no viewer in hand — gets the neutral
``"<Model> #<pk>"``. A model with no masked field keeps its label, for everyone.

Django's own ``LogEntry`` message is the second copy of a diff: it is stored
text, shown in the object's history page to anyone who may view the object, so
a masked field's values are masked in it at write time, the way encrypted
fields were already redacted there.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.contrib.admin import site
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import RequestFactory, override_settings
from django.urls import reverse

from snapadmin import audit
from snapadmin.masking import (
    PII_PERMISSION,
    hidden_object_repr_models,
    mask_object_repr,
    object_repr_hidden,
)
from snapadmin.models import SnapadminAuditLog

CUSTOMER_EMAIL_MASKED = {"demo.Customer": ["email"]}
CUSTOMER_LABEL = "Smith, Alice"


def _request(user):
    request = RequestFactory().post("/admin/x/", REMOTE_ADDR="203.0.113.5")
    request.user = user
    return request


def _grant(user, app_label: str, codename: str) -> None:
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label=app_label, codename=codename)
    )


@pytest.fixture
def audit_reader(regular_user):
    """Staff with the audit log's view permission and no PII access."""
    regular_user.is_staff = True
    regular_user.save()
    _grant(regular_user, "snapadmin", "view_snapadminauditlog")
    return regular_user


@pytest.fixture
def customer_row(customer, admin_user):
    audit.record_audit(
        _request(admin_user), audit.UPDATE, customer,
        {"active": {"old": True, "new": False}},
    )
    return SnapadminAuditLog.objects.get(model="customer")


@pytest.fixture
def product_row(product, admin_user):
    audit.record_audit(
        _request(admin_user), audit.UPDATE, product,
        {"price": {"old": "1.00", "new": "2.00"}},
    )
    return SnapadminAuditLog.objects.get(model="product")


def _neutral_label(customer) -> str:
    return f"Customer #{customer.pk}"


# ── The rule itself ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMaskObjectRepr:
    def test_a_model_without_masked_fields_keeps_its_label(self, product):
        label = mask_object_repr("demo", "product", str(product.pk), "Widget", user=None)

        assert label == "Widget"

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_no_viewer_in_hand_gets_the_neutral_label(self, customer):
        label = mask_object_repr("demo", "customer", str(customer.pk), CUSTOMER_LABEL)

        assert label == _neutral_label(customer)

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_a_viewer_without_pii_access_gets_the_neutral_label(self, customer, regular_user):
        label = mask_object_repr(
            "demo", "customer", str(customer.pk), CUSTOMER_LABEL, user=regular_user
        )

        assert label == _neutral_label(customer)

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_a_superuser_reads_the_label(self, customer, admin_user):
        label = mask_object_repr(
            "demo", "customer", str(customer.pk), CUSTOMER_LABEL, user=admin_user
        )

        assert label == CUSTOMER_LABEL

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_the_raw_pii_permission_reads_the_label(self, customer, regular_user):
        app_label, codename = PII_PERMISSION.split(".")
        _grant(regular_user, app_label, codename)

        label = mask_object_repr(
            "demo", "customer", str(customer.pk), CUSTOMER_LABEL, user=regular_user
        )

        assert label == CUSTOMER_LABEL

    @override_settings(
        SNAPADMIN_MASKING_RULES={
            "demo.Customer": {
                "email": {"permission": "demo.view_customer"},
                "last_name": {"permission": "demo.change_customer"},
            }
        }
    )
    def test_a_per_field_grant_must_cover_every_masked_field(self, customer, regular_user):
        _grant(regular_user, "demo", "view_customer")
        partial = mask_object_repr(
            "demo", "customer", str(customer.pk), CUSTOMER_LABEL, user=regular_user
        )
        _grant(regular_user, "demo", "change_customer")
        regular_user = type(regular_user).objects.get(pk=regular_user.pk)  # drop the perm cache
        complete = mask_object_repr(
            "demo", "customer", str(customer.pk), CUSTOMER_LABEL, user=regular_user
        )

        assert partial == _neutral_label(customer)
        assert complete == CUSTOMER_LABEL

    @override_settings(SNAPADMIN_MASKED_FIELDS={"gone.Model": ["email"]})
    def test_a_model_no_longer_installed_is_named_by_its_stored_name(self):
        label = mask_object_repr("gone", "model", "7", "Jane Doe")

        assert label == "model #7"

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_a_row_without_an_id_is_named_by_its_model_alone(self):
        label = mask_object_repr("demo", "customer", "", CUSTOMER_LABEL)

        assert label == "Customer"

    @override_settings(
        SNAPADMIN_MASKED_FIELDS={"demo.Customer": ["email"], "gone.Model": ["email"]},
        SNAPADMIN_MASKING_RULES={"no-dot-key": {"email": {"replacement": "x"}}},
    )
    def test_the_hidden_models_include_one_no_longer_installed(self, admin_user):
        # demo.CustomerProfile carries an encrypted field: sensitive by construction.
        assert hidden_object_repr_models() == [
            ("demo", "customer"), ("demo", "customerprofile"), ("gone", "model"),
        ]
        assert hidden_object_repr_models(admin_user) == []

    @override_settings(SNAPADMIN_MASKED_FIELDS=["email"], SNAPADMIN_MASKING_RULES=None)
    def test_a_setting_in_the_wrong_shape_names_no_hidden_model(self):
        # snapadmin.E028 reports the shape; this helper must not raise over it.
        # Only the model with an encrypted field is left.
        assert hidden_object_repr_models() == [("demo", "customerprofile")]

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_the_predicate_agrees_with_the_label(self, admin_user, regular_user):
        assert object_repr_hidden("demo", "customer") is True
        assert object_repr_hidden("demo", "customer", user=regular_user) is True
        assert object_repr_hidden("demo", "customer", user=admin_user) is False
        assert object_repr_hidden("demo", "product") is False


# ── Every audit surface goes through the rule ────────────────────────────────

@pytest.mark.django_db
class TestAuditSurfaces:
    @pytest.fixture(autouse=True)
    def _customer_email_masked(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = CUSTOMER_EMAIL_MASKED

    def test_changelist_hides_the_label_from_a_reader_without_pii_access(
        self, client, audit_reader, customer, customer_row
    ):
        client.force_login(audit_reader)

        body = client.get(reverse("admin:snapadmin_snapadminauditlog_changelist")).content.decode()

        assert CUSTOMER_LABEL not in body
        assert _neutral_label(customer) in body

    def test_changelist_shows_the_label_to_a_superuser(
        self, client, admin_user, customer, customer_row
    ):
        client.force_login(admin_user)

        body = client.get(reverse("admin:snapadmin_snapadminauditlog_changelist")).content.decode()

        assert CUSTOMER_LABEL in body

    def test_changelist_keeps_an_unmasked_models_label(
        self, client, audit_reader, product, product_row
    ):
        client.force_login(audit_reader)

        body = client.get(reverse("admin:snapadmin_snapadminauditlog_changelist")).content.decode()

        assert str(product) in body

    def test_change_form_and_its_title_hide_the_label(
        self, client, audit_reader, customer, customer_row
    ):
        client.force_login(audit_reader)

        response = client.get(
            reverse("admin:snapadmin_snapadminauditlog_change", args=[customer_row.pk])
        )

        body = response.content.decode()
        assert response.status_code == 200
        assert CUSTOMER_LABEL not in body
        assert _neutral_label(customer) in body

    def test_change_form_shows_the_label_to_a_superuser(
        self, client, admin_user, customer_row
    ):
        client.force_login(admin_user)

        body = client.get(
            reverse("admin:snapadmin_snapadminauditlog_change", args=[customer_row.pk])
        ).content.decode()

        assert CUSTOMER_LABEL in body

    def test_the_row_str_never_carries_a_masked_models_label(self, customer, customer_row):
        assert str(customer_row) == f"Updated {_neutral_label(customer)} by testadmin"

    def test_the_row_str_keeps_an_unmasked_models_label(self, product, product_row):
        assert str(product_row) == f"Updated {product} by testadmin"

    def test_search_cannot_confirm_a_hidden_label(
        self, client, audit_reader, customer_row
    ):
        client.force_login(audit_reader)

        response = client.get(
            reverse("admin:snapadmin_snapadminauditlog_changelist"), {"q": "Smith"}
        )

        assert list(response.context["cl"].result_list) == []

    def test_search_still_finds_an_unmasked_models_label(
        self, client, audit_reader, product, product_row, customer_row
    ):
        client.force_login(audit_reader)

        response = client.get(
            reverse("admin:snapadmin_snapadminauditlog_changelist"), {"q": str(product)}
        )

        assert [row.pk for row in response.context["cl"].result_list] == [product_row.pk]

    def test_a_quoted_phrase_finds_an_unmasked_models_label(
        self, client, audit_reader, product, product_row, customer_row
    ):
        client.force_login(audit_reader)

        response = client.get(
            reverse("admin:snapadmin_snapadminauditlog_changelist"), {"q": f'"{product}"'}
        )

        assert [row.pk for row in response.context["cl"].result_list] == [product_row.pk]

    def test_search_by_the_other_fields_still_works_for_a_masked_model(
        self, client, audit_reader, customer, customer_row
    ):
        client.force_login(audit_reader)

        response = client.get(
            reverse("admin:snapadmin_snapadminauditlog_changelist"), {"q": "customer"}
        )

        assert [row.pk for row in response.context["cl"].result_list] == [customer_row.pk]

    def test_search_finds_the_label_for_a_superuser(self, client, admin_user, customer_row):
        client.force_login(admin_user)

        response = client.get(
            reverse("admin:snapadmin_snapadminauditlog_changelist"), {"q": '"Smith, Alice"'}
        )

        assert [row.pk for row in response.context["cl"].result_list] == [customer_row.pk]

    def test_timeline_header_hides_the_label(self, client, audit_reader, customer, customer_row):
        client.force_login(audit_reader)

        response = client.get(
            reverse(
                "admin:snapadmin_snapadminauditlog_timeline",
                args=["demo", "customer", customer.pk],
            )
        )

        assert response.context_data["target_repr"] == _neutral_label(customer)
        assert CUSTOMER_LABEL not in response.content.decode()

    def test_timeline_header_shows_the_label_to_a_superuser(
        self, client, admin_user, customer, customer_row
    ):
        client.force_login(admin_user)

        response = client.get(
            reverse(
                "admin:snapadmin_snapadminauditlog_timeline",
                args=["demo", "customer", customer.pk],
            )
        )

        assert response.context_data["target_repr"] == CUSTOMER_LABEL

    def test_export_hides_the_label_by_default(self, tmp_path, customer, customer_row):
        out = tmp_path / "audit.jsonl"

        call_command("snapadmin_audit_export", "--output", str(out))

        row = json.loads(out.read_text().strip())
        assert row["object_repr"] == _neutral_label(customer)

    def test_csv_export_hides_the_label_by_default(self, tmp_path, customer_row):
        out = tmp_path / "audit.csv"

        call_command("snapadmin_audit_export", "--format", "csv", "--output", str(out))

        assert CUSTOMER_LABEL not in out.read_text()

    def test_export_reveals_the_label_only_on_request(self, tmp_path, customer_row):
        out = tmp_path / "audit.jsonl"

        call_command("snapadmin_audit_export", "--reveal-pii", "--output", str(out))

        row = json.loads(out.read_text().strip())
        assert row["object_repr"] == CUSTOMER_LABEL


# ── Django's LogEntry message ────────────────────────────────────────────────

@pytest.mark.django_db
class TestLogEntryMessage:
    def _change_email(self, admin_user, customer, new_email: str) -> LogEntry:
        from demo.apps.shop.models import Customer

        form = SimpleNamespace(
            cleaned_data={"email": new_email},
            changed_data=["email"],
            initial={"email": customer.email},
        )
        customer.email = new_email
        site._registry[Customer].save_model(_request(admin_user), customer, form, change=True)
        return LogEntry.objects.get(object_id=str(customer.pk))

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_a_masked_fields_values_are_masked_in_the_history_message(
        self, admin_user, customer
    ):
        entry = self._change_email(admin_user, customer, "carol@example.com")

        assert entry.change_message == "Email: 'a***@example.com' -> 'c***@example.com'"

    def test_an_unmasked_fields_values_stay_readable(self, admin_user, customer):
        entry = self._change_email(admin_user, customer, "carol@example.com")

        assert entry.change_message == "Email: 'alice@example.com' -> 'carol@example.com'"

    @override_settings(
        SNAPADMIN_MASKING_RULES={"demo.Customer": {"email": {"replacement": "[hidden]"}}}
    )
    def test_the_fields_own_rule_is_the_one_applied(self, admin_user, customer):
        entry = self._change_email(admin_user, customer, "carol@example.com")

        assert entry.change_message == "Email: '[hidden]' -> '[hidden]'"

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUSTOMER_EMAIL_MASKED)
    def test_the_snapadmin_trail_keeps_the_raw_diff_for_authorised_readers(
        self, admin_user, customer
    ):
        self._change_email(admin_user, customer, "carol@example.com")

        row = SnapadminAuditLog.objects.get(action="update")
        assert row.changes["email"] == {"old": "alice@example.com", "new": "carol@example.com"}
