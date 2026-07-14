from django import forms
from django.apps import apps
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.utils.encoding import force_str
import json

class SmartModelSelectorWidget(forms.Widget):
    template_name = "snapadmin/widgets/smart_model_selector.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)

        # Prepare data for JS
        all_models = {}
        for app_config in apps.get_app_configs():
            models = []
            for model in app_config.get_models():
                models.append({
                    "label": force_str(model._meta.verbose_name).capitalize(),
                    "value": f"{app_config.label}.{model.__name__}"
                })
            if models:
                all_models[app_config.label] = {
                    "label": force_str(app_config.verbose_name),
                    "models": models
                }

        current_values = []
        if value:
            if isinstance(value, str):
                try:
                    current_values = json.loads(value)
                except json.JSONDecodeError:
                    current_values = []
            elif isinstance(value, list):
                current_values = value

        context["all_models_json"] = json.dumps(all_models)
        context["current_values"] = current_values
        context["current_values_json"] = json.dumps(current_values)
        context["name"] = name
        return context

    def render(self, name, value, attrs=None, renderer=None):
        context = self.get_context(name, value, attrs)
        return mark_safe(render_to_string(self.template_name, context))

    def value_omitted_from_data(self, data, files, name):
        # An empty/missing/unparseable submission is treated as "no change" rather
        # than "clear to []", because [] means "unrestricted, fall back to the
        # user's Django permissions" (see APIToken.can_access_model). If the page's
        # JS fails to repopulate the hidden input (CSP block, JS error, a stray
        # character in some model's verbose_name breaking JSON.parse), the browser
        # would otherwise submit a literal '[]' and silently widen a restricted
        # token's scope to everything the owning user can touch. Returning True
        # here makes Django's construct_instance() skip the field entirely, so the
        # token keeps its existing allowed_models. The tradeoff: this widget can
        # never be used to deliberately clear allowed_models back to [] — only to
        # widen it by adding entries or narrow it to a non-empty subset. A scope
        # that silently self-widens on save is a worse failure mode than a scope
        # that can't be fully cleared from one screen.
        raw = data.get(name)
        if raw in (None, ""):
            return True
        if isinstance(raw, (list, dict)):
            parsed = raw
        else:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return True
        return parsed == []

    class Media:
        js = ["snapadmin/js/model_selector.js"]
        css = {
            "all": ["snapadmin/css/admin.css"]
        }
