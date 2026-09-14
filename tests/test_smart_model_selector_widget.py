"""
tests/test_smart_model_selector_widget.py

``SmartModelSelectorWidget`` — the app/model picker behind ``allowed_models``.

The widget's whole job is to hand its template a JSON description of every
installed app and model plus the currently selected values, so the tests pin
that structure rather than checking that a key exists or that the rendered
output happens to be a string. The fail-open behaviour of
``value_omitted_from_data`` has its own suite in ``test_widget_security.py``.
(#QA1b: this file was previously named for the coverage metric.)
"""

import json

import pytest
from django.apps import apps

from snapadmin.widgets import SmartModelSelectorWidget


@pytest.fixture
def widget():
    return SmartModelSelectorWidget()


class TestCurrentValues:
    def test_no_value_selects_nothing(self, widget):
        context = widget.get_context("allowed_models", None, {})
        assert context["current_values"] == []
        assert context["current_values_json"] == "[]"

    def test_a_list_is_taken_as_the_selection(self, widget):
        selected = ["demo.Product", "demo.Customer"]
        context = widget.get_context("allowed_models", selected, {})
        assert context["current_values"] == selected
        assert json.loads(context["current_values_json"]) == selected

    def test_a_json_string_is_parsed_into_the_selection(self, widget):
        context = widget.get_context("allowed_models", json.dumps(["demo.Product"]), {})
        assert context["current_values"] == ["demo.Product"]

    def test_an_unparseable_string_selects_nothing_rather_than_raising(self, widget):
        context = widget.get_context("allowed_models", "not-valid-json", {})
        assert context["current_values"] == []

    def test_a_value_of_an_unexpected_type_selects_nothing(self, widget):
        # Neither a list nor a string: fall through to the empty selection.
        context = widget.get_context("allowed_models", {"demo": ["Product"]}, {})
        assert context["current_values"] == []

    def test_the_field_name_reaches_the_template(self, widget):
        assert widget.get_context("my_field", None, {})["name"] == "my_field"


class TestAllModelsPayload:
    def test_every_installed_app_that_has_models_is_described(self, widget):
        payload = json.loads(widget.get_context("allowed_models", None, {})["all_models_json"])

        expected_labels = {
            app.label for app in apps.get_app_configs() if list(app.get_models())
        }
        assert set(payload) == expected_labels
        # Apps with no models are left out entirely, not included empty.
        assert all(entry["models"] for entry in payload.values())

    def test_an_app_entry_carries_its_verbose_name_and_every_one_of_its_models(self, widget):
        payload = json.loads(widget.get_context("allowed_models", None, {})["all_models_json"])

        demo = payload["demo"]
        assert demo["label"] == str(apps.get_app_config("demo").verbose_name)
        assert {model["value"] for model in demo["models"]} == {
            f"demo.{model.__name__}" for model in apps.get_app_config("demo").get_models()
        }

    def test_a_model_entry_pairs_a_capitalised_label_with_its_dotted_value(self, widget):
        payload = json.loads(widget.get_context("allowed_models", None, {})["all_models_json"])

        product = next(
            entry for entry in payload["demo"]["models"] if entry["value"] == "demo.Product"
        )
        assert product == {"label": "Product", "value": "demo.Product"}


class TestRendering:
    def test_renders_the_picker_template_with_the_selection_embedded(self, widget):
        html = widget.render("allowed_models", ["demo.Product"])

        assert 'name="allowed_models"' in html
        assert "demo.Product" in html

    def test_media_declares_the_picker_script_and_the_admin_stylesheet(self, widget):
        assert list(widget.media._js) == ["snapadmin/js/model_selector.js"]
        assert list(widget.media._css["all"]) == ["snapadmin/css/admin.css"]
