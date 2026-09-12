"""
tests/test_encryption_cipher.py

The AES-256-GCM cipher behind field-level encryption (#CRYPT1b) — the envelope
format, the AAD binding, and the guarantees that make a wrong key a loud
failure instead of a silent one.

What is pinned here, deliberately rather than by inspection:

* **Nonces are never reused.** Encrypting the same value twice under the same
  key produces two different ciphertexts; a repeated GCM nonce is a total break
  of the cipher, so the property is asserted, not assumed.
* **A ciphertext cannot move between columns.** The AAD binds each envelope to
  ``app_label.model.field``; a value copied from one column into another fails
  to decrypt rather than silently relocating a secret.
* **Nothing fails open.** A missing key, a tampered payload, a truncated
  envelope and an unknown format version all raise; none of them return
  ``None``, the raw envelope, or a partially-decrypted value.
* **Nothing renders a secret.** No plaintext and no key material appears in any
  exception message, ``repr`` or log call on any path.
* **``DEFAULT_KEY_ID`` is load-bearing.** The id a key configured without one
  receives is written verbatim into every envelope it produces, so changing it
  would orphan already-stored rows. Pinned as a value, not as a default.
"""
from __future__ import annotations

import base64
import re
import sys
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured

from snapadmin.encryption import cipher as ciphermod
from snapadmin.encryption import keys as keymod


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _material(byte: int) -> bytes:
    return bytes([byte]) * keymod.KEY_BYTES


def _key(byte: int, key_id: str) -> keymod.EncryptionKey:
    return keymod.EncryptionKey(id=key_id, material=_material(byte))


def _keyset(*keys: keymod.EncryptionKey) -> keymod.Keyset:
    return keymod.Keyset.build(keys, source=keymod.KeySource.SETTINGS)


KEYSET_A = _keyset(_key(1, "k1"))
KEYSET_B = _keyset(_key(2, "k2"))
ROTATED = _keyset(_key(2, "k2"), _key(1, "k1"))

AAD = ciphermod.aad_for("demo", "patient", "ssn")
OTHER_AAD = ciphermod.aad_for("demo", "note", "body")

SECRET = "123-45-6789"


@pytest.fixture(autouse=True)
def _no_ambient_keyset():
    """Every test states its own keyset; none inherits the project's."""
    keymod.reset_keyset()
    yield
    keymod.reset_keyset()


# ─────────────────────────────────────────────────────────────────────────────
# The lazy backend loader
# ─────────────────────────────────────────────────────────────────────────────

class TestBackendLoading:
    def test_missing_cryptography_names_the_extra(self):
        ciphermod._load_cryptography.cache_clear()
        with mock.patch.dict(sys.modules, {"cryptography.hazmat.primitives.ciphers.aead": None}):
            with pytest.raises(ImproperlyConfigured) as exc:
                ciphermod._load_cryptography()
        ciphermod._load_cryptography.cache_clear()
        message = str(exc.value)
        assert "cryptography" in message
        assert "django-snapadmin[encryption]" in message

    def test_backend_is_cached(self):
        assert ciphermod._load_cryptography() is ciphermod._load_cryptography()

    def test_importing_the_module_does_not_import_cryptography(self):
        """The module is stdlib-only until something actually encrypts."""
        source = (
            __import__("pathlib").Path(ciphermod.__file__).read_text(encoding="utf-8")
        )
        top_level = [
            line for line in source.splitlines()
            if line.startswith("import cryptography") or line.startswith("from cryptography")
        ]
        assert top_level == []


# ─────────────────────────────────────────────────────────────────────────────
# The envelope
# ─────────────────────────────────────────────────────────────────────────────

