"""
tests/test_builtin_admins.py — SnapAdmin's own admins without a SnapModel (#EXT3i).

The error-log, audit-log and API-token admins were registered only inside
``SnapModel.register_all_admins()``. A project that uses SnapAdmin's middleware
or tokens without declaring a SnapModel never calls it — so the error monitor
recorded failures no admin page could show, and nothing said so. Every test and
the demo call ``register_all_admins()``; that adopter shape was never exercised.

``snapadmin.admin.register_builtin_admins()`` registers them on its own, and
``snapadmin.W031`` names the gap. The class guard: every concrete model of the
package either gets an admin from it or is listed below with the reason it has
none — a new package model cannot ship without an admin unnoticed.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.test import override_settings

from snapadmin.admin import register_builtin_admins
from snapadmin.checks import check_builtin_admins_registered

#: Package models deliberately offered no admin, with the reason.
NO_ADMIN_BY_DESIGN = {
    "SnapExportJob": "an operational record, created and read through the export endpoints and commands",
    "SnapImportJob": "an operational record, created and read through snapadmin_import and its job",
    "SnapReindexJob": "an operational record, created and read through the reindex endpoints and command",
}

MONITOR = "snapadmin.middleware.SnapErrorMonitorMiddleware"


@pytest.fixture
def site():
    """A throwaway AdminSite, emptied afterwards: snapadmin.W006 looks for every live
    AdminSite carrying a registry, so a leftover one would warn in an unrelated test."""
    import gc

    isolated = AdminSite(name="isolated")
    yield isolated
    isolated._registry.clear()
    del isolated
    gc.collect()


class TestRegisterBuiltinAdmins:
    @override_settings(SNAPADMIN_TOKEN_ADMIN_ENABLED=True)
    def test_it_registers_the_error_log_the_audit_log_and_the_tokens(self, site):
        from snapadmin.models import APIToken, ErrorEvent, SnapadminAuditLog

        register_builtin_admins(site)

        assert set(site._registry) == {APIToken, ErrorEvent, SnapadminAuditLog}

    @override_settings(SNAPADMIN_TOKEN_ADMIN_ENABLED=False)
    def test_the_token_admin_follows_its_gate(self, site):
        from snapadmin.models import APIToken

        register_builtin_admins(site)

        assert APIToken not in site._registry

    def test_calling_it_twice_is_harmless(self, site):
        register_builtin_admins(site)
        first = dict(site._registry)
        register_builtin_admins(site)

        assert site._registry.keys() == first.keys()

    def test_a_project_s_own_admin_for_one_of_them_is_kept(self, site):
        from snapadmin.models import ErrorEvent

        own = type("ProjectErrorAdmin", (admin.ModelAdmin,), {})
        site.register(ErrorEvent, own)

        register_builtin_admins(site)

        assert type(site._registry[ErrorEvent]) is own


class TestEveryPackageModelHasAnAdminOrAReason:
    @override_settings(SNAPADMIN_TOKEN_ADMIN_ENABLED=True)
    def test_the_class_guard(self, site):
        register_builtin_admins(site)

        unaccounted = sorted(
            model.__name__
            for model in apps.get_app_config("snapadmin").get_models()
            if model not in site._registry and model.__name__ not in NO_ADMIN_BY_DESIGN
        )
        stale = sorted(
            name
            for name in NO_ADMIN_BY_DESIGN
            if name not in {model.__name__ for model in apps.get_app_config("snapadmin").get_models()}
        )

        assert not unaccounted, (
            f"review: package model(s) {unaccounted} get no admin from register_builtin_admins() "
            "and are not in NO_ADMIN_BY_DESIGN — register one or state why there is none"
        )
        assert not stale, f"review: NO_ADMIN_BY_DESIGN names models that no longer exist: {stale}"


class TestTheMissingAdminCheck:
    def _without_error_admin(self, monkeypatch):
        from snapadmin.models import ErrorEvent

        monkeypatch.setattr(
            admin.site, "_registry", {m: a for m, a in admin.site._registry.items() if m is not ErrorEvent}
        )

    def test_monitoring_with_no_admin_to_show_it_warns(self, settings, monkeypatch):
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, MONITOR]
        self._without_error_admin(monkeypatch)

        messages = check_builtin_admins_registered(None)

        assert [m.id for m in messages] == ["snapadmin.W031"]
        assert "register_builtin_admins()" in messages[0].hint

    def test_no_warning_once_the_admin_is_registered(self, settings):
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, MONITOR]

        assert check_builtin_admins_registered(None) == []

    def test_no_warning_without_the_middleware(self, settings, monkeypatch):
        settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if m != MONITOR]
        self._without_error_admin(monkeypatch)

        assert check_builtin_admins_registered(None) == []

    def test_no_warning_while_monitoring_is_switched_off(self, settings, monkeypatch):
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, MONITOR]
        settings.SNAPADMIN_ERROR_MONITOR_ENABLED = False
        self._without_error_admin(monkeypatch)

        assert check_builtin_admins_registered(None) == []
