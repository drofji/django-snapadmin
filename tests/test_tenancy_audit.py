"""
tests/test_tenancy_audit.py — audit-log read scoping (#FUT1b)

``SnapadminAuditLog`` carries no tenant column of its own (see the model's
own docstring) — a row naming a tenant-scoped target model is checked
against that model's *own* current visibility instead
(``snapadmin.audit.visible_audit_queryset``). Pinned here: the changelist
(``SnapadminAuditLogAdmin.get_queryset``), the per-object timeline
(``_object_visible_to_tenant`` / ``timeline_view``), and the fail-closed
rule for a target row that no longer exists.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.admin import site
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse

from snapadmin import audit, tenancy
from snapadmin.models import SnapadminAuditLog


def _request(user):
    req = RequestFactory().post("/admin/x/")
    req.user = user
    return req


def _audit_admin():
    return site._registry[SnapadminAuditLog]


def _order(tenant_id, customer):
    from demo.apps.shop.models import Order
    return Order.objects.create(customer=customer, total=Decimal("1.00"), tenant_id=tenant_id)


@pytest.mark.django_db
class TestChangelistQueryset:
    def test_hides_a_row_for_another_tenant(self, admin_user, customer):
        mine = _order("acme", customer)
        theirs = _order("globex", customer)
        audit.record_audit(_request(admin_user), audit.CREATE, mine, None)
        audit.record_audit(_request(admin_user), audit.CREATE, theirs, None)

        with tenancy.use_tenant("acme"):
            visible = _audit_admin().get_queryset(_request(admin_user))
            object_ids = set(visible.values_list("object_id", flat=True))

        assert str(mine.pk) in object_ids
        assert str(theirs.pk) not in object_ids

    def test_hides_a_row_whose_object_no_longer_exists(self, admin_user, customer):
        # Deleted (or never-visible) is indistinguishable from "belongs to
        # another tenant" here — under-claiming, not guessing, is correct.
        mine = _order("acme", customer)
        audit.record_audit(_request(admin_user), audit.CREATE, mine, None)
        with tenancy.use_all_tenants():
            mine.delete()

        with tenancy.use_tenant("acme"):
            visible = _audit_admin().get_queryset(_request(admin_user))
            object_ids = set(visible.values_list("object_id", flat=True))
        assert str(mine.pk) not in object_ids

    def test_no_tenant_bound_hides_every_tenant_scoped_row(self, admin_user, customer):
        mine = _order("acme", customer)
        audit.record_audit(_request(admin_user), audit.CREATE, mine, None)

        visible = _audit_admin().get_queryset(_request(admin_user))
        assert str(mine.pk) not in set(visible.values_list("object_id", flat=True))

    def test_non_tenant_scoped_model_rows_are_unaffected(self, admin_user, product):
        audit.record_audit(_request(admin_user), audit.CREATE, product, None)
        # No tenant bound at all — Product isn't tenant-scoped, so its row
        # must still be visible regardless.
        visible = _audit_admin().get_queryset(_request(admin_user))
        assert str(product.pk) in set(visible.values_list("object_id", flat=True))

    def test_changelist_renders_with_a_tenant_bound(self, admin_user, client, customer):
        from tests.conftest import DEFAULT_TEST_TENANT

        # A real client request resolves admin_user's own tenant via
        # SnapTenantMiddleware (see the timeline test's comment above).
        mine = _order(DEFAULT_TEST_TENANT, customer)
        audit.record_audit(_request(admin_user), audit.CREATE, mine, None)
        client.force_login(admin_user)
        r = client.get(reverse("admin:snapadmin_snapadminauditlog_changelist"))
        assert r.status_code == 200


@pytest.mark.django_db
class TestTimelineTenantGuard:
    def test_timeline_denied_for_another_tenants_object(self, admin_user, customer):
        theirs = _order("globex", customer)
        audit.record_audit(_request(admin_user), audit.CREATE, theirs, None)

        with tenancy.use_tenant("acme"), pytest.raises(PermissionDenied):
            _audit_admin().timeline_view(
                _request(admin_user), app_label="demo", model="order",
                object_id=str(theirs.pk),
            )

    def test_timeline_allowed_for_the_bound_tenants_own_object(self, admin_user, client, customer):
        from tests.conftest import DEFAULT_TEST_TENANT

        # A real client request runs SnapTenantMiddleware, which resolves
        # admin_user's own tenant (its email domain — see demo/core/
        # tenancy.py) rather than anything this test binds manually, so the
        # row must carry *that* tenant to prove the real end-to-end path.
        mine = _order(DEFAULT_TEST_TENANT, customer)
        audit.record_audit(_request(admin_user), audit.CREATE, mine, None)
        client.force_login(admin_user)
        url = reverse(
            "admin:snapadmin_snapadminauditlog_timeline",
            args=["demo", "order", str(mine.pk)],
        )
        r = client.get(url)
        assert r.status_code == 200

    def test_timeline_unaffected_for_a_non_tenant_scoped_model(self, admin_user, product):
        audit.record_audit(_request(admin_user), audit.CREATE, product, None)
        # No tenant bound — Product isn't tenant-scoped, so the guard must
        # not even ask the question.
        response = _audit_admin().timeline_view(
            _request(admin_user), app_label="demo", model="product",
            object_id=str(product.pk),
        )
        assert response.status_code == 200

    def test_unresolvable_model_is_left_to_the_rest_of_the_view(self, admin_user):
        # A legacy/hand-written row naming a model that no longer exists —
        # nothing here to check against, so the guard steps aside rather
        # than raising a confusing error of its own.
        response = _audit_admin().timeline_view(
            _request(admin_user), app_label="demo", model="nosuchmodel",
            object_id="1",
        )
        assert response.status_code == 200
