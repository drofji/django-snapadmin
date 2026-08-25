"""Tests for wysiwyg HTML sanitization (stored-XSS defense).

Wysiwyg fields store raw HTML and default to ``show_in_list=True``, so their
value is rendered in the admin changelist. Historically that value was passed
straight to ``mark_safe``, letting anyone with write access to the field inject
script that runs in an administrator's session. These tests pin down that the
value is now sanitized on render, with an explicit ``safe_html=True`` opt-out
for content the developer fully trusts.
"""
import sys
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db import models as django_models
from django.test import override_settings
from django.test.utils import isolate_apps
from django.utils.safestring import SafeString

from snapadmin.fields import SnapRichTextField, SnapTextField, snap_field
from snapadmin.sanitize import _load_nh3, sanitize_html

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


# ─────────────────────────────────────────────────────────────────────────────
# Fail closed when nh3 is unavailable (#DEP1f)
#
# nh3 is a required core dependency today (see pyproject.toml), so this state is
# currently impossible in a real install — these tests exist ahead of a planned
# future release that moves nh3 behind an optional extra (mirroring [wysiwyg]/
# [xlsx]). The guarantee this locks in: once that happens, a missing nh3 must
# stop the write/render with a pointed ImproperlyConfigured rather than let
# unsanitized HTML through silently. The real installed nh3 package is never
# touched — `sys.modules["nh3"] = None` makes `import nh3` raise ImportError for
# the duration of the context, same technique already used above for the
# optional CKEditor 5 widget.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def nh3_unavailable():
    """Simulate ``import nh3`` failing, without uninstalling the real package."""
    _load_nh3.cache_clear()
    with mock.patch.dict(sys.modules, {"nh3": None}):
        yield
    _load_nh3.cache_clear()


class TestSanitizeHtmlFailsClosedWithoutNh3:
    def test_default_sanitizer_raises_improperly_configured(self, nh3_unavailable):
        with pytest.raises(ImproperlyConfigured) as exc:
            sanitize_html(XSS)
        msg = str(exc.value)
        assert "nh3" in msg

    def test_custom_sanitizer_setting_does_not_need_nh3(self, nh3_unavailable):
        """The escape hatch must not import nh3 at all -- it must keep working."""
        with override_settings(
            SNAPADMIN_HTML_SANITIZER="tests.test_wysiwyg_sanitize.custom_sanitizer"
        ):
            assert sanitize_html("<b>x</b>") == "CUSTOM"


@pytest.mark.django_db
class TestWysiwygSaveFailsClosedWithoutNh3:
    def _create(self, **kwargs):
        from decimal import Decimal

        from demo.apps.shop.models import Product

        kwargs.setdefault("name", "X")
        return Product.objects.create(price=Decimal("1.00"), **kwargs)

    def test_save_raises_and_nothing_unsanitized_is_stored(self, nh3_unavailable):
        from django.db import transaction

        from demo.apps.shop.models import Product

        with transaction.atomic():
            with pytest.raises(ImproperlyConfigured):
                self._create(name="NoNh3", description=XSS)

        # The write must not have gone through at all -- no row, sanitized or not.
        assert not Product.objects.filter(name="NoNh3").exists()

    def test_custom_sanitizer_setting_still_saves_without_nh3(self, nh3_unavailable):
        with override_settings(
            SNAPADMIN_HTML_SANITIZER="tests.test_wysiwyg_sanitize.custom_sanitizer"
        ):
            product = self._create(name="CustomNh3", description="<b>x</b>")
        product.refresh_from_db()
        assert product.description == "CUSTOM"


@pytest.mark.django_db
class TestWysiwygChangelistFailsClosedWithoutNh3:
    def _display_for(self, model, field_name):
        model.get_admin_fields()
        return model.admin_overrides[f"safe_html_{field_name}"]

    def test_render_raises_improperly_configured(self, nh3_unavailable):
        from demo.apps.shop.models import Product

        display = self._display_for(Product, "description")
        with pytest.raises(ImproperlyConfigured):
            display(None, Product(description=XSS))

    def test_custom_sanitizer_setting_still_renders_without_nh3(self, nh3_unavailable):
        from demo.apps.shop.models import Product

        with override_settings(
            SNAPADMIN_HTML_SANITIZER="tests.test_wysiwyg_sanitize.custom_sanitizer"
        ):
            display = self._display_for(Product, "description")
            html = display(None, Product(description=XSS))
        assert isinstance(html, SafeString)
        assert html == "CUSTOM"


