"""
tests/test_encryption_keys.py

The encryption keyset (#CRYPT1a) — key material resolution, validation and the
startup checks that stand between a project and a silently-plaintext column.

Two properties are load-bearing here and are asserted directly rather than
trusted:

* **Nothing ever renders key material.** Not ``repr``, not ``str``, not an
  exception message, not a log call. Every error path is checked for the
  material it was handed.
* **Nothing fails open.** An unresolvable configuration is an error, not a
  fall-through to the next source; a missing keyset under an encrypted field is
  a startup error, not a runtime surprise.

The cipher itself lands in #CRYPT1b — this module is deliberately stdlib-only
and adds no dependency.
"""
from __future__ import annotations

import base64
import re
import stat
from io import StringIO
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from snapadmin import checks
from snapadmin.encryption import keys as keymod


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _material(byte: int) -> bytes:
    """32 deterministic, distinct bytes — a valid key, stable across runs."""
    return bytes([byte]) * keymod.KEY_BYTES


def _encoded(byte: int) -> str:
    return base64.urlsafe_b64encode(_material(byte)).decode()


KEY_A = _encoded(1)
KEY_B = _encoded(2)
KEY_C = _encoded(3)


class _FakeField:
    """Stands in for a SnapEncrypted*Field until #CRYPT1c ships the real one."""

    def __init__(self, encrypted: bool) -> None:
        if encrypted:
            self.is_snap_encrypted = True


class _FakeMeta:
    def __init__(self, fields) -> None:
        self._fields = fields
        self.label = "fake.Model"

    def get_fields(self):
        return self._fields


class _FakeModel:
    def __init__(self, *fields) -> None:
        self._meta = _FakeMeta(fields)


@pytest.fixture(autouse=True)
def _isolated_keyset(monkeypatch):
    """No ambient key material, no cache carried between tests."""
    monkeypatch.delenv(keymod.ENV_KEYS, raising=False)
    monkeypatch.delenv(keymod.ENV_KEY_FILE, raising=False)
    keymod.reset_keyset()
    yield
    keymod.reset_keyset()


# ─────────────────────────────────────────────────────────────────────────────
# Key generation
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateKey:
    def test_generates_a_32_byte_urlsafe_base64_key(self):
        generated = keymod.generate_key()
        assert base64.urlsafe_b64decode(generated) == base64.urlsafe_b64decode(generated)
        assert len(base64.urlsafe_b64decode(generated)) == keymod.KEY_BYTES

    def test_successive_keys_differ(self):
        assert keymod.generate_key() != keymod.generate_key()

    def test_generated_key_is_accepted_by_the_parser(self):
        key = keymod.EncryptionKey.from_encoded("k1", keymod.generate_key())
        assert key.id == "k1"
        assert len(key.material) == keymod.KEY_BYTES

    def test_encode_key_round_trips(self):
        assert base64.urlsafe_b64decode(keymod.encode_key(_material(7))) == _material(7)


# ─────────────────────────────────────────────────────────────────────────────
# EncryptionKey — validation and the never-render guarantee
# ─────────────────────────────────────────────────────────────────────────────

