"""
tests/test_subject_request.py — GDPR subject-access request command (#FUT4b)

``manage.py snapadmin_subject_request export|delete`` — export reuses the
existing async-export machinery (one SnapExportJob per matched model, run
synchronously); deletion is dry-run by default, pre-flights every matched
row through Django's own deletion Collector (so a protected relation refuses
the whole run up front rather than deleting in dependency order), and writes
an audit entry that survives the deletion because SnapadminAuditLog is never
itself subject-scoped.
"""

import json
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings


def _grant_pii(user):
    from django.contrib.auth import get_user_model
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label="snapadmin", codename="view_raw_pii",
    ))
    return get_user_model().objects.get(pk=user.pk)


def _call(*args, **kwargs):
    out, err = StringIO(), StringIO()
    call_command("snapadmin_subject_request", *args, stdout=out, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


@pytest.fixture
def operator(regular_user):
    return _grant_pii(regular_user)


@pytest.fixture
def subject_customer(db):
    from demo.apps.shop.models import Customer
    return Customer.objects.create(
        first_name="Alice", last_name="Subject", email="subject@example.com",
        origin="status_a", active=True,
    )


@pytest.mark.django_db
class TestOperatorResolution:
    def test_unknown_user_raises(self, subject_customer):
        with pytest.raises(CommandError, match="No user named"):
            _call("export", model="demo.Customer", identifier=subject_customer.email, user="ghost")

    def test_user_without_pii_permission_raises(self, regular_user, subject_customer):
        with pytest.raises(CommandError, match="view_raw_pii"):
            _call("export", model="demo.Customer", identifier=subject_customer.email, user=regular_user.username)

    def test_superuser_needs_no_explicit_grant(self, admin_user, subject_customer):
        # Superusers satisfy has_perm() for everything — no separate grant needed.
        out, err = _call("export", model="demo.Customer", identifier=subject_customer.email, user=admin_user.username)
        assert "EXPORTED demo.Customer" in out


@pytest.mark.django_db
class TestModelValidation:
    def test_unknown_model_raises(self, operator):
        with pytest.raises(CommandError, match="does not resolve"):
            _call("export", model="demo.Ghost", identifier="x", user=operator.username)

    def test_malformed_model_arg_raises(self, operator):
        with pytest.raises(CommandError, match="app_label.ModelName"):
            _call("export", model="nodot", identifier="x", user=operator.username)

    def test_non_subject_model_raises(self, operator):
        # demo.Product is registered but is_data_subject is not set on it.
        with pytest.raises(CommandError, match="is_data_subject"):
            _call("export", model="demo.Product", identifier="x", user=operator.username)


@pytest.mark.django_db
class TestNoMatches:
    def test_export_reports_nothing_found(self, operator):
        out, err = _call("export", model="demo.Customer", identifier="nobody@example.com", user=operator.username)
        assert "No rows matched" in out

    def test_delete_reports_nothing_found(self, operator):
        out, err = _call("delete", model="demo.Customer", identifier="nobody@example.com", user=operator.username)
        assert "No rows matched" in out


@pytest.mark.django_db
class TestHonestLimits:
    def test_limits_are_printed_every_run(self, operator, subject_customer):
        out, err = _call("export", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        assert "cannot see or touch a" in err
        assert "backup bundle" in err
        assert "Elasticsearch copy" in err
        assert "third-party store" in err


@pytest.mark.django_db
class TestExport:
    def _seed(self, customer):
        from demo.apps.shop.models import AuditLog, CustomerProfile, Order, OrderItem, Product
        CustomerProfile.objects.create(customer=customer, newsletter=True, bio="hi")
        order = Order.objects.create(customer=customer, total=Decimal("42.00"))
        product = Product.objects.create(name="Widget", price=Decimal("5.00"))
        OrderItem.objects.create(order=order, product=product, quantity=2, price=Decimal("5.00"))
        AuditLog.objects.create(action="login", user_email=customer.email)
        return order

    def test_exports_every_matched_model(self, operator, subject_customer):
        self._seed(subject_customer)
        out, err = _call("export", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        for label in ("demo.Customer", "demo.CustomerProfile", "demo.Order", "demo.OrderItem", "demo.AuditLog"):
            assert f"EXPORTED {label}" in out

    def test_unrelated_rows_are_not_exported(self, operator, subject_customer):
        from demo.apps.shop.models import Customer
        Customer.objects.create(first_name="Other", last_name="Person", email="other@example.com",
                                 origin="status_a", active=True)
        self._seed(subject_customer)
        out, err = _call("export", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        assert "EXPORTED demo.Customer: 1 row" in out

    def test_manifest_is_written_and_lists_every_file(self, operator, subject_customer):
        from snapadmin.exporting import get_export_storage
        self._seed(subject_customer)
        out, err = _call("export", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        manifest_line = [l for l in out.splitlines() if l.startswith("Manifest:")][0]
        manifest_name = manifest_line.split("Manifest:")[1].strip()
        storage = get_export_storage()
        manifest = json.loads(storage.open(manifest_name).read())
        assert manifest["unmasked"] is True
        assert manifest["requested_by"] == operator.username
        model_names = {f["model"] for f in manifest["files"] if f["model"]}
        assert "demo.Customer" in model_names
        assert "demo.AuditLog" in model_names

    def test_export_is_unmasked_despite_configured_masking(self, operator, subject_customer):
        from snapadmin.exporting import export_file_name, get_export_storage
        from snapadmin.models import SnapExportJob

        with override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Customer": ["email"]}):
            out, err = _call("export", model="demo.Customer", identifier=subject_customer.email, user=operator.username)

        job = SnapExportJob.objects.filter(model="customer").latest("created_at")
        storage = get_export_storage()
        content = storage.open(export_file_name(job)).read().decode()
        assert subject_customer.email in content  # raw, not masked

    def test_writes_an_audit_entry(self, operator, subject_customer):
        from snapadmin.models import SnapadminAuditLog
        self._seed(subject_customer)
        _call("export", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        entry = SnapadminAuditLog.objects.filter(model="subject_access_export").latest("timestamp")
        assert entry.actor_id == operator.pk
        assert subject_customer.email not in entry.object_repr  # fingerprint only, not the raw value

    def test_recipient_encrypts_and_removes_plaintext(self, operator, subject_customer):
        pyrage = pytest.importorskip("pyrage")
        from snapadmin import crypto
        from snapadmin.exporting import get_export_storage

        identity = pyrage.x25519.Identity.generate()
        recipient = str(identity.to_public())

        out, err = _call(
            "export", model="demo.Customer", identifier=subject_customer.email,
            user=operator.username, recipient=[recipient],
        )
        assert "Encrypted to 1 recipient" in out

        manifest_line = [l for l in out.splitlines() if l.startswith("Manifest:")][0]
        # The manifest itself is encrypted too (it is appended before encryption runs).
        encrypted_manifest_name = manifest_line.split("Manifest:")[1].strip() + ".age"
        storage = get_export_storage()
        assert storage.exists(encrypted_manifest_name)
        assert not storage.exists(manifest_line.split("Manifest:")[1].strip())

        import io
        identity_path_holder = io.BytesIO()
        with storage.open(encrypted_manifest_name, "rb") as reader:
            crypto.decrypt_stream(
                reader, identity_path_holder,
                identity_path=_write_identity(identity),
            )
        manifest = json.loads(identity_path_holder.getvalue().decode())
        assert manifest["unmasked"] is True


def _write_identity(identity) -> str:
    import tempfile
    path = tempfile.mktemp(suffix=".txt")
    with open(path, "w") as fh:
        fh.write(str(identity) + "\n")
    return path


@pytest.mark.django_db
class TestDeleteDryRun:
    def test_dry_run_deletes_nothing(self, operator, subject_customer):
        from demo.apps.shop.models import Customer
        out, err = _call("delete", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        assert "DRY RUN demo.Customer: 1 row" in out
        assert Customer.objects.filter(pk=subject_customer.pk).exists()

    def test_dry_run_previews_a_block_identically_to_confirm(self, operator, subject_customer):
        from demo.apps.shop.models import Order
        Order.objects.create(customer=subject_customer, total=Decimal("1.00"))
        out, err = _call("delete", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        assert "REFUSED" in out
        assert "demo.Order" in out


@pytest.mark.django_db
class TestDeleteConfirm:
    def test_confirm_deletes_the_subject_row(self, operator, subject_customer):
        from demo.apps.shop.models import Customer
        out, err = _call(
            "delete", model="demo.Customer", identifier=subject_customer.email,
            user=operator.username, confirm=True,
        )
        assert "DELETED demo.Customer: 1 row" in out
        assert not Customer.objects.filter(pk=subject_customer.pk).exists()

    def test_confirm_cascades_to_a_dependent_row_with_no_protect(self, operator, subject_customer):
        from demo.apps.shop.models import Customer, CustomerProfile
        CustomerProfile.objects.create(customer=subject_customer, newsletter=False, bio="")
        _call(
            "delete", model="demo.Customer", identifier=subject_customer.email,
            user=operator.username, confirm=True,
        )
        assert not Customer.objects.filter(pk=subject_customer.pk).exists()
        assert not CustomerProfile.objects.filter(customer_id=subject_customer.pk).exists()

    def test_confirm_refuses_and_deletes_nothing_when_protected(self, operator, subject_customer):
        from demo.apps.shop.models import Customer, Order
        from tests.conftest import DEFAULT_TEST_TENANT
        from snapadmin.tenancy import use_tenant
        Order.objects.create(customer=subject_customer, total=Decimal("1.00"), tenant_id=DEFAULT_TEST_TENANT)
        out, err = _call(
            "delete", model="demo.Customer", identifier=subject_customer.email,
            user=operator.username, confirm=True,
        )
        assert "REFUSED" in out
        assert Customer.objects.filter(pk=subject_customer.pk).exists()
        with use_tenant(DEFAULT_TEST_TENANT):
            assert Order.objects.filter(customer_id=subject_customer.pk).exists()

    def test_writes_an_audit_entry_that_survives(self, operator, subject_customer):
        from snapadmin.models import SnapadminAuditLog
        _call(
            "delete", model="demo.Customer", identifier=subject_customer.email,
            user=operator.username, confirm=True,
        )
        entry = SnapadminAuditLog.objects.filter(model="subject_access_deletion").latest("timestamp")
        assert entry.action == SnapadminAuditLog.Action.DELETE
        assert entry.changes["models_deleted"]["new"]["demo.Customer"] == 1
        # The entry itself must never be reachable by a later SAR sweep for
        # the same subject — SnapadminAuditLog carries no subject_path at all.
        from snapadmin.registry import get_model_meta, is_registered
        assert not is_registered(SnapadminAuditLog)

    def test_report_counts_match_between_dry_run_and_confirm(self, operator, subject_customer):
        from demo.apps.shop.models import CustomerProfile
        CustomerProfile.objects.create(customer=subject_customer, newsletter=False, bio="")
        out_dry, _ = _call("delete", model="demo.Customer", identifier=subject_customer.email, user=operator.username)
        out_live, _ = _call(
            "delete", model="demo.Customer", identifier=subject_customer.email,
            user=operator.username, confirm=True,
        )
        assert "1 row" in [l for l in out_dry.splitlines() if "demo.CustomerProfile" in l][0]
        assert "1 row" in [l for l in out_live.splitlines() if "demo.CustomerProfile" in l][0]

    def test_export_job_failure_raises(self, operator, subject_customer, monkeypatch):
        from snapadmin import exporting
        from snapadmin.models import SnapExportJob

        def fake_run(job_id):
            SnapExportJob.objects.filter(pk=job_id).update(status=SnapExportJob.Status.FAILED, error="boom")

        monkeypatch.setattr(exporting, "run_export_job", fake_run)
        with pytest.raises(CommandError, match="failed: boom"):
            _call("export", model="demo.Customer", identifier=subject_customer.email, user=operator.username)


@pytest.mark.django_db
class TestDeleteEsOnly:
    """_run_delete is exercised directly here — an ES_ONLY subject-scoped
    model needs no real Elasticsearch connection (EsQuerySet.delete()
    degrades to a logged warning when it cannot reach one), but building one
    through the full CLI flow would need a real ES-backed demo model this
    project does not ship. Unit-level, not integration, on purpose."""

    def _command(self):
        from snapadmin.management.commands.snapadmin_subject_request import Command
        cmd = Command()
        cmd.stdout = type(cmd.stdout)(StringIO())
        cmd.stderr = type(cmd.stderr)(StringIO())
        return cmd

    def _es_only_model(self):
        from django.db import models as django_models
        from django.test.utils import isolate_apps
        from snapadmin.models import EsStorageMode, SnapModel

        with isolate_apps("snapadmin"):
            class EsSubject0(SnapModel):
                query = django_models.CharField(max_length=100)
                es_storage_mode = EsStorageMode.ES_ONLY
                subject_path = "query"

                class Meta:
                    app_label = "snapadmin"
                    managed = False

        return EsSubject0

    def test_dry_run_reports_es_only_rows_without_deleting(self, operator):
        model = self._es_only_model()
        row = model(pk="hit-1", query="alice@example.com")
        cmd = self._command()

        cmd._run_delete({model: [row]}, identifier="alice@example.com", operator=operator, confirm=False)

        out = cmd.stdout.getvalue()
        assert f"DRY RUN {model._meta.label}: 1 row" in out

    def test_confirm_deletes_es_only_rows(self, operator, monkeypatch):
        from snapadmin.models import EsQuerySet

        model = self._es_only_model()
        row = model(pk="hit-1", query="alice@example.com")
        cmd = self._command()

        deleted = {}

        def fake_delete(self):
            deleted["hits"] = list(self._hits)
            return len(self._hits), {model._meta.label: len(self._hits)}

        monkeypatch.setattr(EsQuerySet, "delete", fake_delete)

        cmd._run_delete({model: [row]}, identifier="alice@example.com", operator=operator, confirm=True)

        out = cmd.stdout.getvalue()
        assert f"DELETED {model._meta.label}: 1 row" in out
        assert deleted["hits"] == [row]
