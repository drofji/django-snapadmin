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
