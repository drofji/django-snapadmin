"""
Tests for the Unfold CSS split (task: split-unfold-styles).

snapadmin ships two stylesheets:

* ``snapadmin/css/admin.css`` — theme-agnostic core rules, loaded on every
  SnapModel admin page regardless of theme.
* ``snapadmin/css/admin-unfold.css`` — Unfold-specific overrides, opt-in:
  only layered on when django-unfold is installed, and always *after* the
  core sheet so its ``.unfold``-scoped rules win the cascade.

These tests verify the wiring (conditional injection, ordering) and that the
two sheets stay cleanly separated (no ``.unfold`` rules leak into core).
"""

from pathlib import Path

import pytest
from django.contrib import admin

from snapadmin.models import SnapModel, UNFOLD_INSTALLED

CORE_CSS = "snapadmin/css/admin.css"
UNFOLD_CSS = "snapadmin/css/admin-unfold.css"

STATIC_ROOT = Path(__file__).resolve().parent.parent / "snapadmin" / "static"


def _media_css(model):
    """Return the list of CSS files declared on a model's registered admin."""
    model_admin = admin.site._registry[model]
    return list(model_admin.Media.css["all"])


def _read_asset(rel_path):
    path = STATIC_ROOT / rel_path
    assert path.exists(), f"missing asset: {path}"
    return path.read_text(encoding="utf-8")


class TestCssAssetsExist:
    def test_core_sheet_exists(self):
        assert (STATIC_ROOT / CORE_CSS).exists()

    def test_unfold_sheet_exists(self):
        assert (STATIC_ROOT / UNFOLD_CSS).exists()


class TestCssInjection:
    def test_core_css_loaded_on_every_model(self):
        from demo.apps.shop.models import Product
        assert CORE_CSS in _media_css(Product)

    @pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")
    def test_unfold_css_loaded_when_unfold_installed(self):
        from demo.apps.shop.models import Product
        assert UNFOLD_CSS in _media_css(Product)

    @pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")
    def test_unfold_css_loaded_after_core(self):
        """Unfold overrides must follow core so .unfold rules win the cascade."""
        from demo.apps.shop.models import Product
        css = _media_css(Product)
        assert css.index(CORE_CSS) < css.index(UNFOLD_CSS)

    @pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")
    def test_unfold_css_listed_once(self):
        from demo.apps.shop.models import Product
        assert _media_css(Product).count(UNFOLD_CSS) == 1


class TestCoreSheetIsThemeAgnostic:
    @pytest.fixture(scope="class")
    @staticmethod
    def source():
        return _read_asset(CORE_CSS)

    def test_no_unfold_selectors(self, source):
        assert ".unfold" not in source

    def test_keeps_design_tokens(self, source):
        # Shared :root tokens live in core so both layers reference them.
        assert ":root" in source
        assert "--primary-color" in source
        assert "--radius" in source

    def test_keeps_theme_agnostic_rules(self, source):
        assert ".snap-field-row" in source
        assert ".field-formatted_id" in source


class TestUnfoldSheetIsUnfoldScoped:
    @pytest.fixture(scope="class")
    @staticmethod
    def source():
        return _read_asset(UNFOLD_CSS)

    def test_contains_unfold_selectors(self, source):
        assert ".unfold" in source

    def test_references_shared_tokens(self, source):
        # Tokens are defined in core; this layer only consumes them.
        assert "var(--radius)" in source
        assert ":root {" not in source  # no token *definitions*, only uses

    def test_carries_object_tools_fix(self, source):
        assert "ul.object-tools" in source