class TestLoadNh3Caching:
    def test_successful_import_is_cached(self):
        """Calling the loader twice must not re-attempt the import each time."""
        _load_nh3.cache_clear()
        try:
            first = _load_nh3()
            second = _load_nh3()
            assert first is second
            assert _load_nh3.cache_info().hits >= 1
        finally:
            _load_nh3.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# #PAR1b — sanitize-on-write parity for snap_field(..., wysiwyg=True)
#
# A snap_field()-wrapped plain Django field is not a Snap*Field subclass, so it
# never enters SanitizedHtmlOnSaveMixin's MRO. Without this, wysiwyg=True on the
# wrapper route looked identical to the class route at the call site and
# behaved differently at runtime: it failed OPEN, walking straight around the
# fail-closed guarantee #DEP1f established. These tests prove the wrapper
# reaches the exact same guarantee — reusing SanitizedHtmlOnSaveMixin's logic,
# not a second sanitizer — on every write path the class route already covers.
#
# The model under test is declared with isolate_apps + a real schema_editor
# table (created/dropped per test) rather than a demo model: SearchLog, the
# demo's only other snap_field() dogfood, is ES_ONLY, and ES_ONLY models skip
# pre_save() entirely (get_es_document() reads attributes directly, never
# through the field), so it cannot exercise this write path at all.
# ─────────────────────────────────────────────────────────────────────────────


def _make_wysiwyg_wrapped_model():
    """An isolated model pairing every wysiwyg variant with a plain control field."""
    with isolate_apps("snapadmin"):
        class WysiwygWrapped(django_models.Model):
            class_route = SnapRichTextField(blank=True)
            wrapper_route = snap_field(django_models.TextField(blank=True), wysiwyg=True)
            wrapper_safe_html = snap_field(
                django_models.TextField(blank=True), wysiwyg=True, safe_html=True
            )
            wrapper_no_auto_sanitize = snap_field(
                django_models.TextField(blank=True), wysiwyg=True, auto_sanitize=False
            )
            plain_text = django_models.TextField(blank=True)  # not wysiwyg — must stay untouched

            class Meta:
                app_label = "snapadmin"

        return WysiwygWrapped


@pytest.fixture
def wysiwyg_wrapped_model():
    model = _make_wysiwyg_wrapped_model()
    with connection.schema_editor(atomic=False) as editor:
        editor.create_model(model)
    try:
        yield model
    finally:
        with connection.schema_editor(atomic=False) as editor:
            editor.delete_model(model)


class TestSnapFieldWysiwygKwargIsNotForwardedToDjango:
    def test_wysiwyg_is_not_forwarded_to_django(self):
        field = snap_field(django_models.TextField(), wysiwyg=True)
        assert "wysiwyg" not in field.deconstruct()[3]

    def test_safe_html_is_not_forwarded_to_django(self):
        field = snap_field(django_models.TextField(), wysiwyg=True, safe_html=True)
        assert "safe_html" not in field.deconstruct()[3]

    def test_auto_sanitize_is_not_forwarded_to_django(self):
        field = snap_field(django_models.TextField(), wysiwyg=True, auto_sanitize=False)
        assert "auto_sanitize" not in field.deconstruct()[3]


class TestBindWysiwygPreSaveIsIdempotent:
    def test_chained_snap_field_calls_bind_the_wrapper_only_once(self):
        """Two snap_field() calls touching wysiwyg=True must not double-wrap pre_save."""
        field = snap_field(snap_field(django_models.TextField(), wysiwyg=True), safe_html=True)
        bound_pre_save = field.pre_save

        # Re-binding would replace field.pre_save with a fresh closure; identity
        # staying put proves the idempotency guard actually short-circuited.
        snap_field(field, auto_sanitize=False)
        assert field.pre_save == bound_pre_save
        assert field.safe_html is True
        assert field.auto_sanitize is False


