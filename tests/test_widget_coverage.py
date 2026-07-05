"""
tests/test_widget_coverage.py

Coverage for snapadmin/widgets.py — SmartModelSelectorWidget.
"""

import json
import pytest
from snapadmin.widgets import SmartModelSelectorWidget


class TestSmartModelSelectorWidget:
    def _make_widget(self):
        return SmartModelSelectorWidget()

    def test_get_context_no_value(self):
        widget = self._make_widget()
        context = widget.get_context("allowed_models", None, {})
        assert "all_models_json" in context
        assert "current_values" in context
        assert context["current_values"] == []

    def test_get_context_with_list_value(self):
        widget = self._make_widget()
        value = ["demo.Product", "demo.Customer"]
        context = widget.get_context("allowed_models", value, {})
        assert context["current_values"] == value

    def test_get_context_with_json_string_value(self):
        widget = self._make_widget()
        value = json.dumps(["demo.Product"])
        context = widget.get_context("allowed_models", value, {})
        assert context["current_values"] == ["demo.Product"]

    def test_get_context_with_invalid_json_string(self):
        widget = self._make_widget()
        context = widget.get_context("allowed_models", "not-valid-json", {})
        assert context["current_values"] == []

    def test_get_context_all_models_json_is_valid_json(self):
        widget = self._make_widget()
        context = widget.get_context("allowed_models", None, {})
        parsed = json.loads(context["all_models_json"])
        assert isinstance(parsed, dict)

    def test_get_context_all_models_contains_app_labels(self):
        widget = self._make_widget()
        context = widget.get_context("allowed_models", None, {})
        parsed = json.loads(context["all_models_json"])
        assert "demo" in parsed or len(parsed) > 0

    def test_get_context_name_in_context(self):
        widget = self._make_widget()
        context = widget.get_context("my_field", None, {})
        assert context["name"] == "my_field"

    def test_render_returns_string(self):
        widget = self._make_widget()
        result = widget.render("allowed_models", None)
        assert isinstance(result, str)

    def test_media_has_js(self):
        widget = self._make_widget()
        media = widget.media
        assert len(media._js) > 0

    def test_media_has_css(self):
        widget = self._make_widget()
        media = widget.media
        assert len(media._css) > 0
