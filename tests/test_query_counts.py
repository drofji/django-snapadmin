"""
tests/test_query_counts.py

Query-count regression pins for the N+1-prone surfaces (#QA1d, part 2).

Each generated surface is driven through its real HTTP entry point — the admin
changelist, the REST list endpoint, the audit timeline — and its SQL is counted
with :class:`~django.test.utils.CaptureQueriesContext`. Two things are pinned
per surface:

* **Scale invariance** — the query count with ``SMALL`` rows equals the count
  with ``LARGE`` rows. This is the N+1 detector: a per-row query anywhere in the
  pipeline (a missing ``select_related``/``prefetch_related``, a permission
  lookup per row, a masking rule resolved per value) makes the two numbers
  differ, whatever the absolute count happens to be.
* **The exact count** at ``LARGE`` rows. A new query on a hot path should be a
  decision somebody made, not something that slipped in. When one of these
  numbers changes: re-measure, confirm the extra query is intended, and update
  the pin in the same commit with the reason in its message.

Every measured request is preceded by one unmeasured warm-up request, so the
counts exclude one-off work the first request of a process does (content-type
cache fill, session load on a fresh client) and describe the steady state a
production server sits in.

Each test also asserts the response actually *rendered the related data or the
mask*, so a count can never stay flat because the page stopped showing the
thing whose queries it is supposed to count.

One query is backend-specific, and deliberately so: a generated changelist over
an **unfiltered** queryset asks PostgreSQL's planner for a row estimate
(``EstimatedCountPaginator`` → ``pg_class.reltuples``) before deciding whether
an exact ``COUNT(*)`` is affordable. :func:`_estimate_probe` adds that query
exactly where ``snapadmin.pagination.pg_estimated_count`` runs it, so the same
pins hold on the SQLite default run and in the PostgreSQL CI job — measured on
both.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.conftest import DEFAULT_TEST_TENANT

#: Row counts the two measurements are taken at. ``LARGE`` stays under every
#: page size in play (admin 100, REST 20 by default), so both sizes render
#: every row on one page and a per-row query cannot hide behind pagination.
SMALL = 3
LARGE = 12

#: A masking rule whose unlocking permission nobody holds, so a staff user who
#: can *view* CustomerProfile still sees ``bio`` masked. (The demo settings
#: unlock ``bio`` with ``demo.view_customerprofile`` itself, which would make
#: every viewer of the list a viewer of the raw value.)
MASKING_RULES_NOBODY_UNLOCKS = {
    "demo.CustomerProfile": {
        "bio": {"replacement": "[redacted]", "permission": "demo.unlock_customerprofile_bio"},
    },
}

Seeder = Callable[[int, str], None]


@dataclass(frozen=True)
class Measurement:
    small: int
    large: int
    body: bytes


def _measure(client: Client, url: str, seed: Seeder, **headers: str) -> Measurement:
    """Seed ``SMALL`` rows, measure; seed up to ``LARGE``, measure again."""

    def counted_get() -> tuple[int, bytes]:
        client.get(url, **headers)  # warm-up, unmeasured
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url, **headers)
        assert response.status_code == 200, response.content[:500]
        return len(ctx.captured_queries), response.content

    seed(SMALL, "small")
    small_count, _ = counted_get()
    seed(LARGE - SMALL, "large")
    large_count, body = counted_get()
    return Measurement(small=small_count, large=large_count, body=body)


def _estimate_probe() -> int:
    """The planner-estimate query an unfiltered generated changelist runs.

    Mirrors ``snapadmin.pagination.pg_estimated_count``, whose own guard is the
    connection vendor: PostgreSQL has ``pg_class.reltuples``, other backends
    return ``None`` without a query.
    """
    from snapadmin.pagination import estimated_count_enabled

    return 1 if connection.vendor == "postgresql" and estimated_count_enabled() else 0


def _staff_user(django_user_model, *codenames: str):
    """A non-superuser staff account holding exactly ``codenames``."""
    user = django_user_model.objects.create_user(
        "query_count_staff", "staff@example.com", "unused-password", is_staff=True
    )
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _token_header(user) -> dict[str, str]:
    from snapadmin.models import APIToken

    token = APIToken.create_for_user(user, "query-count pin")
    return {"HTTP_AUTHORIZATION": f"Token {token.token_key}"}


# ── seeders ─────────────────────────────────────────────────────────────────


def _seed_products_with_category_and_tags() -> Seeder:
    """Products, each with its own Category (FK) and two shared Tags (M2M)."""
    from demo.apps.shop.models import Category, Product, Tag

    tags = [Tag.objects.create(name=f"query-count-tag-{i}") for i in range(2)]

    def seed(count: int, batch: str) -> None:
        for i in range(count):
            category = Category.objects.create(name=f"category-{batch}-{i}")
            product = Product.objects.create(
                name=f"product-{batch}-{i}", price=Decimal("1.00"), category=category
            )
            product.tags.set(tags)

    return seed


def _seed_orders_with_items() -> Seeder:
    """One Customer → Order → OrderItem → Product chain per row, in the tenant
    the suite's ``admin_user`` resolves to (Order is tenant-scoped)."""
    from demo.apps.shop.models import Customer, Order, OrderItem, Product

    def seed(count: int, batch: str) -> None:
        for i in range(count):
            customer = Customer.objects.create(
                first_name=f"First-{batch}-{i}", last_name="Buyer",
                email=f"buyer-{batch}-{i}@example.com", origin="status_a", active=True,
            )
            order = Order.objects.create(
                customer=customer, total=Decimal("1.00"), tenant_id=DEFAULT_TEST_TENANT
            )
            product = Product.objects.create(name=f"item-product-{batch}-{i}", price=Decimal("1.00"))
            OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal("1.00"))

    return seed


