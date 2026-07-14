"""
snapadmin/extra_settings_admin.py

Re-skin the optional ``django-extra-settings`` admin with Unfold styling.

``django-extra-settings`` is an optional integration
(``pip install django-snapadmin[extra-settings]``). When installed it registers
its own ``Setting`` admin using a plain ``django.contrib.admin.ModelAdmin``,
which renders unstyled next to the rest of SnapAdmin's Unfold-themed site. This
module swaps that registration for an Unfold-derived admin that keeps all of
extra_settings' own configuration (``list_display``, ``search_fields``,
``get_fieldsets``, custom ``Media``, ...) but inherits Unfold's templates and
styling on top.

**Why this runs from ``SnapAdminConfig.ready()`` and not from an ``admin.py`` at
import time — and why the ordering matters.** Django's admin autodiscovery
imports every installed app's ``admin.py`` in ``INSTALLED_APPS`` order, so doing
the override inside ``snapadmin/admin.py`` would be fragile: if ``extra_settings``
is listed *after* ``snapadmin`` (as it is in this project), its own
``admin.site.register(Setting, ...)`` runs *later* and silently overwrites ours.
Running from ``ready()`` sidesteps the ordering entirely: by the time any app's
``ready()`` fires, ``django.contrib.admin``'s ``AppConfig.ready()`` (listed near
the top of ``INSTALLED_APPS`` by Django convention, and always before
``snapadmin`` here) has already triggered autodiscovery and imported *every*
app's ``admin.py`` — including extra_settings'. So the ``Setting`` admin is
guaranteed registered when ``apply_unfold_styling()`` runs, regardless of where
``extra_settings`` itself sits relative to ``snapadmin``. The only requirement is
that ``django.contrib.admin`` precede ``snapadmin`` in ``INSTALLED_APPS``, which
Django's own project template guarantees.

The override also copes with ``EXTRA_SETTINGS_ADMIN_APP``: when it re-homes the
``Setting`` admin into another app, extra_settings unregisters the base
``Setting`` and registers a *proxy* of it instead. This module therefore restyles
every registered model that is ``Setting`` or a proxy subclass of it, not just
the base model.
"""

from __future__ import annotations


def apply_unfold_styling() -> bool:
    """Restyle any registered ``Setting`` admin with Unfold, in place.

    Returns ``True`` if at least one admin registration was upgraded, ``False``
    if there was nothing to do — extra_settings isn't installed, no ``Setting``
    admin is registered, or every one is already Unfold-styled. Safe and
    idempotent: calling it more than once never double-wraps an admin.
    """
    try:
        from extra_settings.models import Setting
    except ImportError:
        return False

    from django.contrib import admin
    from unfold.admin import ModelAdmin as UnfoldModelAdmin

    changed = False
    # A snapshot of the items — we mutate the registry while iterating.
    for model, model_admin in list(admin.site._registry.items()):
        if not issubclass(model, Setting):
            continue

        existing_admin_class = type(model_admin)
        if issubclass(existing_admin_class, UnfoldModelAdmin):
            continue  # already Unfold-styled — leave it alone

        # Compose: UnfoldModelAdmin first so its templates/forms/media win in the
        # MRO, the existing class second so extra_settings' own list_display,
        # search_fields, get_fieldsets, Media, etc. are preserved.
        styled_admin_class = type(
            f"UnfoldStyled{existing_admin_class.__name__}",
            (UnfoldModelAdmin, existing_admin_class),
            {"__module__": __name__},
        )
        admin.site.unregister(model)
        admin.site.register(model, styled_admin_class)
        changed = True

    return changed