class TestEncryptionKey:
    def test_rejects_material_that_is_not_base64(self):
        with pytest.raises(ImproperlyConfigured, match="not valid base64url"):
            keymod.EncryptionKey.from_encoded("k1", "not base64 !!!")

    def test_rejects_material_of_the_wrong_length(self):
        short = base64.urlsafe_b64encode(b"too short").decode()
        with pytest.raises(ImproperlyConfigured, match="32 bytes"):
            keymod.EncryptionKey.from_encoded("k1", short)

    def test_rejects_empty_material(self):
        with pytest.raises(ImproperlyConfigured, match="empty"):
            keymod.EncryptionKey.from_encoded("k1", "   ")

    @pytest.mark.parametrize("key_id", ["", "   ", "a" * 65, "has.dot", "has:colon", "has space"])
    def test_rejects_an_unusable_key_id(self, key_id):
        with pytest.raises(ImproperlyConfigured, match="key id"):
            keymod.EncryptionKey.from_encoded(key_id, KEY_A)

    def test_accepts_an_unpadded_key(self):
        unpadded = KEY_A.rstrip("=")
        assert keymod.EncryptionKey.from_encoded("k1", unpadded).material == _material(1)

    def test_accepts_surrounding_whitespace(self):
        assert keymod.EncryptionKey.from_encoded(" k1 ", f"  {KEY_A}  ").id == "k1"

    def test_repr_and_str_never_render_the_material(self):
        key = keymod.EncryptionKey.from_encoded("k1", KEY_A)
        for rendered in (repr(key), str(key), f"{key}"):
            assert KEY_A not in rendered
            assert KEY_A.rstrip("=") not in rendered
            assert "k1" in rendered

    def test_repr_shows_the_fingerprint(self):
        key = keymod.EncryptionKey.from_encoded("k1", KEY_A)
        assert key.fingerprint in repr(key)

    def test_fingerprint_is_short_stable_and_value_dependent(self):
        first = keymod.EncryptionKey.from_encoded("k1", KEY_A)
        same = keymod.EncryptionKey.from_encoded("other-id", KEY_A)
        other = keymod.EncryptionKey.from_encoded("k1", KEY_B)
        assert re.fullmatch(r"[0-9a-f]{12}", first.fingerprint)
        assert first.fingerprint == same.fingerprint
        assert first.fingerprint != other.fingerprint

    def test_a_reversed_entry_never_puts_the_key_in_the_message(self):
        """`<key>:<id>` instead of `<id>:<key>` makes the *key* the id — never echo it.

        The id is interpolated into every validation message, so a backwards
        entry would otherwise print key material into a traceback, a DEBUG 500
        page and the logs.
        """
        unpadded = KEY_A.rstrip("=")
        for reversed_entry in (f"{unpadded}:2026-09", f"{KEY_A}:2026-09"):
            with pytest.raises(ImproperlyConfigured) as excinfo:
                keymod._parse_entries(reversed_entry, keymod.KeySource.ENV)
            message = str(excinfo.value)
            assert unpadded not in message
            assert KEY_A not in message
            assert "not shown" in message

    def test_a_short_id_is_still_named_so_a_typo_stays_diagnosable(self):
        with pytest.raises(ImproperlyConfigured, match="'has space'"):
            keymod.EncryptionKey.from_encoded("has space", KEY_A)

    @pytest.mark.parametrize("value,shown", [("2026-09", True), ("a" * 17, False)])
    def test_displayable_id_redacts_only_what_could_be_a_key(self, value, shown):
        rendered = keymod.displayable_id(value)
        assert (value in rendered) is shown

    def test_a_rejected_key_never_appears_in_the_error(self):
        bad = base64.urlsafe_b64encode(b"x" * 31).decode()
        with pytest.raises(ImproperlyConfigured) as excinfo:
            keymod.EncryptionKey.from_encoded("k1", bad)
        assert bad not in str(excinfo.value)
        assert bad.rstrip("=") not in str(excinfo.value)


# ─────────────────────────────────────────────────────────────────────────────
# Keyset
# ─────────────────────────────────────────────────────────────────────────────

