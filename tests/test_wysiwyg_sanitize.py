"""Tests for wysiwyg HTML sanitization (stored-XSS defense).

Wysiwyg fields store raw HTML and default to ``show_in_list=True``, so their
value is rendered in the admin changelist. Historically that value was passed
straight to ``mark_safe``, letting anyone with write access to the field inject
script that runs in an administrator's session. These tests pin down that the
value is now sanitized on render, with an explicit ``safe_html=True`` opt-out
for content the developer fully trusts.
"""
import pytest
from django.test import override_settings
from django.utils.safestring import SafeString

from snapadmin.fields import SnapRichTextField, SnapTextField
from snapadmin.sanitize import sanitize_html

XSS = '<img src=x onerror="alert(1)"><script>alert(2)</script><b>ok</b>'


def custom_sanitizer(value: str) -> str:
    """Test double for the SNAPADMIN_HTML_SANITIZER dotted-path hook."""
    return "CUSTOM"


class TestSanitizeHtml:
    def test_strips_script_and_event_handlers(self):
        out = sanitize_html(XSS)
        assert "onerror" not in out
        assert "<script" not in out
        assert "<b>ok</b>" in out

    def test_strips_javascript_url(self):
        out = sanitize_html('<a href="javascript:alert(1)">x</a>')
        assert "javascript:" not in out

    def test_empty_value_passthrough(self):
        assert sanitize_html("") == ""

    @override_settings(
        SNAPADMIN_HTML_SANITIZER="tests.test_wysiwyg_sanitize.custom_sanitizer"
    )
    def test_custom_sanitizer_setting_is_used(self):
        assert sanitize_html("<b>x</b>") == "CUSTOM"


class TestWysiwygSafeHtmlFlag:
    def test_safe_html_defaults_to_false(self):
        assert SnapTextField(wysiwyg=True).safe_html is False

    def test_safe_html_opt_in(self):
        assert SnapTextField(wysiwyg=True, safe_html=True).safe_html is True

    def test_rich_text_field_defaults_to_sanitized(self):
        assert SnapRichTextField().safe_html is False

    def test_safe_html_is_not_forwarded_to_django(self):
        # The snap-only kwarg must not leak into the Django field constructor.
        field = SnapTextField(wysiwyg=True, safe_html=True)
        assert "safe_html" not in field.deconstruct()[3]


@pytest.mark.django_db
class TestWysiwygChangelistRender:
    def _display_for(self, model, field_name):
        model.get_admin_fields()
        return model.admin_overrides[f"safe_html_{field_name}"]

    def test_changelist_sanitizes_by_default(self):
        from demo.models import Product

        display = self._display_for(Product, "description")
        html = display(None, Product(description=XSS))

        assert isinstance(html, SafeString)
        assert "onerror" not in html
        assert "<script" not in html
        assert "<b>ok</b>" in html

    def test_changelist_renders_raw_when_safe_html_opt_in(self):
        from demo.models import Product

        field = Product._meta.get_field("description")
        original = field.safe_html
        field.safe_html = True
        try:
            display = self._display_for(Product, "description")
            html = display(None, Product(description=XSS))
            assert isinstance(html, SafeString)
            assert "onerror" in html  # trusted content rendered verbatim
        finally:
            field.safe_html = original
            Product.get_admin_fields()  # rebuild override with the restored flag


# ─────────────────────────────────────────────────────────────────────────────
# Optional CKEditor 5: the wysiwyg widget is imported lazily so the package works
# without django-ckeditor-5 (a GPL/commercial editor) installed.
# ─────────────────────────────────────────────────────────────────────────────

class TestWysiwygWidgetOptional:
    def test_returns_ckeditor_widget_when_installed(self):
        from snapadmin.models import _wysiwyg_widget

        widget = _wysiwyg_widget()
        # It is the CKEditor 5 widget (configured for the "extends" config).
        assert type(widget).__name__ == "CKEditor5Widget"

    def test_raises_actionable_error_when_ckeditor_missing(self):
        import sys
        from unittest import mock
        from django.core.exceptions import ImproperlyConfigured

        from snapadmin.models import _wysiwyg_widget

        # Simulate django-ckeditor-5 not being installed: a None entry in
        # sys.modules makes `from django_ckeditor_5.widgets import ...` raise
        # ImportError, which the helper must translate into a clear config error.
        with mock.patch.dict(sys.modules, {"django_ckeditor_5.widgets": None}):
            with pytest.raises(ImproperlyConfigured) as exc:
                _wysiwyg_widget()
        msg = str(exc.value)
        assert "wysiwyg" in msg
        assert "django-snapadmin[wysiwyg]" in msg
