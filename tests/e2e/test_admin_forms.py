"""
tests/e2e/test_admin_forms.py

What a generated change form has to get right in a browser, beyond the fields
being present in the HTML: every relation declared ``show_in_form=True`` is
editable and saves, and the form stays inside the window with every control
showing what it holds.

The relation and layout scenarios below each started as a defect this suite
found (#QA1e): a forward one-to-one or many-to-many field was dropped from the
form entirely, the rich-text editor pushed the page sideways on Unfold and lost
half its toolbar on the stock admin, and every single-choice select on the stock
admin rendered its value invisibly.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from tests.e2e.support import expect

#: Forms with a rich-text editor — the widest control SnapAdmin generates. On
#: Product it has a line of its own; on Showcase (one field of every SnapField
#: type, across Unfold tabs) it is grouped into a ``row=`` with a plain text
#: field, where a column is sized by its content — the case in which a first
#: version of the fix collapsed the editor to the width of its label.
WIDE_FORMS = ["/admin/demo/product/add/", "/admin/demo/showcase/add/"]


def test_a_product_is_saved_with_the_tags_picked_in_its_form(page, log_in, admin_user):
    from demo.apps.shop.models import Product, Tag

    wanted = [Tag.objects.create(name="sale"), Tag.objects.create(name="new")]
    Tag.objects.create(name="clearance")
    log_in(admin_user)
    page.goto("/admin/demo/product/add/")

    page.locator('input[name="name"]').fill("Laptop Pro")
    page.locator('input[name="price"]').fill("999.00")
    page.locator('select[name="tags"]').select_option([str(tag.pk) for tag in wanted])
    page.locator('input[name="_save"], button[name="_save"]').first.click()

    expect(page).to_have_url(re.compile(r"/admin/demo/product/$"))
    product = Product.objects.get()
    assert sorted(product.tags.values_list("name", flat=True)) == ["new", "sale"]


def test_a_profile_is_saved_linked_to_the_customer_picked_in_its_form(
    page, log_in, admin_user, customer, customer_inactive
):
    from demo.apps.shop.models import CustomerProfile

    log_in(admin_user)
    page.goto("/admin/demo/customerprofile/add/")

    # SnapOneToOneField(autocomplete=True): a select2 fed by the admin's
    # autocomplete endpoint, which answers only once the dropdown opens.
    page.locator(".field-customer .select2-selection").click()
    page.locator(".select2-results__option", has_text="Jones, Bob").click()
    page.locator('textarea[name="bio"]').fill("Prefers email.")
    page.locator('input[name="_save"], button[name="_save"]').first.click()

    expect(page).to_have_url(re.compile(r"/admin/demo/customerprofile/$"))
    profile = CustomerProfile.objects.get()
    assert profile.customer_id == customer_inactive.pk
    assert profile.bio == "Prefers email."


@pytest.mark.parametrize("form_url", WIDE_FORMS)
def test_a_form_with_a_rich_text_editor_fits_the_window(page, log_in, admin_user, form_url):
    log_in(admin_user)

    page.goto(form_url)
    editor = page.locator(".ck-editor").first
    expect(editor).to_be_visible()

    layout = page.evaluate(
        """() => {
            const editor = document.querySelector('.ck-editor').getBoundingClientRect();
            return {
                documentWidth: document.documentElement.scrollWidth,
                windowWidth: document.documentElement.clientWidth,
                editorRight: editor.right,
                editorWidth: editor.width,
            };
        }"""
    )
    assert layout["documentWidth"] == layout["windowWidth"], (
        f"the form scrolls sideways: {layout['documentWidth']}px of content in a "
        f"{layout['windowWidth']}px window"
    )
    assert layout["editorRight"] <= layout["windowWidth"]
    # Wider than half the window: on a line of its own, never squeezed into a
    # share of a row next to another field.
    assert layout["editorWidth"] > layout["windowWidth"] / 2, (
        f"the editor did not get a full line: {layout['editorWidth']}px"
    )
    # CKEditor moves the toolbar buttons that do not fit into a "more" menu —
    # but only once it can see that they do not fit. Before the fix the toolbar
    # was always as wide as all of its buttons, so this menu never appeared.
    expect(page.locator(".ck-toolbar__grouped-dropdown").first).to_be_visible()


def test_every_single_choice_select_shows_its_value(page, log_in, admin_user):
    from demo.apps.shop.models import Category, Product

    laptops = Category.objects.create(name="Laptops", slug="laptops")
    product = Product.objects.create(
        name="Laptop Pro", price=Decimal("999.00"), available=True, category=laptops
    )
    log_in(admin_user)

    page.goto(f"/admin/demo/product/{product.pk}/change/")
    expect(page.locator('select[name="available"]')).to_have_value("true")

    selects = page.evaluate(
        """() => [...document.querySelectorAll('select:not([multiple])')]
            .filter(select => select.offsetParent !== null && !select.name.includes('__prefix__'))
            .map(select => {
                const style = getComputedStyle(select);
                const contentHeight = select.clientHeight
                    - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
                return {name: select.name, contentHeight, fontSize: parseFloat(style.fontSize)};
            })"""
    )
    assert {select["name"] for select in selects} >= {"available"}
    cramped = [select for select in selects if select["contentHeight"] < select["fontSize"]]
    assert cramped == [], f"select(s) with no room to show their value: {cramped}"