class TestKeyset:
    def _keyset(self, *pairs) -> keymod.Keyset:
        return keymod.Keyset.build(
            [keymod.EncryptionKey.from_encoded(kid, mat) for kid, mat in pairs],
            source=keymod.KeySource.SETTINGS,
        )

    def test_the_first_key_is_the_active_one(self):
        keyset = self._keyset(("new", KEY_A), ("old", KEY_B))
        assert keyset.active.id == "new"

    def test_every_key_is_reachable_by_id(self):
        keyset = self._keyset(("new", KEY_A), ("old", KEY_B))
        assert keyset.get("old").material == _material(2)
        assert keyset.get("missing") is None

    def test_ids_preserve_configured_order(self):
        assert self._keyset(("new", KEY_A), ("old", KEY_B)).ids == ("new", "old")

    def test_rejects_duplicate_ids(self):
        with pytest.raises(ImproperlyConfigured, match="more than once"):
            self._keyset(("same", KEY_A), ("same", KEY_B))

    def test_a_duplicate_id_that_could_be_a_key_is_not_echoed(self):
        unpadded = KEY_A.rstrip("=")
        with pytest.raises(ImproperlyConfigured) as excinfo:
            self._keyset((unpadded, KEY_A), (unpadded, KEY_B))
        assert unpadded not in str(excinfo.value)

    def test_rejects_an_empty_keyset(self):
        with pytest.raises(ImproperlyConfigured, match="no keys"):
            keymod.Keyset.build([], source=keymod.KeySource.SETTINGS)

    def test_repr_never_renders_material(self):
        keyset = self._keyset(("new", KEY_A), ("old", KEY_B))
        for rendered in (repr(keyset), str(keyset)):
            assert KEY_A not in rendered
            assert KEY_B not in rendered
            assert "new" in rendered

    def test_fingerprint_covers_the_whole_set(self):
        one = self._keyset(("new", KEY_A))
        reordered = self._keyset(("new", KEY_A), ("old", KEY_B))
        assert one.fingerprint != reordered.fingerprint
        assert re.fullmatch(r"[0-9a-f]{12}", one.fingerprint)

    def test_len_reports_the_key_count(self):
        assert len(self._keyset(("new", KEY_A), ("old", KEY_B))) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Resolution — four sources, documented precedence, first hit wins
# ─────────────────────────────────────────────────────────────────────────────

def provider_two_keys():
    """Importable target for KEY_PROVIDER tests."""
    return [{"id": "provided", "key": KEY_C}, {"id": "old", "key": KEY_B}]


def provider_returns_keyset():
    return keymod.Keyset.build(
        [keymod.EncryptionKey.from_encoded("from-keyset", KEY_A)],
        source=keymod.KeySource.PROVIDER,
    )


def provider_returns_nothing():
    return []


def provider_returns_garbage():
    return 42


_HERE = "tests.test_encryption_keys"


