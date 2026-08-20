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
        from demo.apps.shop.models import Product

        display = self._display_for(Product, "description")
        html = display(None, Product(description=XSS))

        assert isinstance(html, SafeString)
        assert "onerror" not in html
        assert "<script" not in html
        assert "<b>ok</b>" in html

    def test_changelist_renders_raw_when_safe_html_opt_in(self):
        from demo.apps.shop.models import Product

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
# Sanitizing on the way *in* (#FIX2)
#
# Rendering was sanitized; storage was not. Anything reading the column outside the
# changelist — a project template with |safe, a frontend consuming the REST API, an
# export — still received the raw payload. Sanitizing in pre_save() puts the guarantee
# in the database, and covers every ORM write path (admin, serializer, bulk_create)
# rather than one rendering path.
# ─────────────────────────────────────────────────────────────────────────────


class TestAutoSanitizeFlag:
    def test_defaults_to_true(self):
        assert SnapTextField(wysiwyg=True).auto_sanitize is True
        assert SnapRichTextField().auto_sanitize is True

    def test_can_be_switched_off(self):
        assert SnapTextField(wysiwyg=True, auto_sanitize=False).auto_sanitize is False

    def test_is_not_forwarded_to_django(self):
        """A snap-only kwarg must never reach the Django field, or it lands in a migration."""
        field = SnapTextField(wysiwyg=True, auto_sanitize=False)
        assert "auto_sanitize" not in field.deconstruct()[3]


@pytest.mark.django_db
class TestWysiwygSanitizedOnSave:
    def _product(self, **kwargs):
        from decimal import Decimal

        from demo.apps.shop.models import Product

        kwargs.setdefault("name", "X")
        return Product.objects.create(price=Decimal("1.00"), **kwargs)

    def test_the_stored_value_is_sanitized(self):
        product = self._product(description=XSS)
        product.refresh_from_db()
        assert "<script" not in product.description
        assert "onerror" not in product.description
        assert "<b>ok</b>" in product.description

    def test_the_saved_instance_matches_the_database(self):
        """The object in hand must not keep markup the database no longer holds."""
        product = self._product(description=XSS)
        assert "<script" not in product.description

    def test_an_update_is_sanitized_too(self):
        product = self._product(description="<b>clean</b>")
        product.description = XSS
        product.save()
        product.refresh_from_db()
        assert "<script" not in product.description

    def test_bulk_create_is_covered(self):
        """Proof the hook is on the field, not in ``Model.save()`` — bulk_create skips save()."""
        from decimal import Decimal

        from demo.apps.shop.models import Product

        Product.objects.bulk_create([Product(name="B", price=Decimal("2.00"), description=XSS)])
        assert "<script" not in Product.objects.get(name="B").description

    def test_queryset_update_is_not_covered(self):
        """A known, documented gap: Django never calls ``pre_save()`` for ``.update()``.

        Pinned so the limitation is visible rather than assumed — a caller reaching for
        ``.update()`` on a rich-text column has to sanitize the value itself.
        """
        from demo.apps.shop.models import Product

        product = self._product(description="<b>clean</b>")
        Product.objects.filter(pk=product.pk).update(description=XSS)
        product.refresh_from_db()
        assert "<script" in product.description

    def test_plain_text_fields_are_untouched(self):
        """Only wysiwyg fields hold HTML; sanitizing plain text would mangle it."""
        product = self._product(name="Bolts < 5mm & washers > 2mm")
        product.refresh_from_db()
        assert product.name == "Bolts < 5mm & washers > 2mm"

    @override_settings(SNAPADMIN_HTML_SANITIZER="tests.test_wysiwyg_sanitize.custom_sanitizer")
    def test_the_project_sanitizer_is_used(self):
        product = self._product(description="<b>x</b>")
        product.refresh_from_db()
        assert product.description == "CUSTOM"

    def test_safe_html_stores_the_value_verbatim(self):
        from demo.apps.shop.models import Product

        field = Product._meta.get_field("description")
        original = field.safe_html
        field.safe_html = True
        try:
            product = self._product(description=XSS)
            product.refresh_from_db()
            assert "onerror" in product.description
        finally:
            field.safe_html = original

    def test_auto_sanitize_false_stores_the_value_verbatim(self):
        from demo.apps.shop.models import Product

        field = Product._meta.get_field("description")
        original = field.auto_sanitize
        field.auto_sanitize = False
        try:
            product = self._product(description=XSS)
            product.refresh_from_db()
            assert "onerror" in product.description
        finally:
            field.auto_sanitize = original

    def test_none_and_empty_values_survive(self):
        product = self._product(description="")
        product.refresh_from_db()
        assert product.description == ""


@pytest.mark.django_db
class TestApiWritePathIsSanitized:
    """The REST hole this task exists to close: a serializer writes straight to the model."""

    def test_rest_create_stores_sanitized_html(self, admin_client):
        import json

        response = admin_client.post(
            "/api/models/demo/Product/",
            data=json.dumps({"name": "Api", "price": "3.00", "description": XSS}),
            content_type="application/json",
        )
        assert response.status_code in (200, 201), response.content

        from demo.apps.shop.models import Product

        stored = Product.objects.get(name="Api").description
        assert "<script" not in stored
        assert "onerror" not in stored


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
