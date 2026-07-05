"""
tests/test_nested_apps.py — admin-index nesting (issues #4 / #16)

SNAPADMIN_NESTED_APPS / _HIDDEN_APPS / _APP_LABELS regroup, hide and rename the
admin index groups via a wrapped admin.site.get_app_list. apply_nested_apps is a
pure function; install_nested_apps wires it in idempotently.
"""

from unittest.mock import MagicMock

from django.test import override_settings

from snapadmin.nesting import apply_nested_apps, nesting_configured
from snapadmin.apps import install_nested_apps


def _app_list():
    return [
        {"app_label": "auth", "name": "Authentication", "models": [{"name": "User"}]},
        {"app_label": "snapadmin", "name": "Snap Admin",
         "models": [{"name": "API Token"}, {"name": "Error Event"}]},
        {"app_label": "silk", "name": "Silk", "models": [{"name": "Request"}]},
    ]


# ── nesting_configured() ─────────────────────────────────────────────────────

class TestNestingConfigured:
    def test_unset(self):
        assert nesting_configured() is False

    @override_settings(SNAPADMIN_NESTED_APPS={"snapadmin": "auth"})
    def test_nested_set(self):
        assert nesting_configured() is True

    @override_settings(SNAPADMIN_HIDDEN_APPS=["silk"])
    def test_hidden_set(self):
        assert nesting_configured() is True

    @override_settings(SNAPADMIN_APP_LABELS={"auth": "Admin"})
    def test_rename_set(self):
        assert nesting_configured() is True


# ── apply_nested_apps() ──────────────────────────────────────────────────────

class TestApplyNestedApps:
    @override_settings(SNAPADMIN_NESTED_APPS={"snapadmin": "auth"})
    def test_moves_models_into_target_and_drops_source(self):
        result = apply_nested_apps(_app_list())
        labels = [a["app_label"] for a in result]
        assert "snapadmin" not in labels
        auth = next(a for a in result if a["app_label"] == "auth")
        names = [m["name"] for m in auth["models"]]
        assert names == ["User", "API Token", "Error Event"]

    @override_settings(SNAPADMIN_NESTED_APPS={"snapadmin": "nope"})
    def test_missing_target_leaves_source_untouched(self):
        result = apply_nested_apps(_app_list())
        assert any(a["app_label"] == "snapadmin" for a in result)

    @override_settings(SNAPADMIN_NESTED_APPS={"auth": "auth"})
    def test_self_reference_is_noop(self):
        result = apply_nested_apps(_app_list())
        auth = next(a for a in result if a["app_label"] == "auth")
        assert [m["name"] for m in auth["models"]] == ["User"]

    @override_settings(SNAPADMIN_HIDDEN_APPS=["silk"])
    def test_hides_groups(self):
        result = apply_nested_apps(_app_list())
        assert all(a["app_label"] != "silk" for a in result)

    @override_settings(SNAPADMIN_APP_LABELS={"auth": "Administration"})
    def test_renames_group(self):
        result = apply_nested_apps(_app_list())
        auth = next(a for a in result if a["app_label"] == "auth")
        assert auth["name"] == "Administration"

    def test_unconfigured_returns_equivalent_list(self):
        original = _app_list()
        assert apply_nested_apps(original) == original

    @override_settings(SNAPADMIN_NESTED_APPS={"snapadmin": "auth"})
    def test_target_position_preserved(self):
        result = apply_nested_apps(_app_list())
        # auth stays first; silk still present after.
        assert [a["app_label"] for a in result] == ["auth", "silk"]


# ── install_nested_apps() wrapper ────────────────────────────────────────────

class TestInstallNestedApps:
    def test_noop_when_unconfigured(self):
        from django.contrib import admin
        # With nothing configured, the real site stays unwrapped.
        install_nested_apps()
        assert not getattr(admin.site, "_snap_nested_wrapped", False)

    @override_settings(SNAPADMIN_HIDDEN_APPS=["silk"])
    def test_wraps_and_regroups_then_idempotent(self):
        from django.contrib import admin

        site = admin.site
        try:
            # Stub the original so the wrapper has a known app_list to regroup.
            site.get_app_list = lambda request, app_label=None: _app_list()

            install_nested_apps()
            assert site._snap_nested_wrapped is True
            wrapped_once = site.get_app_list

            # Idempotent: a second call must not stack another wrapper.
            install_nested_apps()
            assert site.get_app_list is wrapped_once

            # The wrapper delegates to the stub, then hides silk.
            filtered = site.get_app_list(MagicMock())
            assert all(a["app_label"] != "silk" for a in filtered)
            assert any(a["app_label"] == "auth" for a in filtered)
        finally:
            # Remove the instance overrides so the class method is restored.
            site.__dict__.pop("get_app_list", None)
            site.__dict__.pop("_snap_nested_wrapped", None)
