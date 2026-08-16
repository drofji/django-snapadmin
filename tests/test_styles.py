"""
Tests for the admin CSS layering.

snapadmin ships three stylesheets:

* ``snapadmin/css/admin.css`` — theme-agnostic core: design tokens, SnapAdmin's
  own widgets and cosmetics no theme owns. Loaded on every SnapModel admin page.
* ``snapadmin/css/admin-stock.css`` — the form-layout rewrite for Django's
  built-in admin. Loaded **only when django-unfold is absent**.
* ``snapadmin/css/admin-unfold.css`` — the few gaps the Unfold theme leaves.
  Loaded **only when django-unfold is installed**.

The two theme layers are mutually exclusive, and that exclusivity *is* the
scoping mechanism — neither carries a theme prefix. This replaced an earlier
design where the Unfold layer prefixed every rule with a ``.unfold`` ancestor
class: current Unfold (checked against 0.99) puts no such class on the page, so
those rules never matched, while unscoped copies of the stock-admin layout in
``admin.css`` overrode Unfold's own two-column rows, field widths and select
gutter. Hence the strict assertions below that neither layer leaks.
"""

from pathlib import Path

import pytest
from django.contrib import admin

from snapadmin.models import SnapModel, UNFOLD_INSTALLED

CORE_CSS = "snapadmin/css/admin.css"
UNFOLD_CSS = "snapadmin/css/admin-unfold.css"
STOCK_CSS = "snapadmin/css/admin-stock.css"

STATIC_ROOT = Path(__file__).resolve().parent.parent / "snapadmin" / "static"

#: Declarations that rewrite a form's layout. Safe for stock Django admin,
#: destructive on top of a theme — so they may live only in the stock layer.
LAYOUT_RULES = [
    "div.form-row {",
    "div.form-row label {",
    ".selector {",
    ".datetimeshortcuts {",
    ".actions {",
]


def _media_css(model):
    """Return the list of CSS files declared on a model's registered admin."""
    model_admin = admin.site._registry[model]
    return list(model_admin.Media.css["all"])


def _read_asset(rel_path):
    path = STATIC_ROOT / rel_path
    assert path.exists(), f"missing asset: {path}"
    return path.read_text(encoding="utf-8")


def _rules(source):
    """Selector text only — comments stripped, so prose can't satisfy a test."""
    out, depth, buf = [], 0, []
    i = 0
    while i < len(source):
        if source.startswith("/*", i):
            i = source.find("*/", i) + 2
            continue
        ch = source[i]
        if ch == "{":
            depth += 1
            if depth == 1:
                out.append("".join(buf).strip())
                buf = []
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            buf.append(ch)
        i += 1
    return out


class TestCssAssetsExist:
    @pytest.mark.parametrize("sheet", [CORE_CSS, UNFOLD_CSS, STOCK_CSS])
    def test_sheet_exists(self, sheet):
        assert (STATIC_ROOT / sheet).exists()


class TestCssInjection:
    def test_core_css_loaded_on_every_model(self):
        from demo.apps.shop.models import Product
        assert CORE_CSS in _media_css(Product)

    def test_exactly_one_theme_layer_is_loaded(self):
        """Both layers on one page would put them in an unintended cascade."""
        from demo.apps.shop.models import Product
        css = _media_css(Product)
        assert (UNFOLD_CSS in css) != (STOCK_CSS in css)

    @pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")
    def test_unfold_layer_when_unfold_installed(self):
        from demo.apps.shop.models import Product
        css = _media_css(Product)
        assert UNFOLD_CSS in css
        assert STOCK_CSS not in css

    def test_stock_layer_when_unfold_absent(self, monkeypatch):
        """What an install without the theme gets — rebuilt here rather than skipped.

        This branch used to be skipped whenever Unfold was in the environment, which is
        every developer machine and the whole CI matrix, so the layer that ships to every
        no-theme install was asserted nowhere. Re-registering the admin under a patched
        flag exercises the real code path instead of trusting it.
        """
        from django.contrib import admin as dj_admin
        from demo.apps.shop.models import Product
        from snapadmin import models as snap_models

        original = dj_admin.site._registry[Product]
        monkeypatch.setattr(snap_models, "UNFOLD_INSTALLED", False)
        dj_admin.site.unregister(Product)
        try:
            Product.register_admin()
            css = _media_css(Product)
            assert STOCK_CSS in css
            assert UNFOLD_CSS not in css
        finally:
            dj_admin.site.unregister(Product)
            dj_admin.site._registry[Product] = original

    @pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")
    def test_theme_layer_loaded_after_core(self):
        from demo.apps.shop.models import Product
        css = _media_css(Product)
        assert css.index(CORE_CSS) < css.index(UNFOLD_CSS)

    @pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")
    def test_theme_layer_listed_once(self):
        from demo.apps.shop.models import Product
        assert _media_css(Product).count(UNFOLD_CSS) == 1


