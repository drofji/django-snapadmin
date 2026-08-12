"""
tests/test_auth_admin.py — theming Django's built-in auth admin (#UX7)

``django-unfold`` ships template overrides for ``django.contrib.auth`` but not
the admin classes that drive them. A project that installs the optional theme
and leaves ``User``/``Group`` on their stock admins therefore renders Unfold's
templates against Django's forms — and the mismatch broke the password row
outright: Unfold's default ``read_only_password_hash.html`` is written for
Django < 5.1 and reads a ``summary`` context variable modern Django no longer
provides, so the widget body came out empty **and** the "Reset password" button
(which lives in Unfold's ``…_new.html`` variant, selected from Unfold's own
``UserChangeForm``) was never rendered. There was no way to change a password
from the UI at all.

``snapadmin.auth_admin`` re-registers the two stock admins with Unfold's theme
and forms, and only ever touches a registration that is *exactly* Django's.
"""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.test import override_settings

from snapadmin.admin import UNFOLD_INSTALLED
from snapadmin.auth_admin import apply_unfold_auth_admin

pytestmark = pytest.mark.skipif(not UNFOLD_INSTALLED, reason="Unfold not installed")

User = get_user_model()


@pytest.fixture
def restore_registry():
    """Snapshot and restore the User/Group admin registrations."""
    saved = {m: admin.site._registry[m] for m in (User, Group) if m in admin.site._registry}
    yield
    for model, model_admin in saved.items():
        admin.site._registry[model] = model_admin


# ── registration ─────────────────────────────────────────────────────────────

class TestRegistration:
    def test_user_admin_is_themed_at_startup(self):
        model_admin = admin.site._registry[User]
        from unfold.admin import ModelAdmin as UnfoldModelAdmin

        assert isinstance(model_admin, BaseUserAdmin)      # keeps Django's behaviour
        assert isinstance(model_admin, UnfoldModelAdmin)   # gains Unfold's templates

    def test_group_admin_is_themed_at_startup(self):
        from unfold.admin import ModelAdmin as UnfoldModelAdmin

        assert isinstance(admin.site._registry[Group], UnfoldModelAdmin)

    def test_user_admin_uses_unfold_forms(self):
        from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

        model_admin = admin.site._registry[User]
        assert model_admin.form is UserChangeForm
        assert model_admin.add_form is UserCreationForm
        assert model_admin.change_password_form is AdminPasswordChangeForm

    def test_idempotent(self, restore_registry):
        """Already themed → nothing to do, and nothing is re-wrapped."""
        before = type(admin.site._registry[User])
        assert apply_unfold_auth_admin() == []
        assert type(admin.site._registry[User]) is before

    def test_reapplies_to_a_stock_registration(self, restore_registry):
        admin.site._registry[User] = BaseUserAdmin(User, admin.site)
        admin.site._registry[Group] = BaseGroupAdmin(Group, admin.site)

        upgraded = apply_unfold_auth_admin()

        assert upgraded == [User._meta.label, Group._meta.label]
        assert type(admin.site._registry[User]) is not BaseUserAdmin

    def test_works_before_admin_autodiscovery_has_run(self, restore_registry):
        """INSTALLED_APPS may list ``snapadmin`` before ``django.contrib.admin``.

        Autodiscovery runs from that app's own ``ready()``, so it has not
        necessarily happened when ours fires. Simulated by dropping both the
        registrations and the cached module: importing ``django.contrib.auth.admin``
        is what registers User/Group, and the upgrade must do that itself rather
        than find an empty registry and silently no-op.
        """
        import sys

        cached = sys.modules.pop("django.contrib.auth.admin")
        admin.site._registry.pop(User, None)
        admin.site._registry.pop(Group, None)
        try:
            upgraded = apply_unfold_auth_admin()
        finally:
            sys.modules["django.contrib.auth.admin"] = cached

        from unfold.admin import ModelAdmin as UnfoldModelAdmin

        assert upgraded == [User._meta.label, Group._meta.label]
        assert isinstance(admin.site._registry[User], UnfoldModelAdmin)

    def test_never_replaces_a_project_customised_admin(self, restore_registry):
        """A subclass is a deliberate choice — replacing it would drop that work."""
        class MyUserAdmin(BaseUserAdmin):
            pass

        custom = MyUserAdmin(User, admin.site)
        admin.site._registry[User] = custom

        assert User._meta.label not in apply_unfold_auth_admin()
        assert admin.site._registry[User] is custom

    def test_skips_an_unregistered_model(self, restore_registry):
        admin.site._registry.pop(Group, None)
        admin.site._registry[User] = BaseUserAdmin(User, admin.site)

        assert apply_unfold_auth_admin() == [User._meta.label]

    @override_settings(SNAPADMIN_THEME_AUTH_ADMIN=False)
    def test_opt_out_setting(self, restore_registry):
        admin.site._registry[User] = BaseUserAdmin(User, admin.site)
        assert apply_unfold_auth_admin() == []
        assert type(admin.site._registry[User]) is BaseUserAdmin

    def test_no_op_without_the_theme(self, monkeypatch, restore_registry):
        from django.apps import apps as django_apps

        real = django_apps.is_installed
        monkeypatch.setattr(
            django_apps, "is_installed",
            lambda name: False if name == "unfold" else real(name),
        )
        admin.site._registry[User] = BaseUserAdmin(User, admin.site)
        assert apply_unfold_auth_admin() == []

    def test_no_op_without_django_auth(self, monkeypatch, restore_registry):
        from django.apps import apps as django_apps

        real = django_apps.is_installed
        monkeypatch.setattr(
            django_apps, "is_installed",
            lambda name: False if name == "django.contrib.auth" else real(name),
        )
        assert apply_unfold_auth_admin() == []


# ── the rendered change form ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestPasswordRowIsUsable:
    def test_password_hash_summary_is_rendered(self, admin_client, admin_user):
        html = admin_client.get(f"/admin/auth/user/{admin_user.pk}/change/").content.decode()
        # Django's render_password_as_hash output — the row used to be empty.
        assert "algorithm" in html
        assert "<strong>hash</strong>" in html

    def test_change_password_link_is_present(self, admin_client, admin_user):
        html = admin_client.get(f"/admin/auth/user/{admin_user.pk}/change/").content.decode()
        assert 'href="../password/"' in html

    def test_the_password_change_form_actually_works(self, admin_client, admin_user):
        url = f"/admin/auth/user/{admin_user.pk}/password/"
        assert admin_client.get(url).status_code == 200

        response = admin_client.post(url, {
            "password1": "a-brand-new-passphrase-42",
            "password2": "a-brand-new-passphrase-42",
        })
        assert response.status_code == 302
        admin_user.refresh_from_db()
        assert admin_user.check_password("a-brand-new-passphrase-42")

    def test_group_picker_renders(self, admin_client):
        html = admin_client.get("/admin/auth/group/add/").content.decode()
        assert 'name="permissions"' in html
        assert "selectfilter" in html          # Unfold's themed m2m picker

    def test_add_user_form_renders(self, admin_client):
        assert admin_client.get("/admin/auth/user/add/").status_code == 200