class TestResolutionSources:
    def test_unconfigured_project_resolves_to_no_keyset(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        assert keymod.get_keyset() is None
        assert keymod.is_configured() is False

    def test_setting_absent_entirely_resolves_to_no_keyset(self):
        """The default state of every existing install: the setting is simply not there."""
        from django.conf import settings as django_settings

        assert not hasattr(django_settings, "SNAPADMIN_ENCRYPTION")
        assert keymod.get_keyset() is None

    def test_keys_in_settings(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        keyset = keymod.get_keyset()
        assert keyset.source is keymod.KeySource.SETTINGS
        assert keyset.active.id == "k1"
        assert keymod.is_configured() is True

    def test_keys_from_the_environment(self, settings, monkeypatch):
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.setenv(keymod.ENV_KEYS, f"k1:{KEY_A},k2:{KEY_B}")
        keyset = keymod.get_keyset()
        assert keyset.source is keymod.KeySource.ENV
        assert keyset.ids == ("k1", "k2")

    def test_a_bare_environment_key_gets_the_default_id(self, settings, monkeypatch):
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.setenv(keymod.ENV_KEYS, KEY_A)
        assert keymod.get_keyset().ids == (keymod.DEFAULT_KEY_ID,)

    def test_environment_value_tolerates_quotes_whitespace_and_blanks(self, settings, monkeypatch):
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.setenv(keymod.ENV_KEYS, f'"  k1:{KEY_A} , , k2:{KEY_B} "')
        assert keymod.get_keyset().ids == ("k1", "k2")

    def test_an_empty_environment_value_is_not_configuration(self, settings, monkeypatch):
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.setenv(keymod.ENV_KEYS, "   ")
        assert keymod.get_keyset() is None

    def test_keys_from_a_file(self, settings, tmp_path):
        path = tmp_path / "keyset"
        path.write_text(f"# rotated 2026-09\nk1:{KEY_A}\n\nk2:{KEY_B}\n")
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(path)}
        keyset = keymod.get_keyset()
        assert keyset.source is keymod.KeySource.FILE
        assert keyset.ids == ("k1", "k2")

    def test_key_file_can_come_from_the_environment(self, settings, tmp_path, monkeypatch):
        path = tmp_path / "keyset"
        path.write_text(f"k1:{KEY_A}\n")
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.setenv(keymod.ENV_KEY_FILE, str(path))
        assert keymod.get_keyset().source is keymod.KeySource.FILE

    def test_a_missing_key_file_is_a_hard_error(self, settings, tmp_path):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(tmp_path / "absent")}
        with pytest.raises(ImproperlyConfigured, match="cannot be read"):
            keymod.get_keyset()

    def test_an_empty_key_file_is_a_hard_error(self, settings, tmp_path):
        path = tmp_path / "keyset"
        path.write_text("# only a comment\n")
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(path)}
        with pytest.raises(ImproperlyConfigured, match="no keys"):
            keymod.get_keyset()

    def test_keys_from_a_provider(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_PROVIDER": f"{_HERE}.provider_two_keys"}
        keyset = keymod.get_keyset()
        assert keyset.source is keymod.KeySource.PROVIDER
        assert keyset.ids == ("provided", "old")

    def test_a_provider_may_return_a_keyset_directly(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_PROVIDER": f"{_HERE}.provider_returns_keyset"}
        assert keymod.get_keyset().active.id == "from-keyset"

    def test_an_unimportable_provider_is_a_hard_error(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_PROVIDER": "no_such_module.loader"}
        with pytest.raises(ImproperlyConfigured, match="KEY_PROVIDER"):
            keymod.get_keyset()

    def test_a_provider_returning_nothing_is_a_hard_error(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_PROVIDER": f"{_HERE}.provider_returns_nothing"}
        with pytest.raises(ImproperlyConfigured, match="returned no keys"):
            keymod.get_keyset()

    def test_a_provider_returning_the_wrong_shape_is_a_hard_error(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_PROVIDER": f"{_HERE}.provider_returns_garbage"}
        with pytest.raises(ImproperlyConfigured, match="must return"):
            keymod.get_keyset()

    def test_a_key_entry_missing_its_material_is_a_hard_error(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1"}]}
        with pytest.raises(ImproperlyConfigured, match="'key'"):
            keymod.get_keyset()

    def test_a_key_entry_that_is_not_a_mapping_is_a_hard_error(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": ["k1:" + KEY_A]}
        with pytest.raises(ImproperlyConfigured, match="mapping"):
            keymod.get_keyset()

    def test_a_key_entry_may_omit_its_id(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"key": KEY_A}]}
        assert keymod.get_keyset().ids == (keymod.DEFAULT_KEY_ID,)

    def test_a_non_mapping_setting_is_a_hard_error(self, settings):
        settings.SNAPADMIN_ENCRYPTION = ["not", "a", "dict"]
        with pytest.raises(ImproperlyConfigured, match="must be a dict"):
            keymod.get_keyset()


class TestResolutionPrecedence:
    def test_provider_beats_every_other_source(self, settings, tmp_path, monkeypatch):
        path = tmp_path / "keyset"
        path.write_text(f"from-file:{KEY_A}\n")
        monkeypatch.setenv(keymod.ENV_KEYS, f"from-env:{KEY_B}")
        settings.SNAPADMIN_ENCRYPTION = {
            "KEY_PROVIDER": f"{_HERE}.provider_two_keys",
            "KEY_FILE": str(path),
            "KEYS": [{"id": "from-settings", "key": KEY_C}],
        }
        assert keymod.get_keyset().source is keymod.KeySource.PROVIDER

    def test_file_beats_env_and_settings(self, settings, tmp_path, monkeypatch):
        path = tmp_path / "keyset"
        path.write_text(f"from-file:{KEY_A}\n")
        monkeypatch.setenv(keymod.ENV_KEYS, f"from-env:{KEY_B}")
        settings.SNAPADMIN_ENCRYPTION = {
            "KEY_FILE": str(path),
            "KEYS": [{"id": "from-settings", "key": KEY_C}],
        }
        assert keymod.get_keyset().ids == ("from-file",)

    def test_env_beats_settings(self, settings, monkeypatch):
        monkeypatch.setenv(keymod.ENV_KEYS, f"from-env:{KEY_B}")
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "from-settings", "key": KEY_C}]}
        assert keymod.get_keyset().ids == ("from-env",)

    def test_sources_are_never_merged(self, settings, monkeypatch):
        monkeypatch.setenv(keymod.ENV_KEYS, f"from-env:{KEY_B}")
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "from-settings", "key": KEY_C}]}
        assert len(keymod.get_keyset()) == 1