class TestCoreSheetIsThemeAgnostic:
    @pytest.fixture(scope="class")
    @staticmethod
    def source():
        return _read_asset(CORE_CSS)

    def test_keeps_design_tokens(self, source):
        # Shared :root tokens live in core so both layers reference them.
        assert ":root" in source
        assert "--primary-color" in source
        assert "--radius" in source

    def test_keeps_theme_agnostic_rules(self, source):
        assert ".snap-field-row" in source
        assert ".field-formatted_id" in source

    @pytest.mark.parametrize("rule", LAYOUT_RULES)
    def test_no_stock_layout_rewrite_leaks_into_core(self, source, rule):
        """#UX3–#UX6: these are what used to break the themed form layout."""
        assert rule not in source

    def test_core_never_repaints_a_control_a_theme_owns(self, source):
        for selector in _rules(source):
            assert "form-row" not in selector, selector
            # A bare `select`/`input` rule in core would hit every theme.
            assert not selector.strip().startswith("select"), selector
            assert not selector.strip().startswith("input"), selector


class TestStockSheetOwnsTheLayout:
    @pytest.fixture(scope="class")
    @staticmethod
    def source():
        return _read_asset(STOCK_CSS)

    @pytest.mark.parametrize("rule", LAYOUT_RULES)
    def test_carries_the_layout_rewrite(self, source, rule):
        assert rule in source

    def test_multi_selects_are_excluded_from_the_control_rewrite(self, source):
        """#UX4: `select[multiple]` is the filter_horizontal picker, not a control."""
        assert "select:not([multiple])" in source
        assert "\nselect {" not in source

    def test_checkbox_rows_get_a_gap(self, source):
        """#UX3: the block-label rule glued the box to its label."""
        assert 'input[type="checkbox"] + label' in source

    def test_date_shortcuts_get_spacing(self, source):
        """#UX5: "Today | 📅" sat flush against the input."""
        assert ".datetimeshortcuts" in source
        assert ".calendarbox" in source

    def test_select_leaves_room_for_the_native_arrow(self, source):
        """#UX6: symmetric padding ran the option text under the arrow."""
        assert "padding: 12px 36px 12px 16px !important" in source


class TestUnfoldSheetStaysMinimal:
    @pytest.fixture(scope="class")
    @staticmethod
    def source():
        return _read_asset(UNFOLD_CSS)

    def test_no_dead_ancestor_class_scoping(self, source):
        """Unfold puts no `.unfold` class on the page — such rules never match."""
        for selector in _rules(source):
            assert ".unfold" not in selector, selector

    def test_carries_object_tools_fix(self, source):
        assert "ul.object-tools" in source

    def test_guarantees_a_single_select_arrow(self, source):
        """#UX6: Unfold draws its own chevron over an `appearance-none` select."""
        assert "appearance: none" in source
        assert "-webkit-appearance: none" in source

    @pytest.mark.parametrize("rule", LAYOUT_RULES)
    def test_does_not_rewrite_the_themed_form_layout(self, source, rule):
        assert rule not in source

    def test_defines_no_tokens(self, source):
        assert ":root {" not in source  # tokens are defined in core only


@pytest.mark.django_db
class TestRenderedAdminUsesTheThemeLayout:
    """End-to-end: the stock-admin rewrite must not reach a themed page."""

    @pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")
    def test_stock_sheet_is_not_linked(self, admin_client, product):
        html = admin_client.get(f"/admin/demo/product/{product.pk}/change/").content.decode()
        assert "admin-stock.css" not in html
        assert "admin-unfold.css" in html
