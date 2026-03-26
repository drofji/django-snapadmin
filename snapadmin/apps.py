from django.apps import AppConfig, apps
from django.db.models.signals import post_migrate


def sync_es_mappings(sender, **kwargs):
    """
    Ensure Elasticsearch indices and mappings are up-to-date for all SnapModels.
    """
    from snapadmin.models import SnapModel

    for model in apps.get_models():
        if issubclass(model, SnapModel) and model is not SnapModel:
            model._ensure_es_index_and_mapping()


class SnapAdminConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'snapadmin'
    verbose_name = "Snap Admin"

    def ready(self):
        post_migrate.connect(sync_es_mappings, sender=self)

        # Ensure global admin CSS/JS for all models
        from django.contrib import admin
        from django.conf import settings

        original_register = admin.AdminSite.register

        def snap_register(self, model_or_iterable, admin_class=None, **options):
            BASE_JS = ["admin/js/vendor/jquery/jquery.js", "admin/js/jquery.init.js", "snapadmin/js/jquery_bridge.js", "snapadmin/js/select2.min.js", "snapadmin/js/admin.js"]
            BASE_CSS = ["snapadmin/css/select2.min.css", "snapadmin/css/admin.css"]

            if getattr(settings, 'SNAPADMIN_OFFLINE_ENABLED', False):
                BASE_JS.append("snapadmin/js/offline.js")

            # If it's a SnapModel, it already handles its own registration
            from snapadmin.models import SnapModel
            if isinstance(model_or_iterable, type) and issubclass(model_or_iterable, SnapModel):
                return original_register(self, model_or_iterable, admin_class, **options)

            if admin_class is None:
                from django.contrib.admin import ModelAdmin
                admin_class = ModelAdmin

            # Add Media class to admin_class if not present
            if not hasattr(admin_class, "Media"):
                class Media:
                    js = BASE_JS
                    css = {"all": BASE_CSS}
                admin_class.Media = Media
            else:
                # Merge with existing Media
                if not hasattr(admin_class.Media, "js"):
                    admin_class.Media.js = BASE_JS
                else:
                    admin_class.Media.js = list(dict.fromkeys(list(admin_class.Media.js) + BASE_JS))

                if not hasattr(admin_class.Media, "css"):
                    admin_class.Media.css = {"all": BASE_CSS}
                else:
                    if "all" not in admin_class.Media.css:
                        admin_class.Media.css["all"] = BASE_CSS
                    else:
                        admin_class.Media.css["all"] = list(dict.fromkeys(list(admin_class.Media.css["all"]) + BASE_CSS))

            return original_register(self, model_or_iterable, admin_class, **options)

        admin.AdminSite.register = snap_register
