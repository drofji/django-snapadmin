"""
tests/e2e/test_themes.py

The two admins SnapAdmin supports — Unfold and Django's stock admin — and the
difference between them a browser can see.

One process renders one theme (the choice is made at import time), so CI runs
this whole suite twice, the second time with ``SNAPADMIN_TEST_ADMIN_THEME=stock``.
The scenarios here are the ones whose expected answer depends on which run it
is; everything else in the suite asserts the same outcome under both.

Found by this suite (#QA1e): the demo's own admin index extended an Unfold-only
layout, so ``/admin/`` answered 500 the moment Unfold left ``INSTALLED_APPS`` —
on a package whose documentation promises the stock admin is fully supported.
"""

from __future__ import annotations

from tests.e2e.support import expect

THEME_SHEETS = {
    True: "snapadmin/css/admin-unfold.css",
    False: "snapadmin/css/admin-stock.css",
}


def _stylesheets(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('link[rel=stylesheet]')].map(link => link.href)"
    )


def test_a_generated_page_loads_exactly_one_theme_layer(page, log_in, admin_user, unfold_admin):
    expected_sheet = THEME_SHEETS[unfold_admin]
    other_sheet = THEME_SHEETS[not unfold_admin]
    log_in(admin_user)

    page.goto("/admin/demo/product/add/")

    sheets = _stylesheets(page)
    assert any(sheet.endswith(expected_sheet) for sheet in sheets), sheets
    assert not any(sheet.endswith(other_sheet) for sheet in sheets), (
        f"both theme layers reached the page — {other_sheet} overrides the active theme's layout"
    )
    assert any("/static/unfold/" in sheet for sheet in sheets) is unfold_admin


def test_the_admin_index_renders_under_the_active_theme(page, log_in, admin_user, unfold_admin):
    log_in(admin_user)

    response = page.goto("/admin/")

    assert response is not None and response.status == 200
    panel_heading = page.get_by_text("SnapAdmin Custom Dashboard")
    if unfold_admin:
        # The demo's panel replaces the app list; Unfold's sidebar navigates.
        expect(panel_heading).to_be_visible()
        expect(page.locator("#nav-sidebar, aside").first).to_be_visible()
    else:
        # The panel is Unfold-styled markup; the stock index keeps its app list,
        # which is the only navigation that page has.
        expect(panel_heading).to_have_count(0)
        expect(page.locator("#content-main .app-demo")).to_be_visible()
        expect(page.get_by_role("link", name="Products", exact=True)).to_be_visible()
