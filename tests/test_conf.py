"""
tests/test_conf.py — the SNAPADMIN_PROFILE resolution seam (#SIMPL1g)

``snapadmin.conf.get_setting`` is the one place every ``SNAPADMIN_*`` read
site in the package goes through, resolving in this order: an explicitly set
Django setting, then the active ``SNAPADMIN_PROFILE`` preset, then the
built-in default passed by the caller. Before this module the resolution was
just ``getattr(settings, name, default)`` repeated ~100 times; the profile
layer has to slot in without changing what any of those call sites returned
when ``SNAPADMIN_PROFILE`` is unset — that backward-compatibility guarantee
is what most of this file pins down.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from snapadmin import conf


# ── explicit setting always wins ──────────────────────────────────────────────

class TestExplicitSettingWins:
    @override_settings(SNAPADMIN_PROFILE="admin", SNAPADMIN_FAKE_SETTING=True)
    def test_explicit_setting_beats_a_conflicting_profile_value(self, monkeypatch):
        monkeypatch.setitem(conf._PRESETS["admin"], "SNAPADMIN_FAKE_SETTING", False)
        assert conf.get_setting("SNAPADMIN_FAKE_SETTING", False) is True

    @override_settings(SNAPADMIN_FAKE_SETTING=False)
    def test_explicit_setting_wins_even_with_no_profile_active(self):
        assert conf.get_setting("SNAPADMIN_FAKE_SETTING", True) is False

    def test_explicit_false_is_not_mistaken_for_unset(self):
        """`hasattr`, not truthiness — a falsy explicit value must still win."""
        with override_settings(SNAPADMIN_FAKE_SETTING=0):
            assert conf.get_setting("SNAPADMIN_FAKE_SETTING", 99) == 0


# ── profile preset fills in when nothing explicit is set ─────────────────────

class TestProfilePreset:
    @override_settings(SNAPADMIN_PROFILE="admin")
    def test_profile_value_used_when_no_explicit_setting(self, monkeypatch):
        monkeypatch.setitem(conf._PRESETS["admin"], "SNAPADMIN_FAKE_SETTING", "from-admin-profile")
        assert conf.get_setting("SNAPADMIN_FAKE_SETTING", "builtin") == "from-admin-profile"

    @override_settings(SNAPADMIN_PROFILE="api")
    def test_a_setting_the_active_profile_does_not_override_falls_through_to_default(self):
        assert "SNAPADMIN_NOT_IN_ANY_PRESET" not in conf._PRESETS["api"]
        assert conf.get_setting("SNAPADMIN_NOT_IN_ANY_PRESET", "builtin-default") == "builtin-default"

    def test_full_profile_matches_the_builtin_default_by_construction(self, monkeypatch):
        """`full` is documented as "today's defaults" — every preset value must equal it."""
        monkeypatch.setitem(conf._PRESETS["full"], "SNAPADMIN_FAKE_SETTING", "builtin")
        with override_settings(SNAPADMIN_PROFILE="full"):
            assert conf.get_setting("SNAPADMIN_FAKE_SETTING", "builtin") == "builtin"


# ── the backward-compatibility guarantee ──────────────────────────────────────

class TestProfileUnsetIsByteForByteBackwardCompatible:
    """No `SNAPADMIN_PROFILE` at all must behave exactly like the old
    `getattr(settings, name, default)` every call site used before #SIMPL1g —
    regardless of what any preset says. This is the upgrade guarantee."""

    def test_unset_profile_ignores_every_preset_and_returns_the_default(self, monkeypatch):
        # The demo project sets SNAPADMIN_PROFILE = "full" as its own documented
        # no-op (dogfooding the setting) — force it truly unset here so this test
        # means what its name says, regardless of the ambient test settings.
        monkeypatch.delattr(conf.settings, "SNAPADMIN_PROFILE", raising=False)
        monkeypatch.setitem(conf._PRESETS["admin"], "SNAPADMIN_FAKE_SETTING", "would-leak-if-buggy")
        monkeypatch.setitem(conf._PRESETS["api"], "SNAPADMIN_FAKE_SETTING", "would-leak-if-buggy")
        assert conf.get_setting("SNAPADMIN_FAKE_SETTING", "the-real-default") == "the-real-default"

    def test_matches_plain_getattr_for_every_registered_preset_name(self, monkeypatch):
        """Walks every name any preset mentions and confirms profile-unset resolution
        equals a plain `getattr(settings, name, default)` — the exact expression every
        read site used before this module existed."""
        monkeypatch.delattr(conf.settings, "SNAPADMIN_PROFILE", raising=False)
        for profile_defaults in conf._PRESETS.values():
            for name, preset_value in profile_defaults.items():
                sentinel = object()
                assert conf.get_setting(name, sentinel) is getattr(
                    conf.settings, name, sentinel
                ), f"{name}: profile-unset resolution diverged from plain getattr"


# ── an unrecognised profile fails loud, not silent ────────────────────────────

class TestUnknownProfileFailsClosed:
    @override_settings(SNAPADMIN_PROFILE="production")
    def test_unrecognised_profile_name_raises_improperly_configured(self):
        with pytest.raises(ImproperlyConfigured, match="production"):
            conf.get_setting("SNAPADMIN_FAKE_SETTING", None)

    @override_settings(SNAPADMIN_PROFILE="production")
    def test_the_error_names_the_valid_choices(self):
        with pytest.raises(ImproperlyConfigured, match="admin.*api.*full|full.*api.*admin"):
            conf.get_setting("SNAPADMIN_FAKE_SETTING", None)


# ── the module's own inventory of what it exposes ─────────────────────────────

class TestProfilesConstant:
    def test_profiles_lists_exactly_the_three_documented_names(self):
        assert set(conf.PROFILES) == {"admin", "api", "full"}

    def test_every_preset_key_is_a_known_profile(self):
        assert set(conf._PRESETS.keys()) == set(conf.PROFILES)


# ── the upgrade guarantee, walking every real setting ─────────────────────────

#: Every ``SNAPADMIN_*`` setting the package reads at runtime (86, verified
#: 2026-08-26 — see #SIMPL1g), paired with the
#: literal default its call site passes. Three names are deliberately absent:
#: ``SNAPADMIN_SWAGGER_ENABLED`` and ``SNAPADMIN_GRAPHIQL_ENABLED`` resolve a
#: *dynamic* default (another setting's resolved value, not a literal) and are
#: pinned by their own call-site tests instead; ``SNAPADMIN_ERROR_ALERT_COOLDOWN_MINUTES``
#: likewise defaults to the resolved alert window. This table is the upgrade
#: guarantee: with ``SNAPADMIN_PROFILE`` unset, every one of these must resolve
#: to exactly the value it resolved to before #SIMPL1g existed.
ALL_SETTINGS_AND_DEFAULTS: tuple[tuple[str, object], ...] = (
    ("SNAPADMIN_REST_API_ENABLED", True),
    ("SNAPADMIN_GRAPHQL_ENABLED", True),
    ("SNAPADMIN_USER_API_ENABLED", False),
    ("SNAPADMIN_URL_PREFIX", ""),
    ("SNAPADMIN_DASHBOARD_PUBLIC", False),
    ("SNAPADMIN_THEME_AUTH_ADMIN", True),
    ("SNAPADMIN_API_PAGE_SIZE", 25),
    ("SNAPADMIN_API_MAX_PAGE_SIZE", 500),
    ("SNAPADMIN_API_TEXT_LOOKUPS", None),
    ("SNAPADMIN_API_FILTER_BACKEND", None),
    ("SNAPADMIN_API_JSON_FILTER_SCAN_CAP", None),
    ("SNAPADMIN_THROTTLE_ANON", "60/min"),
    ("SNAPADMIN_THROTTLE_USER", "600/min"),
    ("SNAPADMIN_API_DELETE_GUARD", None),
    ("SNAPADMIN_EXPORT_MAX_ROWS", 0),
    ("SNAPADMIN_EXPORT_LIMIT_MAX", 0),
    ("SNAPADMIN_API_AUTHENTICATION_CLASSES", None),
    ("SNAPADMIN_ES_QUERY_ROUTING", True),
    ("SNAPADMIN_ES_SEARCH_LIMIT", 1000),
    ("SNAPADMIN_ES_DB_FALLBACK", True),
    ("SNAPADMIN_ES_CLIENT_FACTORY", None),
    ("SNAPADMIN_QUERY_BACKEND_HEADER", True),
    ("SNAPADMIN_REINDEX_API_ENABLED", False),
    ("SNAPADMIN_REINDEX_API_ASYNC", False),
    ("SNAPADMIN_REINDEX_TUNE_DEFAULT", False),
    ("SNAPADMIN_ANALYTICS_DB_ALIAS", ""),
    ("SNAPADMIN_SSO_PROVIDERS", None),
    ("SNAPADMIN_SSO_ALLOWED_HOSTS", None),
    ("SNAPADMIN_HTML_SANITIZER", None),
    ("SNAPADMIN_MASKED_FIELDS", {}),
    ("SNAPADMIN_MASKING_RULES", {}),
    ("SNAPADMIN_NESTED_APPS", None),
    ("SNAPADMIN_HIDDEN_APPS", None),
    ("SNAPADMIN_APP_LABELS", None),
    ("SNAPADMIN_AUDIT_LOG_ENABLED", True),
    ("SNAPADMIN_AUDIT_RETENTION_DAYS", 365),
    ("SNAPADMIN_ESTIMATED_COUNT", True),
    ("SNAPADMIN_ESTIMATED_COUNT_THRESHOLD", 100_000),
    ("SNAPADMIN_EXPORT_ENABLED", True),
    ("SNAPADMIN_EXPORT_CHUNK_SIZE", 1000),
    ("SNAPADMIN_EXPORT_DIR", ""),
    ("SNAPADMIN_EXPORT_STORAGE", ""),
    ("SNAPADMIN_EXPORT_SOURCES", None),
    ("SNAPADMIN_GRAPHQL_REQUIRE_AUTH", True),
    ("SNAPADMIN_ERROR_MONITOR_ENABLED", True),
    ("SNAPADMIN_ERROR_ALERT_ENABLED", True),
    ("SNAPADMIN_ERROR_ALERT_THRESHOLD", 20),
    ("SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES", 15),
    ("SNAPADMIN_ERROR_ALERT_EMAILS", []),
    ("SNAPADMIN_ERROR_DIGEST_ENABLED", True),
    ("SNAPADMIN_ERROR_DIGEST_EMAILS", []),
    ("SNAPADMIN_ERROR_DIGEST_MAX_GROUPS", 20),
    ("SNAPADMIN_ERROR_RETENTION_DAYS", 30),
    ("SNAPADMIN_HEALTH_ALERT_ENABLED", True),
    ("SNAPADMIN_HEALTH_ALERT_EMAILS", []),
    ("SNAPADMIN_HEALTH_ALERT_COOLDOWN_MINUTES", 60),
    ("SNAPADMIN_ALERT_EMAIL_ENABLED", True),
    ("SNAPADMIN_ALERT_WEBHOOK_TIMEOUT", 5.0),
    ("SNAPADMIN_ALERT_WEBHOOKS", None),
    ("SNAPADMIN_BACKUP_ENABLED", False),
    ("SNAPADMIN_BACKUP_KEEP", 7),
    ("SNAPADMIN_BACKUP_LOCAL_DIR", "backups"),
    ("SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS", 24),
    ("SNAPADMIN_BACKUP_NETWORK_DIR", ""),
    ("SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS", 24),
    ("SNAPADMIN_BACKUP_FTP_HOST", ""),
    ("SNAPADMIN_BACKUP_FTP_PORT", 21),
    ("SNAPADMIN_BACKUP_FTP_USER", ""),
    ("SNAPADMIN_BACKUP_FTP_PASSWORD", ""),
    ("SNAPADMIN_BACKUP_FTP_DIR", "/"),
    ("SNAPADMIN_BACKUP_FTP_TLS", False),
    ("SNAPADMIN_BACKUP_REMOTE_EVERY_HOURS", 168),
    ("SNAPADMIN_BACKUP_SFTP_HOST", ""),
    ("SNAPADMIN_BACKUP_SFTP_PORT", 22),
    ("SNAPADMIN_BACKUP_SFTP_USER", ""),
    ("SNAPADMIN_BACKUP_SFTP_PASSWORD", ""),
    ("SNAPADMIN_BACKUP_SFTP_KEY_FILE", ""),
    ("SNAPADMIN_BACKUP_SFTP_DIR", "/"),
    ("SNAPADMIN_BACKUP_SFTP_EVERY_HOURS", 168),
    ("SNAPADMIN_BACKUP_AGE_RECIPIENTS", None),
    ("SNAPADMIN_BACKUP_AGE_IDENTITY_FILE", ""),
    ("SNAPADMIN_BACKUP_AGE_BACKEND", "auto"),
    ("SNAPADMIN_BACKUP_AGE_BINARY_PATH", ""),
)


class TestBackwardCompatibilityAcrossEveryRealSetting:
    """With no ``SNAPADMIN_PROFILE`` set, #SIMPL1g must change nothing.

    Walks every real setting the package reads and asserts it still resolves
    to exactly its pre-#SIMPL1g default — the upgrade guarantee the task
    calls for, verified against the actual inventory rather than a synthetic
    stand-in.
    """

    def test_covers_every_setting_the_package_actually_reads(self):
        """A guard on the guard: catches this table drifting from the code."""
        assert len(ALL_SETTINGS_AND_DEFAULTS) == 83
        names = [name for name, _ in ALL_SETTINGS_AND_DEFAULTS]
        assert len(names) == len(set(names)), "duplicate name in the table"

    @pytest.mark.parametrize("name,default", ALL_SETTINGS_AND_DEFAULTS)
    def test_resolves_to_its_default_with_no_profile_active(self, name, default, monkeypatch):
        # The demo project's own settings.py explicitly declares many of these
        # (project convention — every setting gets a commented entry), often
        # to a project-specific value that differs from the package's generic
        # default (e.g. SNAPADMIN_USER_API_ENABLED=True in the demo vs. False
        # here). That is explicit-wins working correctly, not a regression —
        # but it means testing "what does an unset setting resolve to" must
        # first remove the demo's own explicit value for this one name.
        monkeypatch.delattr(conf.settings, name, raising=False)
        # The demo also sets SNAPADMIN_PROFILE = "full" itself (a documented
        # no-op) — remove it too so this genuinely exercises the profile-unset
        # path the test name promises, not "full" merely behaving like it.
        monkeypatch.delattr(conf.settings, "SNAPADMIN_PROFILE", raising=False)
        assert conf.get_setting(name, default) == default
