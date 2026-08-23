"""
tests/test_audit_trail.py — unalterable audit trail (issue #7)

SnapadminAuditLog records every admin create/update/delete (who/what/when +
diff), is immutable at the ORM level, has a read-only admin, and exports for a
SIEM via `snapadmin_audit_export`.
"""

import json
from types import SimpleNamespace

import pytest
from django.contrib.admin import site
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, override_settings
from django.utils import timezone

from snapadmin import audit
from snapadmin.models import SnapadminAuditLog


def _request(user, ip="203.0.113.5", ua="pytest-UA", xff=None):
    extra = {"REMOTE_ADDR": ip, "HTTP_USER_AGENT": ua}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    req = RequestFactory().post("/admin/x/", **extra)
    req.user = user
    return req


# ── Model immutability ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestImmutability:
    def _row(self):
        return SnapadminAuditLog.objects.create(action="create", actor_repr="a", object_repr="o")

    def test_create_allowed(self):
        assert self._row().pk is not None

    def test_update_rejected(self):
        row = self._row()
        row.object_repr = "tampered"
        with pytest.raises(ValidationError):
            row.save()

    def test_instance_delete_rejected(self):
        row = self._row()
        with pytest.raises(ValidationError):
            row.delete()

    def test_queryset_delete_allowed_for_retention(self):
        self._row()
        deleted, _ = SnapadminAuditLog.objects.all().delete()  # bypass for purge
        assert deleted == 1

    def test_str(self):
        row = self._row()
        assert "Created" in str(row) and "o" in str(row)


# ── Capture helpers ──────────────────────────────────────────────────────────