def _seed_profiles_with_masked_bio() -> Seeder:
    """CustomerProfiles (O2O to Customer) with a masked ``bio`` and an
    encrypted ``tax_id`` — both masking paths on every row."""
    from demo.apps.shop.models import Customer, CustomerProfile

    def seed(count: int, batch: str) -> None:
        for i in range(count):
            customer = Customer.objects.create(
                first_name=f"Subject-{batch}-{i}", last_name="Person",
                email=f"subject-{batch}-{i}@example.com", origin="status_a", active=True,
            )
            CustomerProfile.objects.create(
                customer=customer, bio=f"raw-bio-{batch}-{i}", tax_id=f"TAX-{batch}-{i}"
            )

    return seed


def _seed_audit_entries(target, actor, *, field: str) -> Seeder:
    """Audit rows describing changes to ``field`` of one ``target`` object."""
    from django.contrib.contenttypes.models import ContentType

    from snapadmin.models import SnapadminAuditLog

    content_type = ContentType.objects.get_for_model(type(target))
    meta = type(target)._meta

    def seed(count: int, batch: str) -> None:
        for i in range(count):
            SnapadminAuditLog.objects.create(
                action="update", actor=actor, actor_repr=f"actor-{batch}-{i}",
                content_type=content_type, app_label=meta.app_label,
                model=meta.object_name, object_id=str(target.pk), object_repr=str(target),
                changes={field: {"old": f"raw-old-{batch}-{i}", "new": f"raw-new-{batch}-{i}"}},
            )

    return seed


# ── admin changelists ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAdminChangelistQueryCounts:
    def test_product_changelist_with_fk_and_m2m_is_constant(self, admin_user, client):
        client.force_login(admin_user)
        url = reverse("admin:demo_product_changelist")

        measured = _measure(client, url, _seed_products_with_category_and_tags())

        assert b"category-large-0" in measured.body  # the FK column rendered
        assert measured.small == measured.large
        assert measured.large == 6 + _estimate_probe()

    def test_order_changelist_with_customer_fk_is_constant(self, admin_user, client):
        client.force_login(admin_user)
        url = reverse("admin:demo_order_changelist")

        measured = _measure(client, url, _seed_orders_with_items())

        assert b"First-large-0" in measured.body  # the customer FK column rendered
        assert measured.small == measured.large
        # No planner-estimate probe here on any backend: Order is tenant-scoped,
        # so its changelist queryset carries a WHERE clause and the estimate
        # (whole-table) would be wrong.
        assert measured.large == 5

    def test_orderitem_changelist_with_two_fks_is_constant(self, admin_user, client):
        client.force_login(admin_user)
        url = reverse("admin:demo_orderitem_changelist")

        measured = _measure(client, url, _seed_orders_with_items())

        assert b"item-product-large-0" in measured.body  # the product FK column rendered
        assert measured.small == measured.large
        assert measured.large == 5 + _estimate_probe()

    def test_masked_changelist_is_constant(self, django_user_model, client, settings):
        settings.SNAPADMIN_MASKING_RULES = MASKING_RULES_NOBODY_UNLOCKS
        client.force_login(_staff_user(django_user_model, "view_customerprofile", "view_customer"))
        url = reverse("admin:demo_customerprofile_changelist")

        measured = _measure(client, url, _seed_profiles_with_masked_bio())

        assert b"[redacted]" in measured.body
        assert b"raw-bio-" not in measured.body
        assert measured.small == measured.large
        assert measured.large == 7 + _estimate_probe()


