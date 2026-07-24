"""
Django ``AppConfig`` for SnapAdmin.

``SnapAdminConfig.ready()`` wires the package's startup work: connecting the
``post_migrate`` hook that ensures Elasticsearch indices/mappings, installing the
nested-app shim, registering the ``snapadmin.*`` system checks, and applying the
optional Unfold re-styling of the extra_settings admin. Every optional integration
it touches is guarded so a missing/half-installed package degrades rather than
crashing ``django.setup()``.
"""

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


def install_nested_apps():
    """Wrap ``admin.site.get_app_list`` to regroup the index per SNAPADMIN_NESTED_APPS.

    No-op unless nesting/hide/rename settings are configured, and idempotent so a
    double ``ready()`` (autoreload, test reloads) can't stack wrappers.

    This only patches ``django.contrib.admin.site`` — the default ``AdminSite``.
    A project serving ``/admin/`` from a *different* ``AdminSite`` instance won't
    see these settings applied there; ``snapadmin.checks.check_nesting_active_site``
    (``snapadmin.W006``) warns when that mismatch is detectable at check time.
    """
    from django.contrib import admin
    from snapadmin.nesting import apply_nested_apps, nesting_configured

    if not nesting_configured():
        return

    site = admin.site
    if getattr(site, "_snap_nested_wrapped", False):
        return

    original = site.get_app_list

    def patched(request, app_label=None):
        return apply_nested_apps(original(request, app_label))

    site.get_app_list = patched
    site._snap_nested_wrapped = True


class SnapAdminConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'snapadmin'
    verbose_name = "Snap Admin"

    def ready(self):
        post_migrate.connect(sync_es_mappings, sender=self)
        install_nested_apps()

        from snapadmin.checks import register_checks
        register_checks()

        from snapadmin.extra_settings_admin import apply_unfold_styling
        apply_unfold_styling()
