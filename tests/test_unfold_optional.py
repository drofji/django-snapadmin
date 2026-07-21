"""
tests/test_unfold_optional.py

``django-unfold`` is an optional theme (``pip install django-snapadmin[theme]``).
SnapAdmin resolves its admin base class lazily: it uses Unfold's themed
``ModelAdmin``/widgets/decorators when Unfold is installed *and* enabled in
INSTALLED_APPS, and falls back to Django's built-in admin otherwise. These tests
exercise that stock-admin fallback so it is genuinely covered — the branch is
``# pragma``-free and must render correctly for installs without the theme.

Testing the *import-time* fallback (``snapadmin/admin.py`` lines 6-26) can't be
done by monkeypatching a flag — the branch is chosen while the module executes.
So we load ``snapadmin/admin.py`` **fresh** with Unfold hidden, into a throwaway
module name. Coverage still attributes the executed lines to ``snapadmin/admin.py``
(coverage keys on the source file, not the module name), while the canonical
``snapadmin.admin`` stays untouched — no module reload, no admin-registry churn.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
from django.conf import settings
from django.contrib.admin import AdminSite
from unittest.mock import patch

ADMIN_SOURCE = pathlib.Path(sys.modules["snapadmin.admin"].__file__)


def _load_admin_without_unfold():
    """Execute ``snapadmin/admin.py`` fresh with Unfold removed from INSTALLED_APPS.

    Swaps ``settings.INSTALLED_APPS`` for a copy that omits the unfold apps (a plain
    value swap — it does *not* rebuild the app registry) so the module's
    ``'unfold' not in settings.INSTALLED_APPS`` guard takes the fallback branch, then
    restores it. Returns the freshly-executed throwaway module.
    """
    spec = importlib.util.spec_from_file_location("snapadmin._admin_no_unfold_probe", ADMIN_SOURCE)
    module = importlib.util.module_from_spec(spec)

    original = settings.INSTALLED_APPS
    without_unfold = [
        app for app in original if app != "unfold" and not app.startswith("unfold.")
    ]
    try:
        settings.INSTALLED_APPS = without_unfold
        spec.loader.exec_module(module)
    finally:
        settings.INSTALLED_APPS = original
    return module


class TestAdminImportFallback:
    def test_falls_back_to_stock_admin(self):
        from django.contrib import admin as django_admin

        module = _load_admin_without_unfold()

        assert module.UNFOLD_INSTALLED is False
        assert module.ModelAdmin is django_admin.ModelAdmin
        assert module.TabularInline is django_admin.TabularInline
        assert module.StackedInline is django_admin.StackedInline
        assert module.RelatedDropdownFilter is django_admin.RelatedFieldListFilter
        assert module.ChoicesDropdownFilter is django_admin.ChoicesFieldListFilter

        # The canonical module is unaffected — the test env keeps Unfold active.
        import snapadmin.admin as canonical
        assert canonical.UNFOLD_INSTALLED is True

    def test_stub_display_decorator(self):
        module = _load_admin_without_unfold()

        @module.display(description="A label")
        def with_desc(self, obj):  # pragma: no cover - body never called
            return obj

        assert with_desc.short_description == "A label"

        # No description → the decorator is a transparent passthrough.
        @module.display()
        def without_desc(self, obj):  # pragma: no cover - body never called
            return obj

        assert not hasattr(without_desc, "short_description")


class TestAdminDisplayMethodFallback:
    """Without Unfold the ``@display`` methods return plain values, not the
    ``[value, None, None]`` / ``(label, colour)`` shapes Unfold consumes."""

    def _admins(self):
        from snapadmin.models import APIToken, ErrorEvent, SnapadminAuditLog

        module = _load_admin_without_unfold()
        site = AdminSite()
        return (
            module.APITokenAdmin(APIToken, site),
            module.ErrorEventAdmin(ErrorEvent, site),
            module.SnapadminAuditLogAdmin(SnapadminAuditLog, site),
        )

    def test_api_token_masked_key_and_status_return_plain(self):
        token_admin, _, _ = self._admins()

        token = type("T", (), {"token_prefix": "abcd1234", "is_active": True, "is_expired": False})()
        masked = token_admin.masked_key(token)
        assert masked == "abcd1234••••••••"
        assert not isinstance(masked, list)

        assert token_admin.status_badge(token) == "Active"

    def test_error_event_status_returns_plain(self):
        _, error_admin, _ = self._admins()
        event = type("E", (), {"status_code": 500})()
        assert error_admin.status_badge(event) == "500"

    def test_audit_log_action_returns_plain(self):
        _, _, audit_admin = self._admins()
        entry = type("A", (), {"action": "create", "get_action_display": lambda self: "Created"})()
        assert audit_admin.action_badge(entry) == "Created"


class TestFormattedIdFallback:
    def test_formatted_id_returns_bare_value_without_unfold(self):
        import snapadmin.models as models_module

        obj = type("Obj", (), {"pk": 123})()
        with patch.object(models_module, "UNFOLD_INSTALLED", False):
            result = models_module.formatted_id(obj)

        # Stock-admin path returns the bare SafeString, not the Unfold triple.
        assert not isinstance(result, list)
        assert "123" in str(result)


class TestExtraSettingsWithoutUnfold:
    def test_apply_unfold_styling_is_noop_without_unfold(self, monkeypatch):
        """A ``[extra-settings]``-without-``[theme]`` install must not crash ready().

        ``extra_settings`` is installed in the test env, so we get past its import;
        hiding ``unfold.admin`` then makes the restyle a clean no-op (returns False)
        instead of raising an ImportError out of ``SnapAdminConfig.ready()``.
        """
        from snapadmin.extra_settings_admin import apply_unfold_styling

        monkeypatch.setitem(sys.modules, "unfold.admin", None)
        assert apply_unfold_styling() is False