class TestHelpers:
    def test_client_ip_remote_addr(self):
        assert audit.client_ip(_request(AnonymousUser())) == "203.0.113.5"

    def test_client_ip_forwarded_first_hop(self):
        req = _request(AnonymousUser(), xff="70.1.2.3, 10.0.0.1")
        assert audit.client_ip(req) == "70.1.2.3"

    def test_client_ip_none_request(self):
        assert audit.client_ip(None) is None

    def test_client_ip_empty_forwarded_falls_back(self):
        req = _request(AnonymousUser(), xff="")
        assert audit.client_ip(req) == "203.0.113.5"

    def test_user_agent(self):
        assert audit.user_agent(_request(AnonymousUser())) == "pytest-UA"

    def test_user_agent_none_request(self):
        assert audit.user_agent(None) == ""

    def test_format_value_keeps_json_native_types(self):
        assert audit.format_value(None) is None
        assert audit.format_value(42) == 42            # not the string "42"
        assert audit.format_value(True) is True
        assert audit.format_value(1.5) == 1.5
        assert audit.format_value("42") == "42"

    def test_format_value_stringifies_the_rest(self):
        from decimal import Decimal
        assert audit.format_value(Decimal("1.20")) == "1.20"
        assert audit.format_value(float("inf")) == "inf"   # no JSON literal
        assert audit.format_value(float("nan")) == "nan"

    def test_display_value(self):
        assert audit.display_value(None) == "\u2014"
        assert audit.display_value(True) == "true"
        assert audit.display_value(False) == "false"
        assert audit.display_value({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
        assert audit.display_value([1, "x"]) == '[1, "x"]'
        assert audit.display_value(7) == "7"

    def test_diff_rows(self):
        rows = audit.diff_rows({"b": {"old": 1, "new": 2}, "a": {"old": None, "new": "x"}})
        assert [r["field"] for r in rows] == ["a", "b"]     # sorted by field name
        assert rows[0] == {"field": "a", "old": "\u2014", "new": "x", "changed": True}
        assert rows[1] == {"field": "b", "old": "1", "new": "2", "changed": True}

    def test_diff_rows_marks_unchanged_side(self):
        rows = audit.diff_rows({"a": {"old": "same", "new": "same"}})
        assert rows[0]["changed"] is False

    def test_diff_rows_tolerates_a_malformed_entry(self):
        # A hand-written row whose value is not an old/new mapping still renders.
        rows = audit.diff_rows({"a": "just a value", "b": {"new": 2}})
        assert rows[0] == {"field": "a", "old": "\u2014", "new": "just a value", "changed": True}
        assert rows[1] == {"field": "b", "old": "\u2014", "new": "2", "changed": True}

    def test_diff_rows_empty(self):
        assert audit.diff_rows(None) == []
        assert audit.diff_rows({}) == []

    def test_audit_enabled_default(self):
        assert audit.audit_enabled() is True

    @override_settings(SNAPADMIN_AUDIT_LOG_ENABLED=False)
    def test_audit_disabled(self):
        assert audit.audit_enabled() is False


# ── record_audit() ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRecordAudit:
    def test_records_full_row(self, product, admin_user):
        req = _request(admin_user)
        audit.record_audit(req, audit.UPDATE, product, {"name": {"old": "x", "new": "y"}})
        row = SnapadminAuditLog.objects.get()
        assert row.action == "update"
        assert row.actor_id == admin_user.id
        assert row.actor_repr == str(admin_user)
        assert row.ip_address == "203.0.113.5"
        assert row.user_agent == "pytest-UA"
        assert row.app_label == "demo" and row.model == "product"
        assert row.object_id == str(product.pk)
        assert row.object_repr == str(product)
        assert row.changes == {"name": {"old": "x", "new": "y"}}

    def test_anonymous_actor(self, product):
        audit.record_audit(_request(AnonymousUser()), audit.DELETE, product, None)
        row = SnapadminAuditLog.objects.get()
        assert row.actor_id is None
        assert row.actor_repr == "anonymous"

    @override_settings(SNAPADMIN_AUDIT_LOG_ENABLED=False)
    def test_disabled_writes_nothing(self, product, admin_user):
        audit.record_audit(_request(admin_user), audit.CREATE, product, None)
        assert SnapadminAuditLog.objects.count() == 0

    def test_failure_is_swallowed(self, product, admin_user, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(SnapadminAuditLog.objects, "create", boom)
        # Must not raise, and must not create a row.
        audit.record_audit(_request(admin_user), audit.CREATE, product, None)
        assert SnapadminAuditLog.objects.count() == 0


# ── Admin capture via SnapSaveMixin ──────────────────────────────────────────

@pytest.mark.django_db
class TestAdminCapture:
    def _product_admin(self):
        from demo.apps.shop.models import Product
        return site._registry[Product]

    def test_create_audited(self, admin_user):
        from demo.apps.shop.models import Product
        obj = Product(name="Fresh", price=3)
        form = SimpleNamespace(cleaned_data={"name": "Fresh", "price": 3}, changed_data=[], initial={})
        self._product_admin().save_model(_request(admin_user), obj, form, change=False)
        row = SnapadminAuditLog.objects.get(action="create")
        assert row.object_repr == str(obj)
        assert row.changes["name"] == {"old": None, "new": "Fresh"}

    def test_update_audited_with_diff(self, admin_user, product):
        form = SimpleNamespace(
            cleaned_data={"name": "Renamed"}, changed_data=["name"], initial={"name": product.name},
        )
        self._product_admin().save_model(_request(admin_user), product, form, change=True)
        row = SnapadminAuditLog.objects.get(action="update")
        assert row.changes["name"] == {"old": product.name, "new": "Renamed"}

    def test_update_without_real_change_writes_no_audit(self, admin_user, product):
        # changed_data lists a field but the value is unchanged → no diff → no row.
        form = SimpleNamespace(
            cleaned_data={"name": product.name}, changed_data=["name"], initial={"name": product.name},
        )
        self._product_admin().save_model(_request(admin_user), product, form, change=True)
        assert SnapadminAuditLog.objects.filter(action="update").count() == 0

    def test_delete_model_audited(self, admin_user, product):
        self._product_admin().delete_model(_request(admin_user), product)
        assert SnapadminAuditLog.objects.filter(action="delete").count() == 1

    def test_delete_queryset_audited(self, admin_user, many_products):
        from demo.apps.shop.models import Product
        self._product_admin().delete_queryset(_request(admin_user), Product.objects.all())
        assert SnapadminAuditLog.objects.filter(action="delete").count() == len(many_products)


# ── Read-only admin ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReadOnlyAdmin:
    def test_registered(self):
        assert SnapadminAuditLog in site._registry

    def test_permissions_all_false(self, admin_user):
        model_admin = site._registry[SnapadminAuditLog]
        req = _request(admin_user)
        assert model_admin.has_add_permission(req) is False
        assert model_admin.has_change_permission(req) is False
        assert model_admin.has_delete_permission(req) is False

    def test_changelist_renders(self, admin_user, client, product):
        audit.record_audit(_request(admin_user), audit.CREATE, product, None)
        client.force_login(admin_user)
        from django.urls import reverse
        r = client.get(reverse("admin:snapadmin_snapadminauditlog_changelist"))
        assert r.status_code == 200


# ── PII masking (#SEC6) ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuditMasking:
    CUST = {"demo.Customer": ["email"]}

    def _seed(self, customer, admin_user):
        audit.record_audit(
            _request(admin_user), audit.UPDATE, customer,
            {
                "email": {"old": "old@example.com", "new": "new@example.com"},
                "last_name": {"old": "A", "new": "B"},
            },
        )
        return SnapadminAuditLog.objects.get()

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_masked_changes_masks_only_configured_fields(self, customer, admin_user):
        row = self._seed(customer, admin_user)
        model_admin = site._registry[SnapadminAuditLog]
        html = model_admin.masked_changes(row)
        assert "o***@example.com" in html and "n***@example.com" in html
        assert "old@example.com" not in html and "new@example.com" not in html
        assert ">A<" in html and ">B<" in html  # last_name is not masked

    def test_masked_changes_noop_when_unconfigured(self, customer, admin_user):
        row = self._seed(customer, admin_user)
        model_admin = site._registry[SnapadminAuditLog]
        html = model_admin.masked_changes(row)
        assert "old@example.com" in html and "new@example.com" in html

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_changes_diff_renders_raw_values(self, customer, admin_user):
        row = self._seed(customer, admin_user)
        model_admin = site._registry[SnapadminAuditLog]
        html = model_admin.changes_diff(row)
        assert "old@example.com" in html and "new@example.com" in html

    def test_rendered_diff_escapes_stored_markup(self, customer, admin_user):
        # An audit row holds whatever was typed into the admin — it must reach
        # the page escaped, never as live markup.
        audit.record_audit(
            _request(admin_user), audit.UPDATE, customer,
            {"last_name": {"old": "A", "new": "<img src=x onerror=alert(1)>"}},
        )
        row = SnapadminAuditLog.objects.first()
        html = site._registry[SnapadminAuditLog].changes_diff(row)
        assert "<img src=x" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html

    def test_rendered_diff_without_changes(self, product, admin_user):
        audit.record_audit(_request(admin_user), audit.DELETE, product, None)
        row = SnapadminAuditLog.objects.get()
        assert "No field-level diff" in site._registry[SnapadminAuditLog].changes_diff(row)

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_readonly_fields_swap_for_unprivileged(self, customer, regular_user, admin_user):
        self._seed(customer, admin_user)
        model_admin = site._registry[SnapadminAuditLog]
        fields = model_admin.get_readonly_fields(_request(regular_user))
        assert "changes" not in fields
        assert "masked_changes" in fields

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_readonly_fields_raw_for_privileged(self, customer, admin_user):
        self._seed(customer, admin_user)
        model_admin = site._registry[SnapadminAuditLog]
        fields = model_admin.get_readonly_fields(_request(admin_user))
        assert "changes_diff" in fields
        assert "masked_changes" not in fields

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_change_form_renders_the_masked_diff(self, customer, regular_user, admin_user, client):
        from django.contrib.auth.models import Permission
        from django.urls import reverse

        row = self._seed(customer, admin_user)
        regular_user.is_staff = True
        regular_user.save()
        regular_user.user_permissions.add(Permission.objects.get(
            content_type__app_label="snapadmin", codename="view_snapadminauditlog",
        ))
        client.force_login(regular_user)
        r = client.get(reverse("admin:snapadmin_snapadminauditlog_change", args=[row.pk]))
        assert r.status_code == 200
        body = r.content.decode()
        assert "o***@example.com" in body
        assert "old@example.com" not in body


# ── Per-object diff timeline (#PROP3c) ───────────────────────────────────────

@pytest.mark.django_db
class TestTimelineView:
    def _url(self, obj):
        from django.urls import reverse
        return reverse(
            "admin:snapadmin_snapadminauditlog_timeline",
            args=[obj._meta.app_label, obj._meta.model_name, obj.pk],
        )

    def _seed(self, product, admin_user, count=2):
        for i in range(count):
            audit.record_audit(
                _request(admin_user), audit.UPDATE, product,
                {"name": {"old": f"name {i}", "new": f"name {i + 1}"}},
            )

    def test_renders_every_entry_newest_first(self, product, admin_user, client):
        self._seed(product, admin_user)
        client.force_login(admin_user)
        r = client.get(self._url(product))
        assert r.status_code == 200
        body = r.content.decode()
        assert "name 0" in body and "name 2" in body
        assert [e["entry"].pk for e in r.context_data["entries"]] == \
            list(SnapadminAuditLog.objects.order_by("-timestamp").values_list("pk", flat=True))
        assert r.context_data["total"] == 2
        assert r.context_data["truncated"] is False

    def test_empty_history_renders(self, product, admin_user, client):
        client.force_login(admin_user)
        r = client.get(self._url(product))
        assert r.status_code == 200
        assert r.context_data["entries"] == []
        assert "No audit entries" in r.content.decode()

    def test_caps_the_number_of_entries(self, product, admin_user, client, monkeypatch):
        self._seed(product, admin_user, count=3)
        model_admin = site._registry[SnapadminAuditLog]
        monkeypatch.setattr(model_admin, "timeline_max_entries", 2, raising=False)
        client.force_login(admin_user)
        r = client.get(self._url(product))
        assert len(r.context_data["entries"]) == 2
        assert r.context_data["total"] == 3
        assert r.context_data["truncated"] is True

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_masks_pii_for_unprivileged_viewer(self, product, admin_user, regular_user, client):
        from django.contrib.auth.models import Permission

        self._seed(product, admin_user, count=1)
        regular_user.is_staff = True
        regular_user.save()
        regular_user.user_permissions.add(Permission.objects.get(
            content_type__app_label="snapadmin", codename="view_snapadminauditlog",
        ))
        client.force_login(regular_user)
        body = client.get(self._url(product)).content.decode()
        assert "name 0" not in body and "name 1" not in body
        assert "na**" in body

    def test_requires_view_permission(self, product, admin_user, regular_user, client):
        self._seed(product, admin_user, count=1)
        regular_user.is_staff = True   # staff, but no audit-log view permission
        regular_user.save()
        client.force_login(regular_user)
        assert client.get(self._url(product)).status_code == 403

    def test_url_is_not_shadowed_by_the_change_view(self, product, admin_user, client):
        # The default admin URLs end in a catch-all "<path:object_id>/"; the
        # timeline route must be matched before it.
        client.force_login(admin_user)
        r = client.get(self._url(product))
        assert r.status_code == 200
        assert r.template_name == "snapadmin/audit_timeline.html"

    def test_object_column_links_to_the_timeline(self, product, admin_user):
        self._seed(product, admin_user, count=1)
        row = SnapadminAuditLog.objects.get()
        model_admin = site._registry[SnapadminAuditLog]
        assert self._url(product) in model_admin.object_timeline(row)
        assert self._url(product) in model_admin.timeline_link(row)

    def test_unidentifiable_row_has_no_link(self):
        row = SnapadminAuditLog.objects.create(action="create", object_repr="orphan")
        model_admin = site._registry[SnapadminAuditLog]
        assert model_admin.timeline_url(row) is None
        assert model_admin.object_timeline(row) == "orphan"
        assert model_admin.timeline_link(row) == "\u2014"

    def test_row_without_repr_falls_back_to_the_id(self, product, admin_user):
        self._seed(product, admin_user, count=1)
        row = SnapadminAuditLog.objects.get()
        row_id, model_admin = row.object_id, site._registry[SnapadminAuditLog]
        SnapadminAuditLog.objects.update(object_repr="")
        row.refresh_from_db()
        assert row_id in model_admin.object_timeline(row)


# ── SIEM export command ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestExportCommand:
    def _seed(self, product, admin_user):
        audit.record_audit(_request(admin_user), audit.CREATE, product, {"name": {"old": None, "new": "P"}})
        audit.record_audit(_request(admin_user), audit.DELETE, product, None)

    def test_json_export(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "audit.jsonl"
        call_command("snapadmin_audit_export", "--output", str(out))
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 2
        rows = [json.loads(l) for l in lines]
        assert {r["action"] for r in rows} == {"create", "delete"}
        assert rows[0]["ip_address"] == "203.0.113.5"

    def test_csv_export(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "audit.csv"
        call_command("snapadmin_audit_export", "--format", "csv", "--output", str(out))
        text = out.read_text()
        assert "timestamp,action,actor_id" in text.replace(" ", "")[:80] or "action" in text
        assert text.count("\n") >= 3  # header + 2 rows

    def test_action_filter(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "a.jsonl"
        call_command("snapadmin_audit_export", "--action", "delete", "--output", str(out))
        rows = [json.loads(l) for l in out.read_text().strip().splitlines()]
        assert [r["action"] for r in rows] == ["delete"]

    def test_app_and_model_filter(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "a.jsonl"
        call_command("snapadmin_audit_export", "--app", "demo", "--model", "product", "--output", str(out))
        assert len(out.read_text().strip().splitlines()) == 2
        out2 = tmp_path / "b.jsonl"
        call_command("snapadmin_audit_export", "--model", "nope", "--output", str(out2))
        assert out2.read_text().strip() == ""

    def test_since_date_filter(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "a.jsonl"
        tomorrow = (timezone.now() + timezone.timedelta(days=1)).date().isoformat()
        call_command("snapadmin_audit_export", "--since", tomorrow, "--output", str(out))
        assert out.read_text().strip() == ""  # nothing on/after tomorrow

    def test_since_datetime_filter(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "a.jsonl"
        yesterday = (timezone.now() - timezone.timedelta(days=1)).isoformat()
        call_command("snapadmin_audit_export", "--since", yesterday, "--until",
                     (timezone.now() + timezone.timedelta(days=1)).isoformat(), "--output", str(out))
        assert len(out.read_text().strip().splitlines()) == 2

    def test_invalid_since_raises(self, product, admin_user):
        with pytest.raises(CommandError):
            call_command("snapadmin_audit_export", "--since", "not-a-date")

    def test_stdout_export(self, product, admin_user, capsys):
        self._seed(product, admin_user)
        call_command("snapadmin_audit_export")  # output '-' → stdout
        assert len(capsys.readouterr().out.strip().splitlines()) == 2

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=30)
    def test_purge_removes_aged_rows(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        old = timezone.now() - timezone.timedelta(days=90)
        # queryset.update bypasses the immutability guard (auto_now_add otherwise).
        SnapadminAuditLog.objects.update(timestamp=old)
        out = tmp_path / "a.jsonl"
        call_command("snapadmin_audit_export", "--purge", "--output", str(out))
        assert SnapadminAuditLog.objects.count() == 0

    @override_settings(SNAPADMIN_AUDIT_RETENTION_DAYS=0)
    def test_purge_disabled_when_retention_zero(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "a.jsonl"
        call_command("snapadmin_audit_export", "--purge", "--output", str(out))
        assert SnapadminAuditLog.objects.count() == 2

    # ── PII masking (#SEC6) ──────────────────────────────────────────────────

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_masked_by_default(self, tmp_path, product, admin_user):
        audit.record_audit(
            _request(admin_user), audit.UPDATE, product,
            {"name": {"old": "Old Name", "new": "New Name"}},
        )
        out = tmp_path / "a.jsonl"
        call_command("snapadmin_audit_export", "--output", str(out))
        row = json.loads(out.read_text().strip())
        assert row["changes"]["name"] == {"old": "Ol****me", "new": "Ne****me"}

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_reveal_pii_flag_shows_raw(self, tmp_path, product, admin_user):
        audit.record_audit(
            _request(admin_user), audit.UPDATE, product,
            {"name": {"old": "Old Name", "new": "New Name"}},
        )
        out = tmp_path / "a.jsonl"
        call_command("snapadmin_audit_export", "--reveal-pii", "--output", str(out))
        row = json.loads(out.read_text().strip())
        assert row["changes"]["name"] == {"old": "Old Name", "new": "New Name"}

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_masked_by_default_csv(self, tmp_path, product, admin_user):
        audit.record_audit(
            _request(admin_user), audit.UPDATE, product,
            {"name": {"old": "Old Name", "new": "New Name"}},
        )
        out = tmp_path / "a.csv"
        call_command("snapadmin_audit_export", "--format", "csv", "--output", str(out))
        text = out.read_text()
        assert "Old Name" not in text
        assert "Ol****me" in text

    def test_unconfigured_model_untouched(self, tmp_path, product, admin_user):
        self._seed(product, admin_user)
        out = tmp_path / "a.jsonl"
        call_command("snapadmin_audit_export", "--output", str(out))
        rows = [json.loads(l) for l in out.read_text().strip().splitlines()]
        assert any(r["changes"] and r["changes"].get("name", {}).get("new") == "P" for r in rows)