@pytest.mark.django_db(transaction=True)
class TestSnapFieldWysiwygSanitizedOnSave:
    def test_the_stored_value_is_sanitized(self, wysiwyg_wrapped_model):
        obj = wysiwyg_wrapped_model.objects.create(wrapper_route=XSS)
        obj.refresh_from_db()
        assert "<script" not in obj.wrapper_route
        assert "onerror" not in obj.wrapper_route
        assert "<b>ok</b>" in obj.wrapper_route

    def test_the_saved_instance_matches_the_database(self, wysiwyg_wrapped_model):
        """The object in hand must not keep markup the database no longer holds."""
        obj = wysiwyg_wrapped_model.objects.create(wrapper_route=XSS)
        assert "<script" not in obj.wrapper_route

    def test_an_update_is_sanitized_too(self, wysiwyg_wrapped_model):
        obj = wysiwyg_wrapped_model.objects.create(wrapper_route="<b>clean</b>")
        obj.wrapper_route = XSS
        obj.save()
        obj.refresh_from_db()
        assert "<script" not in obj.wrapper_route

    def test_bulk_create_is_covered(self, wysiwyg_wrapped_model):
        """Proof the hook is on the field, not in Model.save() — bulk_create skips save()."""
        wysiwyg_wrapped_model.objects.bulk_create([wysiwyg_wrapped_model(wrapper_route=XSS)])
        assert "<script" not in wysiwyg_wrapped_model.objects.first().wrapper_route

    def test_queryset_update_is_not_covered(self, wysiwyg_wrapped_model):
        """Same documented gap as the class route: Django never calls pre_save() for .update()."""
        obj = wysiwyg_wrapped_model.objects.create(wrapper_route="<b>clean</b>")
        wysiwyg_wrapped_model.objects.filter(pk=obj.pk).update(wrapper_route=XSS)
        obj.refresh_from_db()
        assert "<script" in obj.wrapper_route

    def test_a_plain_non_wysiwyg_field_is_untouched(self, wysiwyg_wrapped_model):
        obj = wysiwyg_wrapped_model.objects.create(plain_text="Bolts < 5mm & washers > 2mm")
        obj.refresh_from_db()
        assert obj.plain_text == "Bolts < 5mm & washers > 2mm"

    def test_safe_html_stores_the_value_verbatim(self, wysiwyg_wrapped_model):
        obj = wysiwyg_wrapped_model.objects.create(wrapper_safe_html=XSS)
        obj.refresh_from_db()
        assert "onerror" in obj.wrapper_safe_html

    def test_auto_sanitize_false_stores_the_value_verbatim(self, wysiwyg_wrapped_model):
        obj = wysiwyg_wrapped_model.objects.create(wrapper_no_auto_sanitize=XSS)
        obj.refresh_from_db()
        assert "onerror" in obj.wrapper_no_auto_sanitize

    def test_none_and_empty_values_survive(self, wysiwyg_wrapped_model):
        obj = wysiwyg_wrapped_model.objects.create(wrapper_route="")
        obj.refresh_from_db()
        assert obj.wrapper_route == ""

    def test_the_serializer_write_path_is_sanitized(self, wysiwyg_wrapped_model):
        """The REST hole this task exists to close, for the wrapper route too."""
        from snapadmin.api.serializers import build_model_serializer

        serializer_class = build_model_serializer(wysiwyg_wrapped_model)
        serializer = serializer_class(data={"wrapper_route": XSS})
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()

        instance.refresh_from_db()
        assert "<script" not in instance.wrapper_route
        assert "onerror" not in instance.wrapper_route


@pytest.mark.django_db(transaction=True)
class TestSnapFieldWysiwygMatchesTheClassRoute:
    """The parity proof: identical input in, byte-identical stored output."""

    def test_stored_values_are_byte_identical(self, wysiwyg_wrapped_model):
        obj = wysiwyg_wrapped_model.objects.create(class_route=XSS, wrapper_route=XSS)
        obj.refresh_from_db()
        assert obj.class_route == obj.wrapper_route