class TestResolutionCaching:
    def test_the_keyset_is_resolved_once(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_PROVIDER": f"{_HERE}.provider_two_keys"}
        with mock.patch(f"{_HERE}.provider_two_keys", side_effect=provider_two_keys) as provider:
            first = keymod.get_keyset()
            second = keymod.get_keyset()
        assert first is second
        assert provider.call_count == 1

    def test_the_unconfigured_answer_is_cached_too(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        assert keymod.get_keyset() is None
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "late", "key": KEY_A}]}
        assert keymod.get_keyset() is None, "a cached resolution must not silently re-resolve"

    def test_reset_forces_a_re_resolution(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        assert keymod.get_keyset() is None
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "late", "key": KEY_A}]}
        keymod.reset_keyset()
        assert keymod.get_keyset().active.id == "late"


class TestRequireKeyset:
    def test_returns_the_keyset_when_configured(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        assert keymod.require_keyset().active.id == "k1"

    def test_raises_with_an_actionable_hint_when_unconfigured(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        with pytest.raises(ImproperlyConfigured) as excinfo:
            keymod.require_keyset()
        message = str(excinfo.value)
        assert "SNAPADMIN_ENCRYPTION" in message
        assert "snapadmin_encryption_key" in message


class TestNoMaterialEverLeaks:
    def test_resolution_logs_the_source_and_fingerprint_but_no_material(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        with mock.patch.object(keymod, "logger") as logger:
            keyset = keymod.get_keyset()
        assert logger.info.called
        rendered = repr(logger.info.call_args)
        assert KEY_A not in rendered
        assert KEY_A.rstrip("=") not in rendered
        assert keyset.fingerprint in rendered

    def test_every_resolution_error_stays_material_free(self, settings, tmp_path):
        path = tmp_path / "keyset"
        path.write_text(f"bad id:{KEY_A}\n")
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(path)}
        with pytest.raises(ImproperlyConfigured) as excinfo:
            keymod.get_keyset()
        assert KEY_A not in str(excinfo.value)


# ─────────────────────────────────────────────────────────────────────────────
# Encrypted-field detection (the trigger for the fail-closed startup check)
# ─────────────────────────────────────────────────────────────────────────────

class TestHasEncryptedFields:
    def test_false_for_a_project_with_no_encrypted_field(self):
        assert keymod.has_encrypted_fields() is False

    def test_true_when_any_model_declares_one(self):
        models = [_FakeModel(_FakeField(False)), _FakeModel(_FakeField(True))]
        with mock.patch("snapadmin.encryption.keys.apps.get_models", return_value=models):
            assert keymod.has_encrypted_fields() is True

    def test_reverse_relations_without_the_marker_are_ignored(self):
        models = [_FakeModel(object(), _FakeField(False))]
        with mock.patch("snapadmin.encryption.keys.apps.get_models", return_value=models):
            assert keymod.has_encrypted_fields() is False


# ─────────────────────────────────────────────────────────────────────────────
# Startup checks
# ─────────────────────────────────────────────────────────────────────────────

def _ids(messages) -> list[str]:
    return [message.id for message in messages]


class TestEncryptionChecksAreInert:
    def test_no_message_for_an_unconfigured_project(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        assert checks.check_encryption_keys(None) == []
        assert checks.check_encryption_key_file(None) == []
        assert checks.check_encryption_required(None) == []

    def test_registered_with_the_rest(self):
        for check in (
            checks.check_encryption_keys,
            checks.check_encryption_key_file,
            checks.check_encryption_required,
        ):
            assert check in checks.ALL_CHECKS


class TestSecretKeyReuse:
    def test_errors_when_the_key_is_the_django_secret_key(self, settings):
        settings.SECRET_KEY = base64.urlsafe_b64encode(_material(9)).decode()
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": _encoded(9)}]}
        messages = checks.check_encryption_keys(None)
        assert "snapadmin.E017" in _ids(messages)

    def test_errors_when_the_key_is_the_secret_key_verbatim(self, settings):
        settings.SECRET_KEY = "s" * keymod.KEY_BYTES
        settings.SNAPADMIN_ENCRYPTION = {
            "KEYS": [{"id": "k1", "key": base64.urlsafe_b64encode(b"s" * 32).decode()}]
        }
        assert "snapadmin.E017" in _ids(checks.check_encryption_keys(None))

    def test_silent_for_an_independent_key(self, settings):
        settings.SECRET_KEY = "an unrelated django secret key"
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        assert "snapadmin.E017" not in _ids(checks.check_encryption_keys(None))

    def test_survives_a_project_with_no_usable_secret_key(self, settings):
        """Django refuses to hand out an empty SECRET_KEY — the check must still report."""
        settings.SECRET_KEY = ""
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        assert "snapadmin.E017" not in _ids(checks.check_encryption_keys(None))

    def test_silent_when_the_secret_key_is_not_base64(self, settings):
        settings.SECRET_KEY = "django-insecure-not base64 !!!"
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        assert "snapadmin.E017" not in _ids(checks.check_encryption_keys(None))

    def test_the_message_never_repeats_the_key(self, settings):
        settings.SECRET_KEY = base64.urlsafe_b64encode(_material(9)).decode()
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": _encoded(9)}]}
        rendered = str(checks.check_encryption_keys(None)[0])
        assert _encoded(9) not in rendered
        assert settings.SECRET_KEY not in rendered


class TestMalformedConfiguration:
    def test_reported_as_a_check_error_not_a_traceback(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": "not base64 !!!"}]}
        messages = checks.check_encryption_keys(None)
        assert "snapadmin.E019" in _ids(messages)

    def test_the_other_checks_stay_quiet_once_it_is_reported(self, settings, tmp_path):
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(tmp_path / "absent")}
        assert "snapadmin.E019" in _ids(checks.check_encryption_keys(None))
        assert checks.check_encryption_key_file(None) == []
        assert checks.check_encryption_required(None) == []


class TestKeyFilePermissions:
    def test_warns_when_the_key_file_is_readable_by_others(self, settings, tmp_path):
        path = tmp_path / "keyset"
        path.write_text(f"k1:{KEY_A}\n")
        path.chmod(0o644)
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(path)}
        assert "snapadmin.W016" in _ids(checks.check_encryption_key_file(None))

    def test_silent_for_an_owner_only_key_file(self, settings, tmp_path):
        path = tmp_path / "keyset"
        path.write_text(f"k1:{KEY_A}\n")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(path)}
        assert checks.check_encryption_key_file(None) == []

    def test_silent_when_no_key_file_is_configured(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        assert checks.check_encryption_key_file(None) == []

    def test_an_unstattable_key_file_does_not_break_the_check(self, settings, tmp_path):
        path = tmp_path / "keyset"
        path.write_text(f"k1:{KEY_A}\n")
        settings.SNAPADMIN_ENCRYPTION = {"KEY_FILE": str(path)}
        with mock.patch("snapadmin.checks.os.stat", side_effect=OSError("gone")):
            assert checks.check_encryption_key_file(None) == []


class TestKeysInSettings:
    def test_warns_when_key_material_lives_in_settings_in_production(self, settings):
        settings.DEBUG = False
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        assert "snapadmin.W017" in _ids(checks.check_encryption_keys(None))

    def test_silent_in_debug(self, settings):
        settings.DEBUG = True
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        assert "snapadmin.W017" not in _ids(checks.check_encryption_keys(None))

    def test_silent_when_the_keys_come_from_elsewhere(self, settings, monkeypatch):
        settings.DEBUG = False
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.setenv(keymod.ENV_KEYS, f"k1:{KEY_A}")
        assert "snapadmin.W017" not in _ids(checks.check_encryption_keys(None))


class TestKeysetRequiredByEncryptedFields:
    def test_errors_when_an_encrypted_field_has_no_keyset(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        with mock.patch.object(keymod, "has_encrypted_fields", return_value=True):
            messages = checks.check_encryption_required(None)
        assert "snapadmin.E018" in _ids(messages)

    def test_downgraded_to_a_warning_when_strict_is_off(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"STRICT": False}
        with mock.patch.object(keymod, "has_encrypted_fields", return_value=True):
            messages = checks.check_encryption_required(None)
        assert _ids(messages) == ["snapadmin.W018"]

    def test_silent_once_a_keyset_resolves(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        with mock.patch.object(keymod, "has_encrypted_fields", return_value=True):
            assert checks.check_encryption_required(None) == []

    def test_silent_when_no_model_uses_encryption(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        with mock.patch.object(keymod, "has_encrypted_fields", return_value=False):
            assert checks.check_encryption_required(None) == []


# ─────────────────────────────────────────────────────────────────────────────
# manage.py snapadmin_encryption_key
# ─────────────────────────────────────────────────────────────────────────────

def _run(*args) -> str:
    out = StringIO()
    call_command("snapadmin_encryption_key", *args, stdout=out)
    return out.getvalue()


class TestEncryptionKeyCommand:
    def test_prints_a_usable_env_line(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        output = _run()
        assert keymod.ENV_KEYS in output
        line = next(l for l in output.splitlines() if l.strip().startswith(keymod.ENV_KEYS))
        value = line.split("=", 1)[1].strip()
        key_id, encoded = value.split(":", 1)
        assert keymod.EncryptionKey.from_encoded(key_id, encoded).id == key_id

    def test_default_id_is_the_current_month(self, settings):
        from datetime import date

        settings.SNAPADMIN_ENCRYPTION = {}
        assert f"{date.today():%Y-%m}:" in _run()

    def test_id_can_be_chosen(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        assert "chosen-id:" in _run("--id", "chosen-id")

    def test_a_key_pasted_as_the_id_is_not_echoed_back(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": KEY_A}]}
        with pytest.raises(CommandError) as excinfo:
            _run("--rotate", "--id", KEY_A)
        assert KEY_A not in str(excinfo.value)

    def test_an_unusable_id_is_refused(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        with pytest.raises(CommandError, match="key id"):
            _run("--id", "not.usable")

    def test_each_run_generates_fresh_material(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        assert _run() != _run()

    def test_rotate_lists_existing_ids_without_their_material(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {
            "KEYS": [{"id": "current", "key": KEY_A}, {"id": "previous", "key": KEY_B}]
        }
        output = _run("--rotate", "--id", "next-key")
        assert "current" in output and "previous" in output
        assert KEY_A not in output and KEY_B not in output
        assert output.index("next-key") < output.index("current")

    def test_rotate_refuses_to_reuse_an_existing_id(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "current", "key": KEY_A}]}
        with pytest.raises(CommandError, match="already in the keyset"):
            _run("--rotate", "--id", "current")

    def test_rotate_without_a_configured_keyset_is_refused(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        with pytest.raises(CommandError, match="no keyset"):
            _run("--rotate")

    def test_rotate_reports_a_broken_keyset_instead_of_crashing(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [{"id": "k1", "key": "not base64 !!!"}]}
        with pytest.raises(CommandError, match="cannot be read"):
            _run("--rotate")

    def test_explains_where_to_put_the_key(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        output = _run()
        assert "KEY_FILE" in output or keymod.ENV_KEY_FILE in output
        assert "SECRET_KEY" in output

    def test_runs_before_the_checks_it_would_otherwise_trip(self, settings):
        """The command exists to fix E014 — it must not refuse to run because of it."""
        from snapadmin.management.commands import snapadmin_encryption_key as command_module

        assert command_module.Command.requires_system_checks == []