class TestEnvelope:
    def test_round_trips_through_text(self):
        envelope = ciphermod.Envelope(key_id="k1", nonce=b"\x00" * 12, payload=b"payload")
        assert ciphermod.Envelope.parse(envelope.serialise()) == envelope

    def test_serialised_form_is_the_documented_shape(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        version, key_id, nonce, payload = token.split(".")
        assert version == "snap1"
        assert key_id == "k1"
        assert len(base64.urlsafe_b64decode(nonce + "==")) == ciphermod.NONCE_BYTES
        assert payload

    def test_envelope_is_url_safe_and_dot_delimited(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        assert re.fullmatch(r"snap1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token)

    def test_repr_shows_no_payload_bytes(self):
        envelope = ciphermod.Envelope(key_id="k1", nonce=b"\x01" * 12, payload=b"secret-bytes")
        assert "secret-bytes" not in repr(envelope)
        assert "k1" in repr(envelope)

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "plain text",
            "snap1",
            "snap1.k1.nonce",
            "snap1.k1.nonce.payload.extra",
            "snap1..AAAAAAAAAAAAAAAA.AAAA",
            "snap1.bad id.AAAAAAAAAAAAAAAA.AAAA",
            "snap1.k1.!!!!.AAAA",
            "snap1.k1.AAAA.AAAA",  # nonce is the wrong length
        ],
    )
    def test_malformed_tokens_raise(self, token):
        with pytest.raises(ciphermod.DecryptionError):
            ciphermod.Envelope.parse(token)

    def test_truncated_base64_raises_rather_than_decoding_garbage(self):
        """A part that is base64url-shaped but the wrong length still fails."""
        with pytest.raises(ciphermod.DecryptionError) as exc:
            ciphermod.Envelope.parse("snap1.k1.A.AAAA")
        assert "nonce" in str(exc.value)

    def test_non_string_token_raises(self):
        with pytest.raises(ciphermod.DecryptionError):
            ciphermod.Envelope.parse(b"snap1.k1.AAAAAAAAAAAAAAAA.AAAA")

    def test_future_version_is_named_in_the_error(self):
        with pytest.raises(ciphermod.DecryptionError) as exc:
            ciphermod.Envelope.parse("snap2.k1.AAAAAAAAAAAAAAAA.AAAA")
        assert "snap2" in str(exc.value)
        assert "newer" in str(exc.value).lower()


class TestLooksEncrypted:
    def test_recognises_its_own_output(self):
        assert ciphermod.looks_encrypted(ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A))

    def test_recognises_a_future_version_it_cannot_read(self):
        """Detection must not double-encrypt a snap2 envelope it cannot parse."""
        assert ciphermod.looks_encrypted("snap2.k1.AAAAAAAAAAAAAAAA.AAAA")

    @pytest.mark.parametrize(
        "value", ["", "plain", "snap1", "snap1.k1.nonce", "snapshot.of.the.db", None, 42, b"snap1.a.b.c"]
    )
    def test_rejects_everything_else(self, value):
        assert ciphermod.looks_encrypted(value) is False


# ─────────────────────────────────────────────────────────────────────────────
# Encrypt / decrypt
# ─────────────────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_value_survives(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        assert ciphermod.decrypt(token, aad=AAD, keyset=KEYSET_A) == SECRET

    def test_ciphertext_contains_no_plaintext(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        assert SECRET not in token

    @pytest.mark.parametrize("value", ["", " ", "ünïcödé ✉", "x" * 10_000, "a\x00b", "line\nbreak"])
    def test_awkward_values_survive(self, value):
        token = ciphermod.encrypt(value, aad=AAD, keyset=KEYSET_A)
        assert ciphermod.decrypt(token, aad=AAD, keyset=KEYSET_A) == value

    def test_same_value_twice_yields_different_ciphertext(self):
        """A repeated GCM nonce is a catastrophic break — pin the randomness."""
        first = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        second = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        assert first != second
        assert first.split(".")[2] != second.split(".")[2]

    def test_nonces_do_not_repeat_across_many_writes(self):
        nonces = {
            ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A).split(".")[2]
            for _ in range(200)
        }
        assert len(nonces) == 200

    def test_encrypt_refuses_a_non_string(self):
        with pytest.raises(ciphermod.EncryptionError) as exc:
            ciphermod.encrypt(12345, aad=AAD, keyset=KEYSET_A)
        assert "12345" not in str(exc.value)
        assert "str" in str(exc.value)