# ── REST list endpoint ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRestListQueryCounts:
    def test_product_list_with_fk_and_m2m_is_constant(self, admin_user, client):
        url = reverse("model-list", args=["demo", "Product"])

        measured = _measure(
            client, url, _seed_products_with_category_and_tags(), **_token_header(admin_user)
        )

        assert f'"count":{LARGE}'.encode() in measured.body
        assert measured.small == measured.large
        assert measured.large == 5

    def test_orderitem_list_with_two_fks_is_constant(self, admin_user, client):
        url = reverse("model-list", args=["demo", "OrderItem"])

        measured = _measure(client, url, _seed_orders_with_items(), **_token_header(admin_user))

        assert f'"count":{LARGE}'.encode() in measured.body
        assert measured.small == measured.large
        assert measured.large == 4

    def test_masked_list_is_constant(self, django_user_model, client, settings):
        settings.SNAPADMIN_MASKING_RULES = MASKING_RULES_NOBODY_UNLOCKS
        staff = _staff_user(django_user_model, "view_customerprofile", "view_customer")
        url = reverse("model-list", args=["demo", "CustomerProfile"])

        measured = _measure(client, url, _seed_profiles_with_masked_bio(), **_token_header(staff))

        assert measured.body.count(b'"bio":"[redacted]"') == LARGE
        assert b"raw-bio-" not in measured.body
        assert b"TAX-" not in measured.body  # the encrypted field is masked too
        assert measured.small == measured.large
        assert measured.large == 6


# ── audit trail ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAuditQueryCounts:
    def test_timeline_is_constant(self, admin_user, client, product):
        client.force_login(admin_user)
        url = reverse(
            "admin:snapadmin_snapadminauditlog_timeline", args=["demo", "Product", product.pk]
        )

        measured = _measure(client, url, _seed_audit_entries(product, admin_user, field="name"))

        assert b"raw-new-large-0" in measured.body  # the diff rows rendered
        assert measured.small == measured.large
        assert measured.large == 4

    def test_masked_timeline_is_constant(self, django_user_model, client, settings):
        from demo.apps.shop.models import Customer, CustomerProfile

        settings.SNAPADMIN_MASKING_RULES = MASKING_RULES_NOBODY_UNLOCKS
        staff = _staff_user(django_user_model, "view_snapadminauditlog")
        client.force_login(staff)
        profile = CustomerProfile.objects.create(
            customer=Customer.objects.create(
                first_name="Timeline", last_name="Subject", email="timeline@example.com",
                origin="status_a", active=True,
            ),
            bio="current raw bio",
        )
        url = reverse(
            "admin:snapadmin_snapadminauditlog_timeline",
            args=["demo", "CustomerProfile", profile.pk],
        )

        measured = _measure(client, url, _seed_audit_entries(profile, staff, field="bio"))

        assert b"[redacted]" in measured.body
        assert b"raw-new-" not in measured.body
        assert measured.small == measured.large
        # Two more than the superuser timeline: a non-superuser's permissions
        # are loaded once (user permissions + group permissions, one query
        # each) and cached on the user for every later has_perm in the request.
        assert measured.large == 6

    def test_audit_changelist_is_constant(self, admin_user, client, product):
        client.force_login(admin_user)
        url = reverse("admin:snapadmin_snapadminauditlog_changelist")

        measured = _measure(client, url, _seed_audit_entries(product, admin_user, field="name"))

        assert b"actor-large-0" in measured.body  # the rows rendered
        assert measured.small == measured.large
        assert measured.large == 12
