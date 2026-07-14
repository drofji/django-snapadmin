"""
tests/test_widget_security.py

Security regression tests for SmartModelSelectorWidget's fail-open bug
(roadmap item #SEC8): the hidden input used to hardcode value='[]', so if the
client-side JS never ran (CSP block, JS error, a bad JSON.parse), submitting
the form silently wiped APIToken.allowed_models to [] — which
APIToken.can_access_model() treats as "unrestricted, fall back to the user's
Django permissions". These tests cover both mitigations:

1. get_context()/the rendered template now emit the real current value in the
   hidden input, not a hardcoded [].
2. value_omitted_from_data() treats a missing/empty/unparseable submission as
   "no change" so ModelForm.construct_instance() leaves the DB value alone.
"""

import json
import pytest
from django.template.loader import render_to_string
from snapadmin.widgets import SmartModelSelectorWidget


class TestCurrentValuesJson:
    def _make_widget(self):
        return SmartModelSelectorWidget()

    def test_context_with_list_value(self):
        widget = self._make_widget()
        value = ["demo.Product", "demo.Customer"]
        context = widget.get_context("allowed_models", value, {})
        assert json.loads(context["current_values_json"]) == value

    def test_context_with_json_string_value(self):
        widget = self._make_widget()
        value = json.dumps(["demo.Product"])
        context = widget.get_context("allowed_models", value, {})
        assert json.loads(context["current_values_json"]) == ["demo.Product"]

    def test_context_with_no_value_is_empty_list(self):
        widget = self._make_widget()
        context = widget.get_context("allowed_models", None, {})
        assert json.loads(context["current_values_json"]) == []

    def test_rendered_hidden_input_contains_real_value_not_hardcoded_empty(self):
        widget = self._make_widget()
        value = ["demo.Product", "demo.Customer"]
        html = widget.render("allowed_models", value)
        assert (
            "id=\"hidden-input-allowed_models\" value='[\"demo.Product\", \"demo.Customer\"]'"
            in html
        )
        assert "id=\"hidden-input-allowed_models\" value='[]'" not in html

    def test_rendered_hidden_input_still_empty_when_value_is_empty(self):
        widget = self._make_widget()
        html = widget.render("allowed_models", None)
        assert "id=\"hidden-input-allowed_models\" value='[]'" in html

    def test_template_directly_uses_current_values_json(self):
        context = {
            "name": "allowed_models",
            "all_models_json": "{}",
            "current_values": ["demo.Product"],
            "current_values_json": json.dumps(["demo.Product"]),
        }
        html = render_to_string(
            "snapadmin/widgets/smart_model_selector.html", context
        )
        assert "value='[\"demo.Product\"]'" in html


class TestValueOmittedFromData:
    def _make_widget(self):
        return SmartModelSelectorWidget()

    def test_true_when_key_absent(self):
        widget = self._make_widget()
        assert widget.value_omitted_from_data({}, {}, "allowed_models") is True

    def test_true_when_empty_string(self):
        widget = self._make_widget()
        data = {"allowed_models": ""}
        assert widget.value_omitted_from_data(data, {}, "allowed_models") is True

    def test_true_when_empty_json_list(self):
        widget = self._make_widget()
        data = {"allowed_models": "[]"}
        assert widget.value_omitted_from_data(data, {}, "allowed_models") is True

    def test_true_when_invalid_json(self):
        widget = self._make_widget()
        data = {"allowed_models": "not-valid-json"}
        assert widget.value_omitted_from_data(data, {}, "allowed_models") is True

    def test_true_when_value_is_none(self):
        widget = self._make_widget()
        data = {"allowed_models": None}
        assert widget.value_omitted_from_data(data, {}, "allowed_models") is True

    def test_false_when_non_empty_json_list(self):
        widget = self._make_widget()
        data = {"allowed_models": '["demo.Product"]'}
        assert widget.value_omitted_from_data(data, {}, "allowed_models") is False

    def test_false_when_value_already_a_list(self):
        widget = self._make_widget()
        data = {"allowed_models": ["demo.Product"]}
        assert widget.value_omitted_from_data(data, {}, "allowed_models") is False

    def test_true_when_value_already_an_empty_list(self):
        widget = self._make_widget()
        data = {"allowed_models": []}
        assert widget.value_omitted_from_data(data, {}, "allowed_models") is True


@pytest.mark.django_db
class TestAdminFormNoOpSubmission:
    """
    Integration coverage: simulate the no-JS / broken-JS case where the
    browser submits the hardcoded-empty hidden input for a token that already
    has a restricted scope. The save must leave allowed_models untouched
    rather than silently widening the token.
    """

    def test_submitting_empty_allowed_models_leaves_existing_scope_untouched(
        self, admin_client, restricted_token
    ):
        from django.urls import reverse

        assert restricted_token.allowed_models == ["demo.Product"]
        url = reverse("admin:snapadmin_apitoken_change", args=[restricted_token.pk])

        data = {
            "token_name": restricted_token.token_name,
            "user": restricted_token.user_id,
            "is_active": "on",
            "allowed_models": "[]",
            "_save": "Save",
        }
        response = admin_client.post(url, data)

        assert response.status_code == 302
        restricted_token.refresh_from_db()
        assert restricted_token.allowed_models == ["demo.Product"]

    def test_submitting_non_empty_allowed_models_updates_scope(
        self, admin_client, restricted_token
    ):
        from django.urls import reverse

        url = reverse("admin:snapadmin_apitoken_change", args=[restricted_token.pk])

        data = {
            "token_name": restricted_token.token_name,
            "user": restricted_token.user_id,
            "is_active": "on",
            "allowed_models": '["demo.Customer"]',
            "_save": "Save",
        }
        response = admin_client.post(url, data)

        assert response.status_code == 302
        restricted_token.refresh_from_db()
        assert restricted_token.allowed_models == ["demo.Customer"]
