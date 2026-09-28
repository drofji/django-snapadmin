"""
tests/e2e/test_admin_flows.py

The generated admin, driven the way a person drives it: signing in, finding a
model, searching, filtering, paging, running a bulk action, being refused.

Every scenario runs against both admin themes (see ``tests/e2e/conftest.py``).
Where the two render the same control differently — Unfold keeps its filters in
a slide-over panel with an "Apply" button, the stock admin in a sidebar of
links — the scenario takes the path each theme offers and asserts the *same*
outcome, because the outcome is the contract and the markup is not.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from snapadmin.tenancy import use_tenant
from tests.conftest import DEFAULT_TEST_TENANT
from tests.e2e.support import change_link, expect, result_rows

PRODUCTS_URL = "/admin/demo/product/"


@pytest.fixture
def catalogue(db):
    """Three products: two that match "lap", one available and one not."""
    from demo.apps.shop.models import Product

    return {
        "laptop": Product.objects.create(name="Laptop Pro", price=Decimal("999.00"), available=True),
        "stand": Product.objects.create(name="Laptop Stand", price=Decimal("49.00"), available=False),
        "lamp": Product.objects.create(name="Desk Lamp", price=Decimal("19.00"), available=True),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Signing in
# ─────────────────────────────────────────────────────────────────────────────

def test_login_refuses_a_wrong_password_and_returns_to_the_requested_page(page, admin_user):
    requested_url = PRODUCTS_URL

    page.goto(requested_url)
    expect(page).to_have_url(re.compile(r"/admin/login/\?next=/admin/demo/product/$"))
    page.locator('input[name="username"]').fill(admin_user.username)
    page.locator('input[name="password"]').fill("not-the-password")
    page.get_by_role("button", name="Log in").click()

    expect(page.get_by_text("Please enter the correct username and password")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/admin/login/"))

    page.locator('input[name="password"]').fill("password")
    page.get_by_role("button", name="Log in").click()

    expect(page).to_have_url(re.compile(re.escape(requested_url) + "$"))
    expect(result_rows(page)).to_have_count(0)


# ─────────────────────────────────────────────────────────────────────────────
# Finding things
# ─────────────────────────────────────────────────────────────────────────────

def test_the_admin_index_links_to_a_generated_changelist(page, log_in, admin_user, catalogue):
    log_in(admin_user)

    page.goto("/admin/")
    # Unfold's sidebar and the stock app list both link the changelist; the
    # link text differs (Unfold puts an icon ligature inside it), the URL does not.
    page.locator(f'a[href="{PRODUCTS_URL}"]').first.click()

    expect(page).to_have_url(re.compile(re.escape(PRODUCTS_URL) + "$"))
    expect(result_rows(page)).to_have_count(3)


def test_search_narrows_the_changelist_to_matching_rows(page, log_in, admin_user, catalogue):
    log_in(admin_user)
    page.goto(PRODUCTS_URL)

    search_box = page.locator('input[name="q"]')
    search_box.fill("lap")
    search_box.press("Enter")

    expect(page).to_have_url(re.compile(r"\?q=lap$"))
    rows = result_rows(page)
    expect(rows).to_have_count(2)
    expect(rows.filter(has_text="Laptop Pro")).to_have_count(1)
    expect(rows.filter(has_text="Laptop Stand")).to_have_count(1)
    expect(rows.filter(has_text="Desk Lamp")).to_have_count(0)


def test_a_filter_limits_the_changelist_to_one_value(page, log_in, admin_user, catalogue, unfold_admin):
    log_in(admin_user)
    page.goto(PRODUCTS_URL)

    if unfold_admin:
        # Unfold keeps the filters in a slide-over panel behind a toolbar button.
        page.locator("a", has_text="Filters").click()
    page.locator("#changelist-filter").get_by_role("link", name="No", exact=True).click()

    expect(page).to_have_url(re.compile(r"available__exact=0"))
    rows = result_rows(page)
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Laptop Stand")


def test_pagination_reaches_the_rows_past_the_first_page(page, log_in, admin_user):
    from demo.apps.shop.models import Tag

    # list_per_page is 100 and the changelist sorts newest first, so exactly
    # one tag — the oldest — is left for page two.
    Tag.objects.bulk_create([Tag(name=f"tag-{number:03d}") for number in range(101)])
    log_in(admin_user)
    page.goto("/admin/demo/tag/")
    expect(result_rows(page)).to_have_count(100)
    expect(result_rows(page).filter(has_text="tag-000")).to_have_count(0)

    page.locator('a[href="?p=2"]').first.click()

    expect(page).to_have_url(re.compile(r"\?p=2$"))
    expect(result_rows(page)).to_have_count(1)
    expect(result_rows(page).first).to_contain_text("tag-000")


def test_a_changelist_row_opens_its_change_form(page, log_in, admin_user, catalogue):
    laptop = catalogue["laptop"]
    change_url = f"{PRODUCTS_URL}{laptop.pk}/change/"
    log_in(admin_user)
    page.goto(PRODUCTS_URL)

    change_link(page, change_url).click()

    expect(page).to_have_url(re.compile(re.escape(change_url) + "$"))
    expect(page.locator('input[name="name"]')).to_have_value("Laptop Pro")
    expect(page.locator('input[name="price"]')).to_have_value("999.00")


# ─────────────────────────────────────────────────────────────────────────────
# Writing: a bulk action, an inline, a validation error
# ─────────────────────────────────────────────────────────────────────────────

def test_bulk_delete_asks_for_confirmation_before_deleting(page, log_in, admin_user, catalogue):
    from demo.apps.shop.models import Product

    lamp = catalogue["lamp"]
    log_in(admin_user)
    page.goto(PRODUCTS_URL)

    page.locator(f'input.action-select[value="{lamp.pk}"]').check()
    page.locator('select[name="action"]').select_option("delete_selected")
    page.locator('button[name="index"], button[type="submit"][title="Run the selected action"]').first.click()

    expect(page.get_by_text("Are you sure you want to delete the selected product?")).to_be_visible()
    assert Product.objects.filter(pk=lamp.pk).exists(), "deleted before the confirmation"

    page.get_by_role("button", name="Yes, I’m sure").click()

    expect(page.get_by_text("Successfully deleted 1 product.")).to_be_visible()
    expect(result_rows(page)).to_have_count(2)
    assert sorted(Product.objects.values_list("name", flat=True)) == ["Laptop Pro", "Laptop Stand"]


def test_an_order_is_created_with_an_inline_line_item(page, log_in, admin_user, catalogue, customer):
    from demo.apps.shop.models import Order

    laptop = catalogue["laptop"]
    log_in(admin_user)
    page.goto("/admin/demo/order/add/")

    # The customer is a select2 autocomplete: the options arrive from the
    # admin's autocomplete endpoint only once the dropdown is opened.
    page.locator(".field-customer .select2-selection").click()
    page.locator(".select2-results__option", has_text="Smith, Alice").click()
    page.locator('input[name="total"]').fill("1998.00")
    page.locator('select[name="items-0-product"]').select_option(str(laptop.pk))
    page.locator('input[name="items-0-quantity"]').fill("2")
    page.locator('input[name="items-0-price"]').fill("999.00")
    page.locator('input[name="_save"], button[name="_save"]').first.click()

    expect(page).to_have_url(re.compile(r"/admin/demo/order/$"))
    # Order is tenant-scoped and default-deny: outside a bound tenant the ORM
    # sees no rows at all, so the assertion reads as that tenant, as the admin did.
    with use_tenant(DEFAULT_TEST_TENANT):
        order = Order.objects.get()
        items = [(item.product_id, item.quantity, item.price) for item in order.items.all()]
    assert order.customer_id == customer.pk
    assert order.total == Decimal("1998.00")
    assert order.tenant_id == DEFAULT_TEST_TENANT, "the admin must stamp the signed-in user's tenant"
    assert items == [(laptop.pk, 2, Decimal("999.00"))]


def test_a_failing_model_rule_keeps_the_form_and_writes_nothing(page, log_in, admin_user):
    from demo.apps.shop.models import Product

    log_in(admin_user)
    page.goto(f"{PRODUCTS_URL}add/")

    page.locator('input[name="name"]').fill("Free Sample")
    page.locator('input[name="price"]').fill("0")
    page.locator('select[name="available"]').select_option("true")
    page.locator('input[name="_save"], button[name="_save"]').first.click()

    expect(page.get_by_text("An available product needs a price above zero.")).to_be_visible()
    expect(page).to_have_url(re.compile(re.escape(f"{PRODUCTS_URL}add/") + "$"))
    expect(page.locator('input[name="name"]')).to_have_value("Free Sample")
    assert Product.objects.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# Being refused
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def view_only_staff(db):
    """A staff member allowed to look at products and nothing else."""
    from django.contrib.auth.models import Permission, User

    user = User.objects.create_user(
        username="viewer", password="password", email=f"viewer@{DEFAULT_TEST_TENANT}", is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="view_product"))
    return user


def test_view_permission_shows_the_data_but_no_way_to_change_it(page, log_in, view_only_staff, catalogue):
    laptop = catalogue["laptop"]
    log_in(view_only_staff)

    page.goto(PRODUCTS_URL)
    expect(result_rows(page)).to_have_count(3)
    expect(page.locator(f'a[href="{PRODUCTS_URL}add/"]')).to_have_count(0)

    page.goto(f"{PRODUCTS_URL}{laptop.pk}/change/")
    expect(page.get_by_text("Laptop Pro").first).to_be_visible()
    expect(page.locator('input[name="name"]')).to_have_count(0)
    expect(page.locator('[name="_save"]')).to_have_count(0)

    refused = page.goto(f"{PRODUCTS_URL}add/")
    assert refused is not None and refused.status == 403


def test_a_staff_member_without_the_permission_cannot_open_the_changelist(page, log_in, db):
    from django.contrib.auth.models import User

    no_permissions = User.objects.create_user(
        username="bystander", password="password", email=f"bystander@{DEFAULT_TEST_TENANT}", is_staff=True
    )
    log_in(no_permissions)

    refused = page.goto(PRODUCTS_URL)

    assert refused is not None and refused.status == 403
