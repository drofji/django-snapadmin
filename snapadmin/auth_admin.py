"""
snapadmin/auth_admin.py

Give Django's built-in ``auth`` admin the same theme as the rest of the site.

``django-unfold`` is an optional theme (``pip install django-snapadmin[theme]``).
It ships template *overrides* for ``django.contrib.auth`` — including
``auth/widgets/read_only_password_hash.html`` — but the matching admin classes and
forms are something the project is expected to wire up itself. A project that
installs the theme and leaves ``django.contrib.auth`` registered with its stock
``UserAdmin``/``GroupAdmin`` therefore gets Unfold's templates driven by Django's
forms, and the mismatch is not cosmetic:

* **The password row renders empty and offers no way to change the password.**
  Unfold's default ``read_only_password_hash.html`` is written for Django < 5.1
  and reads a ``summary`` context variable that modern Django no longer provides,
  so the widget body comes out blank and the "Reset password" button — which lives
  in Unfold's ``…_new.html`` variant — is never rendered. Unfold switches to that
  variant from its own ``UserChangeForm``, which stock ``UserAdmin`` never uses.
* The many-to-many group/permission pickers and the add-user flow miss Unfold's
  widget styling for the same reason.

This module re-registers those two admins as ``(DjangoAdmin, unfold.ModelAdmin)``
with Unfold's ``UserChangeForm`` / ``UserCreationForm`` / ``AdminPasswordChangeForm``
— the wiring Unfold documents — so the built-in auth screens work and match. It is
**conservative**: it only replaces a registration whose admin class is *exactly*
Django's stock one, so a project that has customised its user admin is never
touched. Set ``SNAPADMIN_THEME_AUTH_ADMIN = False`` to skip it entirely.

Runs from ``SnapAdminConfig.ready()``, like :mod:`snapadmin.extra_settings_admin`.
Admin autodiscovery is itself triggered from ``django.contrib.admin``'s ``ready()``,
so whether it has run by the time this fires depends on INSTALLED_APPS order — the
upgrade therefore imports ``django.contrib.auth.admin`` itself rather than assuming
the registrations are already in place.
"""

from __future__ import annotations


def apply_unfold_auth_admin() -> list[str]:
    """Re-register the stock ``User``/``Group`` admins with Unfold's theme.

    Returns the labels of the models whose registration was upgraded — empty when
    there was nothing to do: the theme isn't installed, the feature is switched
    off, the model isn't registered, or its admin is already themed/customised.
    Idempotent; safe to call more than once.
    """
    from django.apps import apps

    from snapadmin.conf import get_setting

    if not get_setting("SNAPADMIN_THEME_AUTH_ADMIN", True):
        return []
    if not apps.is_installed("unfold") or not apps.is_installed("django.contrib.auth"):
        return []

    try:
        from unfold.admin import ModelAdmin as UnfoldModelAdmin
        from unfold.forms import (
            AdminPasswordChangeForm,
            UserChangeForm,
            UserCreationForm,
        )
    except ImportError:  # pragma: no cover - unreachable once is_installed() passed
        return []

    from django.contrib import admin
    from django.contrib.auth import get_user_model
    # Importing this module is what registers User/Group on the admin site. Admin
    # autodiscovery normally does it, but that runs inside
    # ``django.contrib.admin``'s own ``ready()`` — so it has only happened by now
    # if ``snapadmin`` is listed *after* it in INSTALLED_APPS. Importing it here
    # makes the upgrade independent of app ordering; the import is cached, so a
    # later autodiscovery is a no-op and cannot re-register over the themed class.
    from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
    from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
    from django.contrib.auth.models import Group

    # Django's own admin classes come first in the MRO — this is the composition
    # order Unfold documents. UserAdmin carries the password-change URLs, the
    # add-form flow and the fieldsets; Unfold contributes templates and widgets.
    upgraded = []
    for model, base_admin, extra_attrs in (
        (get_user_model(), BaseUserAdmin, {
            "form": UserChangeForm,
            "add_form": UserCreationForm,
            "change_password_form": AdminPasswordChangeForm,
        }),
        (Group, BaseGroupAdmin, {}),
    ):
        registered = admin.site._registry.get(model)
        # `is` and not `isinstance`: a project that subclassed the stock admin has
        # made a deliberate choice, and replacing it would silently drop that work.
        if registered is None or type(registered) is not base_admin:
            continue

        themed = type(
            f"Unfold{base_admin.__name__}",
            (base_admin, UnfoldModelAdmin),
            {"__module__": __name__, **extra_attrs},
        )
        admin.site.unregister(model)
        admin.site.register(model, themed)
        upgraded.append(model._meta.label)

    return upgraded
