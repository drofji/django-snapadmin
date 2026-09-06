"""
tests/test_critical_path_smoke.py

One end-to-end walk through the declarative pipeline SnapAdmin's own quickstart
promises (the ``snapadmin`` package docstring, README's "3 steps"): declare a
model, get an admin, a REST API and an audit trail, all wired to the same row.

Every layer already has its own deep suite — test_admin_site.py (admin GET views),
test_model_api.py (REST CRUD via APIClient), test_audit_trail.py (save_model()/
delete_model() called directly with a hand-built request/form). Each drives its
layer in isolation, so each can keep passing while the seam *between* layers
quietly breaks: nothing anywhere posts to the real generated admin add form and
checks the row that comes out is the same one the REST API serves and the audit
trail recorded. That is exactly the kind of regression a refactor (human or AI)
can introduce without a single existing test noticing, because no existing test
exercises the chain start to finish.

This file is deliberately small — it is a tripwire for "the pipeline stopped
connecting", not a second copy of the deep per-layer suites.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from snapadmin.models import SnapadminAuditLog


@pytest.mark.django_db
class TestDeclarativePipelineStaysWired:
    """Product (demo/apps/shop/models.py) exercises the full surface: FK, M2M,
    a wysiwyg field, an ES-backed mapping (ES itself is off in tests), and a
    REST-writable field allowlist — the closest thing to "one model, every
    feature" the demo has, which is why it is the target here rather than a
    minimal throwaway model."""

    def test_admin_add_creates_a_row_the_api_serves_and_audit_records(self, admin_user, client):
        from demo.apps.shop.models import Product

        client.force_login(admin_user)

        add_url = reverse("admin:demo_product_add")
        response = client.post(add_url, data={
            "name": "Smoke Test Widget",
            "price": "12.34",
            # available is a nullable BooleanField → Django renders it as a
            # NullBooleanSelect, not a checkbox: "true"/"false"/"unknown".
            "available": "true",
            "description": "<p>Created by the critical-path smoke test.</p>",
            "tags": [],
        })
        assert response.status_code == 302, (
            f"admin add form did not accept the submission: "
            f"{response.context['adminform'].form.errors if response.status_code == 200 else response}"
        )

        product = Product.objects.get(name="Smoke Test Widget")
        assert product.price == Decimal("12.34")
        assert product.available is True

        # The audit trail (admin-layer only, by design — snapadmin/audit.py)
        # recorded the create with the acting user and a diff.
        audit_row = SnapadminAuditLog.objects.get(action="create", object_id=str(product.pk))
        assert audit_row.actor == admin_user
        assert admin_user.username in audit_row.actor_repr
        assert audit_row.changes["name"]["new"] == "Smoke Test Widget"

        # The REST API reads the exact row the admin form wrote — same pk,
        # same field values, no divergence between the two generated surfaces.
        detail_url = reverse("model-detail", args=["demo", "Product", product.pk])
        api_response = client.get(detail_url)
        assert api_response.status_code == 200
        payload = api_response.json()
        assert payload["name"] == "Smoke Test Widget"
        assert Decimal(str(payload["price"])) == Decimal("12.34")

        # The generated changelist still renders with the new row in it —
        # the admin's own list_display/search machinery over live data.
        changelist_url = reverse("admin:demo_product_changelist")
        list_response = client.get(changelist_url)
        assert list_response.status_code == 200
        assert b"Smoke Test Widget" in list_response.content

    def test_admin_edit_updates_the_row_and_audits_the_diff(self, admin_user, client, product):
        client.force_login(admin_user)

        change_url = reverse("admin:demo_product_change", args=[product.pk])
        response = client.post(change_url, data={
            "name": "Renamed By Smoke Test",
            "price": str(product.price),
            "available": "true" if product.available else "false",
            "description": "",
            "tags": [],
        })
        assert response.status_code == 302, (
            f"admin change form did not accept the submission: "
            f"{response.context['adminform'].form.errors if response.status_code == 200 else response}"
        )

        product.refresh_from_db()
        assert product.name == "Renamed By Smoke Test"

        audit_row = SnapadminAuditLog.objects.get(action="update", object_id=str(product.pk))
        assert audit_row.changes["name"]["new"] == "Renamed By Smoke Test"

        detail_url = reverse("model-detail", args=["demo", "Product", product.pk])
        payload = client.get(detail_url).json()
        assert payload["name"] == "Renamed By Smoke Test"

    def test_snapadmin_info_stays_clean_with_live_data(self, admin_user, client, product):
        """The diagnostics command (snapadmin_info) must keep introspecting the
        registry/settings/DB without crashing once real, admin-created and
        API-visible data exists — the same object every assertion above touched."""
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("snapadmin_info", stdout=out)
        text = out.getvalue()
        assert "System checks" in text
        assert "Ok: ✓" in text