class TestAadBinding:
    def test_ciphertext_moved_to_another_column_fails(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        with pytest.raises(ciphermod.DecryptionError) as exc:
            ciphermod.decrypt(token, aad=OTHER_AAD, keyset=KEYSET_A)
        assert "demo.note.body" in str(exc.value)

    def test_aad_shape_is_app_model_field(self):
        assert ciphermod.aad_for("demo", "Patient", "ssn") == "demo.patient.ssn"

    def test_aad_excludes_the_primary_key(self):
        """Row copies and PK-changing restores must keep decrypting."""
        assert "pk" not in ciphermod.aad_for("demo", "patient", "ssn")

    def test_two_field_names_differing_only_in_case_do_not_share_a_binding(self):
        """Folding the field name would merge two columns into one binding.

        Django permits `ssn` and `SSN` on one model. Nobody writes that, but
        the binding is the only thing stopping a ciphertext being moved between
        columns, and it must not be weakened for the sake of tidy spelling.
        """
        assert ciphermod.aad_for("demo", "patient", "ssn") != ciphermod.aad_for(
            "demo", "patient", "SSN"
        )

    def test_the_model_name_is_normalised(self):
        """`Model._meta.model_name` is already lower-cased; this is a net."""
        assert ciphermod.aad_for("Demo", "Patient", "ssn") == ciphermod.aad_for(
            "demo", "patient", "ssn"
        )


class TestFailureModes:
    def test_wrong_key_under_the_same_id_fails_loudly(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        impostor = _keyset(_key(9, "k1"))
        with pytest.raises(ciphermod.DecryptionError) as exc:
            ciphermod.decrypt(token, aad=AAD, keyset=impostor)
        assert "k1" in str(exc.value)

    def test_unknown_key_id_names_the_id_and_what_is_available(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        with pytest.raises(ciphermod.DecryptionError) as exc:
            ciphermod.decrypt(token, aad=AAD, keyset=KEYSET_B)
        message = str(exc.value)
        assert "k1" in message and "k2" in message

    def test_tampered_payload_fails(self):
        version, key_id, nonce, payload = ciphermod.encrypt(
            SECRET, aad=AAD, keyset=KEYSET_A
        ).split(".")
        flipped = ("B" if payload[0] != "B" else "C") + payload[1:]
        with pytest.raises(ciphermod.DecryptionError):
            ciphermod.decrypt(f"{version}.{key_id}.{nonce}.{flipped}", aad=AAD, keyset=KEYSET_A)

    def test_decryption_error_is_an_encryption_error(self):
        assert issubclass(ciphermod.DecryptionError, ciphermod.EncryptionError)

    def test_rotation_keeps_old_rows_readable(self):
        old = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        assert ciphermod.decrypt(old, aad=AAD, keyset=ROTATED) == SECRET

    def test_rotation_writes_under_the_new_active_key(self):
        assert ciphermod.encrypt(SECRET, aad=AAD, keyset=ROTATED).split(".")[1] == "k2"


class TestKeysetResolution:
    def test_encrypt_without_a_keyset_uses_the_project_configuration(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {
            "KEYS": [{"id": "cfg", "key": keymod.encode_key(_material(7))}]
        }
        keymod.reset_keyset()
        token = ciphermod.encrypt(SECRET, aad=AAD)
        assert token.split(".")[1] == "cfg"
        assert ciphermod.decrypt(token, aad=AAD) == SECRET

    def test_encrypt_without_any_key_refuses_rather_than_storing_plaintext(self, settings):
        settings.SNAPADMIN_ENCRYPTION = {}
        keymod.reset_keyset()
        with mock.patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ImproperlyConfigured) as exc:
                ciphermod.encrypt(SECRET, aad=AAD)
        assert SECRET not in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────────
# No secret reaches a message, a repr or a log
# ─────────────────────────────────────────────────────────────────────────────

class TestNothingLeaks:
    def test_no_failure_path_renders_the_plaintext_or_the_key(self):
        token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
        encoded_key = keymod.encode_key(_material(1))
        for call in (
            lambda: ciphermod.decrypt(token, aad=OTHER_AAD, keyset=KEYSET_A),
            lambda: ciphermod.decrypt(token, aad=AAD, keyset=KEYSET_B),
            lambda: ciphermod.decrypt("snap1.k1.AAAA.AAAA", aad=AAD, keyset=KEYSET_A),
            lambda: ciphermod.encrypt(object(), aad=AAD, keyset=KEYSET_A),
        ):
            with pytest.raises(ciphermod.EncryptionError) as exc:
                call()
            rendered = f"{exc.value!r} {exc.value!s}"
            assert SECRET not in rendered
            assert encoded_key not in rendered
            assert "\\x01\\x01" not in rendered

    def test_the_module_logs_nothing_on_the_hot_path(self):
        """Encrypting and decrypting must not write a line per value."""
        with mock.patch.object(ciphermod, "logger") as logged:
            token = ciphermod.encrypt(SECRET, aad=AAD, keyset=KEYSET_A)
            ciphermod.decrypt(token, aad=AAD, keyset=KEYSET_A)
        assert logged.mock_calls == []

    def test_default_key_id_is_pinned(self):
        """Written into every envelope from a key configured without an id."""
        assert keymod.DEFAULT_KEY_ID == "default"
        keyset = _keyset(_key(4, keymod.DEFAULT_KEY_ID))
        assert ciphermod.encrypt(SECRET, aad=AAD, keyset=keyset).split(".")[1] == "default"
