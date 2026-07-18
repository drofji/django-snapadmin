"""
tests/test_extra_settings_sync.py — demo extra_settings → settings bridge (#DEMO10)

The demo surfaces a curated set of runtime-editable SNAPADMIN_* settings through
django-extra-settings (DB-backed, admin-editable) and syncs the DB value back onto
``django.conf.settings`` so the (extra_settings-agnostic) package keeps reading its
config normally. See demo/app/managed_settings.py.

These tests create their own Setting rows (the suite otherwise seeds none — see
``EXTRA_SETTINGS_DEFAULTS = []`` in settings_test). The sync mutates the
process-global settings object, which pytest does not roll back, so the fixture
below snapshots and restores every managed setting around each test.
"""

import pytest
from django.conf import settings

from demo.app import managed_settings as ms


@pytest.fixture(autouse=True)
def _restore_managed_settings():
    """Snapshot every managed setting and restore it after the test.

    ``setattr`` on the global settings object is not transactional, so a test that
    saves a Setting (firing the live-apply signal) would otherwise leak the new
    value into later tests.
    """
    _MISSING = object()
    snapshot = {name: getattr(settings, name, _MISSING) for name in ms.MANAGED_SETTING_NAMES}
    yield
    for name, value in snapshot.items():
        if value is _MISSING:
            try:
                delattr(settings, name)
            except AttributeError:
                pass
        else:
            setattr(settings, name, value)


# ── build_extra_settings_defaults ────────────────────────────────────────────

class TestBuildDefaults:
    def test_shape_matches_spec(self):
        defaults = ms.build_extra_settings_defaults()
        assert [d["name"] for d in defaults] == list(ms.MANAGED_SETTING_NAMES)
        for d in defaults:
            assert {"name", "type", "value", "description"} <= set(d)
            assert d["description"]  # never blank

    def test_override_replaces_seed_value(self):
        defaults = ms.build_extra_settings_defaults(
            overrides={"SNAPADMIN_API_PAGE_SIZE": 99}
        )
        by_name = {d["name"]: d for d in defaults}
        assert by_name["SNAPADMIN_API_PAGE_SIZE"]["value"] == 99
        # An un-overridden setting keeps its spec default.
        assert by_name["SNAPADMIN_API_MAX_PAGE_SIZE"]["value"] == 500

    def test_returns_a_fresh_list_not_shared_spec(self):
        # Mutating the returned dicts must not corrupt the module-level spec.
        defaults = ms.build_extra_settings_defaults(overrides={"SNAPADMIN_API_PAGE_SIZE": 1})
        for d in defaults:
            if d["name"] == "SNAPADMIN_API_PAGE_SIZE":
                d["value"] = 12345
        assert ms.MANAGED_SETTINGS_SPEC[1]["value"] == 25  # untouched

    def test_no_secret_settings_surfaced(self):
        names = set(ms.MANAGED_SETTING_NAMES)
        for leaked in (
            "SECRET_KEY",
            "SNAPADMIN_BACKUP_SFTP_PASSWORD",
            "SNAPADMIN_BACKUP_FTP_PASSWORD",
        ):
            assert leaked not in names

    def test_no_boottime_routing_toggles_surfaced(self):
        # These are read at import/boot (URL registration), so editing them live
        # wouldn't take effect — deliberately excluded.
        names = set(ms.MANAGED_SETTING_NAMES)
        for boot in (
            "SNAPADMIN_REST_API_ENABLED",
            "SNAPADMIN_GRAPHQL_ENABLED",
            "SNAPADMIN_SWAGGER_ENABLED",
            "SNAPADMIN_URL_PREFIX",
            "SNAPADMIN_USER_API_ENABLED",
        ):
            assert boot not in names


# ── live apply on save (post_save signal) ────────────────────────────────────

@pytest.mark.django_db
class TestLiveApply:
    def test_saving_managed_setting_applies_to_django_settings(self):
        from extra_settings.models import Setting

        Setting.objects.create(
            name="SNAPADMIN_API_PAGE_SIZE", value_type=Setting.TYPE_INT, value=7
        )
        assert settings.SNAPADMIN_API_PAGE_SIZE == 7

    def test_editing_managed_setting_reapplies(self):
        from extra_settings.models import Setting

        obj = Setting.objects.create(
            name="SNAPADMIN_API_PAGE_SIZE", value_type=Setting.TYPE_INT, value=7
        )
        obj.value = 13
        obj.save()
        assert settings.SNAPADMIN_API_PAGE_SIZE == 13

    def test_unmanaged_setting_is_ignored(self):
        from extra_settings.models import Setting

        Setting.objects.create(
            name="SOME_OTHER_SETTING", value_type=Setting.TYPE_INT, value=42
        )
        assert not hasattr(settings, "SOME_OTHER_SETTING")

    def test_masked_fields_json_roundtrips(self):
        from extra_settings.models import Setting

        Setting.objects.create(
            name="SNAPADMIN_MASKED_FIELDS",
            value_type=Setting.TYPE_JSON,
            value={"demo.Customer": ["email"]},
        )
        assert settings.SNAPADMIN_MASKED_FIELDS == {"demo.Customer": ["email"]}


# ── startup sync (sync_managed_settings_to_django) ───────────────────────────

@pytest.mark.django_db
class TestStartupSync:
    def test_applies_present_rows_only(self):
        from extra_settings.models import Setting

        Setting.objects.create(
            name="SNAPADMIN_DASHBOARD_PUBLIC", value_type=Setting.TYPE_BOOL, value=True
        )
        # Simulate a fresh process: reset the attr, then run the startup sync.
        settings.SNAPADMIN_DASHBOARD_PUBLIC = False
        ms.sync_managed_settings_to_django()
        assert settings.SNAPADMIN_DASHBOARD_PUBLIC is True

    def test_absent_row_leaves_default_untouched(self):
        # No Setting row for this name → sync must not touch settings.py's value.
        sentinel = getattr(settings, "SNAPADMIN_ES_SEARCH_LIMIT", 1000)
        ms.sync_managed_settings_to_django()
        assert getattr(settings, "SNAPADMIN_ES_SEARCH_LIMIT", 1000) == sentinel

    def test_sync_is_safe_when_extra_settings_unavailable(self, monkeypatch):
        # Emulate the pre-migrate / uninstalled case: the query raises → no-op.
        import extra_settings.models as esm

        def boom(*a, **k):
            raise RuntimeError("no such table")

        monkeypatch.setattr(esm.Setting.objects, "filter", boom)
        ms.sync_managed_settings_to_django()  # must not raise


# ── seeding integration (EXTRA_SETTINGS_DEFAULTS) ────────────────────────────

@pytest.mark.django_db
def test_defaults_seed_and_apply():
    """A populated EXTRA_SETTINGS_DEFAULTS seeds rows that the sync then applies."""
    from django.test import override_settings
    from extra_settings.models import Setting

    defaults = ms.build_extra_settings_defaults(overrides={"SNAPADMIN_API_PAGE_SIZE": 33})
    with override_settings(EXTRA_SETTINGS_DEFAULTS=defaults):
        Setting.set_defaults(defaults)  # what post_migrate does
        settings_page = Setting.objects.get(name="SNAPADMIN_API_PAGE_SIZE")
        assert settings_page.value == 33
    # sync applies it onto django settings
    settings.SNAPADMIN_API_PAGE_SIZE = 25
    ms.sync_managed_settings_to_django()
    assert settings.SNAPADMIN_API_PAGE_SIZE == 33
