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
        context["name"] = name
        return context

    def render(self, name, value, attrs=None, renderer=None):
        context = self.get_context(name, value, attrs)
        return mark_safe(render_to_string(self.template_name, context))

    class Media:
        js = ["snapadmin/js/model_selector.js"]
        css = {
            "all": ["snapadmin/css/admin.css"]
        }
