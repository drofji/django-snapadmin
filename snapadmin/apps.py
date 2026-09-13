"""
Django ``AppConfig`` for SnapAdmin.

``SnapAdminConfig.ready()`` wires the package's startup work: connecting the
``post_migrate`` hook that ensures Elasticsearch indices/mappings, connecting the
``post_delete`` receiver that keeps those indices in step with deletes that never
call ``Model.delete()`` (``QuerySet.delete()``, cascades), installing the
nested-app shim, configuring optional multi-shard/read-replica database routing
(``snapadmin.sharding``), registering the ``snapadmin.*`` system checks, and
applying the optional Unfold re-styling of the extra_settings admin and of
Django's built-in ``auth`` admin. Every optional integration it touches is
guarded so a missing/half-installed package (or a sharding misconfiguration)
degrades rather than crashing ``django.setup()``.
"""

from django.apps import AppConfig, apps
from django.db.models.signals import post_migrate


def sync_es_mappings(sender, **kwargs):
    """
    Ensure Elasticsearch indices and mappings are up-to-date for all SnapModels.

    Only models that actually carry SnapModel's Elasticsearch machinery have an
    index to maintain; a plain model registered with ``@snap_model`` gets no ES
    mirroring at all, so it is skipped rather than failing ``post_migrate``.
    """
    from snapadmin.registry import is_registered

    for model in apps.get_models():
        if is_registered(model) and hasattr(model, "_ensure_es_index_and_mapping"):
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

        from snapadmin.models import connect_es_delete_receivers
        connect_es_delete_receivers()

        install_nested_apps()

        from snapadmin.sharding.registration import configure_sharding
        configure_sharding()

        from snapadmin.checks import register_checks
        register_checks()

        from snapadmin.extra_settings_admin import apply_unfold_styling
        apply_unfold_styling()

        from snapadmin.auth_admin import apply_unfold_auth_admin
        apply_unfold_auth_admin()
