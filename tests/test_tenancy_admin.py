"""
tests/test_tenancy_admin.py — tenant stamping on admin create (#FUT1b)

The generated admin form never exposes a tenant-scoped model's tenant
column (no Snap field ``show_in_form`` flag — see ``get_admin_fields()``),
so ``SnapSaveMixin._stamp_tenant`` is the only place a create's tenant is
ever assigned. Exercised the same way ``tests/test_audit_trail.py`` exercises
``save_model`` directly — a ``RequestFactory`` request plus a minimal fake
form — since building a real multipart POST for a model carrying an inline
formset is not what this test is about.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.admin import site
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse

from snapadmin import tenancy


def _request(user):
    req = RequestFactory().post("/admin/x/")
    req.user = user
    return req


def _order_admin():
    from demo.apps.shop.models import Order
    return site._registry[Order]


@pytest.mark.django_db
class TestAdminCreateStampsTenant:
    def test_bound_tenant_is_stamped_on_create(self, admin_user, customer):
        from demo.apps.shop.models import Order

        obj = Order(customer=customer, total=Decimal("10.00"))
        form = SimpleNamespace(
            cleaned_data={"customer": customer, "total": Decimal("10.00")},
            changed_data=[], initial={},
        )
        with tenancy.use_tenant("acme"):
            _order_admin().save_model(_request(admin_user), obj, form, change=False)

        assert obj.pk is not None
        assert obj.tenant_id == "acme"

    def test_no_tenant_bound_refuses_the_create(self, admin_user, customer):
        from demo.apps.shop.models import Order

        obj = Order(customer=customer, total=Decimal("10.00"))
        form = SimpleNamespace(
            cleaned_data={"customer": customer, "total": Decimal("10.00")},
            changed_data=[], initial={},
        )
        with pytest.raises(PermissionDenied):
            _order_admin().save_model(_request(admin_user), obj, form, change=False)
        assert obj.pk is None

    def test_all_tenants_context_also_refuses_the_create(self, admin_user, customer):
        # ALL_TENANTS is a background-only bypass for reads (retention purge,
        # reindex); it names no concrete tenant to assign, so a create still
        # has nothing to stamp and must refuse the same way "no tenant" does.
        from demo.apps.shop.models import Order

        obj = Order(customer=customer, total=Decimal("10.00"))
        form = SimpleNamespace(
            cleaned_data={"customer": customer, "total": Decimal("10.00")},
            changed_data=[], initial={},
        )
        with tenancy.use_all_tenants():
            with pytest.raises(PermissionDenied):
                _order_admin().save_model(_request(admin_user), obj, form, change=False)
        assert obj.pk is None

    def test_untenanted_model_create_is_unaffected(self, admin_user):
        # Product is not tenant-scoped — _stamp_tenant must be a complete
        # no-op for it, with or without a tenant bound.
        from demo.apps.shop.models import Product

        obj = Product(name="Widget", price=Decimal("5.00"))
        form = SimpleNamespace(
            cleaned_data={"name": "Widget", "price": Decimal("5.00")},
            changed_data=[], initial={},
        )
        site._registry[Product].save_model(_request(admin_user), obj, form, change=False)
        assert obj.pk is not None


# ── read/delete scoping via the real request cycle (#FUT1b) ──────────────────
#
# Unlike the create tests above, these go through admin_client (a real,
# session-authenticated request) end to end — SnapTenantMiddleware resolves
# admin_user's tenant via demo/core/tenancy.py's email-domain fallback, the
# same path a live deployment uses, so this is the closest thing to an
# integration test for the admin's read-side scoping.

@pytest.mark.django_db
class TestAdminReadAndDeleteScoping:
    def test_changelist_lists_only_the_callers_own_tenant(self, admin_client, customer):
        from demo.apps.shop.models import Order
        from tests.conftest import DEFAULT_TEST_TENANT

        mine = Order.objects.create(customer=customer, total=Decimal("1.00"), tenant_id=DEFAULT_TEST_TENANT)
        theirs = Order.objects.create(customer=customer, total="2.00", tenant_id="a-different-tenant")

        url = reverse("admin:demo_order_changelist")
        html = admin_client.get(url).content.decode()
        assert f"/order/{mine.pk}/change/" in html
        assert f"/order/{theirs.pk}/change/" not in html

    def test_change_view_is_unreachable_for_another_tenants_object(self, admin_client, customer):
        from demo.apps.shop.models import Order

        theirs = Order.objects.create(customer=customer, total="2.00", tenant_id="a-different-tenant")
        url = reverse("admin:demo_order_change", args=[theirs.pk])
        # Django admin's own "object not in get_queryset()" response — the
        # same one a genuinely nonexistent pk gets — not a raw 404.
        response = admin_client.get(url)
        assert response.status_code == 302
        assert response["Location"] == reverse("admin:index")

    def test_change_view_200s_for_the_callers_own_tenant(self, admin_client, order):
        # `order` (conftest.py) already carries DEFAULT_TEST_TENANT, the
        # tenant admin_user's own email domain resolves to.
        url = reverse("admin:demo_order_change", args=[order.pk])
        assert admin_client.get(url).status_code == 200

    def test_delete_view_is_unreachable_for_another_tenants_object(self, admin_client, customer):
        from demo.apps.shop.models import Order

        theirs = Order.objects.create(customer=customer, total="2.00", tenant_id="a-different-tenant")
        url = reverse("admin:demo_order_delete", args=[theirs.pk])
        response = admin_client.get(url)
        assert response.status_code == 302
        assert response["Location"] == reverse("admin:index")
        # Nothing was deleted — the row is still there for its own tenant.
        with tenancy.use_all_tenants():
            assert Order.objects.filter(pk=theirs.pk).exists()
