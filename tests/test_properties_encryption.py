"""
tests/test_properties_encryption.py

Property-based tests for field-level encryption (#QA1d, part 3).

The example-based suites (``test_encryption_cipher.py``, ``test_encryption_keys.py``,
``test_encryption_blind_index.py``) pin named cases. These state the *laws* the
cipher and the blind index must obey for every input ``hypothesis`` can think
of — arbitrary Unicode, empty strings, surrogate-free astral characters, any
key id the id pattern allows, any column binding:

* the envelope round-trips: ``decrypt(encrypt(x)) == x``;
* the envelope binds to its column: the same token under any other AAD fails
  authentication, never decrypts to something;
* no two writes produce the same token (the nonce is fresh), and the token
  never contains the plaintext;
* ``Envelope.parse`` is the exact inverse of ``Envelope.serialise``, and any
  single-character corruption of a token is refused with ``DecryptionError``
  rather than decrypting to a different value;
* the blind index is deterministic, NFC-insensitive, case-sensitive, column-
  and key-separated, and always exactly ``INDEX_CHARS`` of base64url.
"""

from __future__ import annotations

import base64
import re
import unicodedata

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from snapadmin.encryption import blind_index
from snapadmin.encryption.cipher import (
    ENVELOPE_VERSION,
    NONCE_BYTES,
    DecryptionError,
    Envelope,
    aad_for,
    decrypt,
    encrypt,
    looks_encrypted,
)
from snapadmin.encryption.keys import (
    KEY_BYTES,
    EncryptionKey,
    Keyset,
    KeySource,
    encode_key,
)

KEY_IDS = st.from_regex(r"\A[A-Za-z0-9_-]{1,64}\Z")
KEY_MATERIAL = st.binary(min_size=KEY_BYTES, max_size=KEY_BYTES)
PLAINTEXTS = st.text()
# An AAD is "app.model.field"; any text the three parts could hold is fair game.
AADS = st.text(min_size=1, max_size=80)


@st.composite
def keysets(draw, min_keys: int = 1, max_keys: int = 3) -> Keyset:
    ids = draw(st.lists(KEY_IDS, min_size=min_keys, max_size=max_keys, unique=True))
    keys = [EncryptionKey.from_encoded(key_id, encode_key(draw(KEY_MATERIAL))) for key_id in ids]
    return Keyset.build(keys, source=KeySource.SETTINGS)


_B64URL = re.compile(r"\A[A-Za-z0-9_-]*\Z")

#: AES-GCM's authentication tag, appended to every ciphertext.
GCM_TAG_BYTES = 16


class TestEnvelopeLaws:
    @given(plaintext=PLAINTEXTS, aad=AADS, keyset=keysets())
    def test_decrypt_inverts_encrypt(self, plaintext, aad, keyset):
        token = encrypt(plaintext, aad=aad, keyset=keyset)

        assert decrypt(token, aad=aad, keyset=keyset) == plaintext

    @given(plaintext=PLAINTEXTS, aad=AADS, other_aad=AADS, keyset=keysets())
    def test_a_token_only_opens_under_its_own_column_binding(
        self, plaintext, aad, other_aad, keyset
    ):
        assume(aad != other_aad)
        token = encrypt(plaintext, aad=aad, keyset=keyset)

        with pytest.raises(DecryptionError, match="failed authentication"):
            decrypt(token, aad=other_aad, keyset=keyset)

    @given(plaintext=PLAINTEXTS, aad=AADS, keyset=keysets())
    def test_every_write_is_a_new_token_that_hides_the_plaintext(self, plaintext, aad, keyset):
        first = encrypt(plaintext, aad=aad, keyset=keyset)
        second = encrypt(plaintext, aad=aad, keyset=keyset)

        assert first != second
        assert looks_encrypted(first) and looks_encrypted(second)
        if len(plaintext) >= 4:
            assert plaintext not in first

    @given(plaintext=PLAINTEXTS, aad=AADS, keyset=keysets(min_keys=2))
    def test_encrypt_always_writes_under_the_active_key(self, plaintext, aad, keyset):
        token = encrypt(plaintext, aad=aad, keyset=keyset)

        assert Envelope.parse(token).key_id == keyset.active.id

    @given(plaintext=PLAINTEXTS, aad=AADS, writer=keysets(), reader=keysets())
    def test_a_keyset_without_the_writing_key_refuses_rather_than_guesses(
        self, plaintext, aad, writer, reader
    ):
        assume(writer.active.id not in reader.ids)
        token = encrypt(plaintext, aad=aad, keyset=writer)

        with pytest.raises(DecryptionError, match="No encryption key with id"):
            decrypt(token, aad=aad, keyset=reader)

    @given(
        key_id=KEY_IDS,
        nonce=st.binary(min_size=NONCE_BYTES, max_size=NONCE_BYTES),
        # AES-GCM appends a 16-byte tag, so a real payload is never shorter.
        payload=st.binary(min_size=GCM_TAG_BYTES, max_size=200),
    )
    def test_parse_inverts_serialise(self, key_id, nonce, payload):
        envelope = Envelope(key_id=key_id, nonce=nonce, payload=payload)

        text = envelope.serialise()

        assert Envelope.parse(text) == envelope
        assert text.startswith(f"{ENVELOPE_VERSION}.{key_id}.")
        assert "=" not in text  # unpadded base64url throughout

    @given(
        plaintext=PLAINTEXTS,
        aad=AADS,
        keyset=keysets(),
        data=st.data(),
    )
    def test_any_single_character_corruption_is_refused(self, plaintext, aad, keyset, data):
        """Flip one character of the stored token, anywhere. The result must be
        a ``DecryptionError`` (shape or authentication) — never a value, and
        never a different exception type leaking out of the backend."""
        token = encrypt(plaintext, aad=aad, keyset=keyset)
        position = data.draw(st.integers(min_value=0, max_value=len(token) - 1))
        replacement = data.draw(st.characters(codec="ascii").filter(lambda c: c != token[position]))
        corrupted = token[:position] + replacement + token[position + 1 :]

        try:
            recovered = decrypt(corrupted, aad=aad, keyset=keyset)
        except DecryptionError:
            return
        # base64url's last character carries padding bits; flipping only those
        # can leave the decoded bytes unchanged. That is the one corruption
        # allowed to "succeed" — and only by returning the original value.
        assert recovered == plaintext
        assert Envelope.parse(corrupted) == Envelope.parse(token)

    @given(key_id=KEY_IDS, nonce=st.binary(min_size=NONCE_BYTES, max_size=NONCE_BYTES))
    def test_an_envelope_with_no_payload_is_refused(self, key_id, nonce):
        """Found by the round-trip law above: ``serialise`` will render an empty
        payload, which no real encryption can produce (the GCM tag alone is 16
        bytes). ``parse`` refusing it is correct — pinned so it stays refused."""
        empty = Envelope(key_id=key_id, nonce=nonce, payload=b"").serialise()

        with pytest.raises(DecryptionError, match="payload is not base64url"):
            Envelope.parse(empty)

    @given(value=st.one_of(st.none(), st.integers(), st.binary(), st.lists(st.text())))
    def test_parse_refuses_every_non_string_with_decryption_error(self, value):
        with pytest.raises(DecryptionError, match="expected a string"):
            Envelope.parse(value)

    @given(text=st.text())
    def test_parse_either_reads_an_envelope_or_raises_decryption_error(self, text):
        """Arbitrary text — including plaintext somebody stored by mistake —
        must never escape ``parse`` as anything but ``DecryptionError``."""
        try:
            envelope = Envelope.parse(text)
        except DecryptionError as exc:
            if len(text) >= 8:
                assert text not in str(exc)  # a rejection never echoes the value
            return
        # Canonical form, not the same string: base64url's last character can
        # carry ignored padding bits, so two spellings may decode alike.
        assert Envelope.parse(envelope.serialise()) == envelope
        assert envelope.version == ENVELOPE_VERSION
        assert len(envelope.nonce) == NONCE_BYTES


class TestAadLaws:
    @given(app=st.text(min_size=1), model=st.text(min_size=1), field=st.text(min_size=1))
    def test_app_and_model_are_lower_cased_and_the_field_name_is_verbatim(self, app, model, field):
        """``str.lower`` is the contract, not "case-insensitive": ``'ß'.upper()``
        is ``'SS'``, so an upper-then-lower law would be false for German model
        names while the code is right — Django hands this function
        ``_meta.model_name``, which is itself ``object_name.lower()``."""
        assert aad_for(app, model, field) == aad_for(app.lower(), model.lower(), field)
        assert aad_for(app, model, field) == f"{app.lower()}.{model.lower()}.{field}"

    @given(app=st.text(min_size=1), model=st.text(min_size=1), field=st.text(min_size=1))
    def test_two_fields_differing_only_in_case_get_two_bindings(self, app, model, field):
        assume(field.swapcase() != field)

        assert aad_for(app, model, field) != aad_for(app, model, field.swapcase())


class TestBlindIndexLaws:
    @given(plaintext=PLAINTEXTS, aad=AADS, material=KEY_MATERIAL)
    def test_the_index_is_deterministic_and_fixed_width_base64url(self, plaintext, aad, material):
        key = EncryptionKey(id="k", material=material)

        first = blind_index.index_value(plaintext, aad=aad, key=key)

        assert first == blind_index.index_value(plaintext, aad=aad, key=key)
        assert len(first) == blind_index.INDEX_CHARS
        assert _B64URL.match(first)
        assert len(base64.urlsafe_b64decode(first + "=")) == blind_index.INDEX_BYTES

    @given(plaintext=PLAINTEXTS, aad=AADS, material=KEY_MATERIAL)
    def test_composition_forms_of_one_string_share_an_index(self, plaintext, aad, material):
        key = EncryptionKey(id="k", material=material)

        composed = blind_index.index_value(
            unicodedata.normalize("NFC", plaintext), aad=aad, key=key
        )
        decomposed = blind_index.index_value(
            unicodedata.normalize("NFD", plaintext), aad=aad, key=key
        )

        assert composed == decomposed

    @given(plaintext=st.text(alphabet=st.characters(categories=["Ll", "Lu"]), min_size=1), aad=AADS, material=KEY_MATERIAL)
    def test_case_is_not_folded(self, plaintext, aad, material):
        assume(plaintext.swapcase() != plaintext)
        assume(unicodedata.normalize("NFC", plaintext.swapcase()) != unicodedata.normalize("NFC", plaintext))
        key = EncryptionKey(id="k", material=material)

        assert blind_index.index_value(plaintext, aad=aad, key=key) != blind_index.index_value(
            plaintext.swapcase(), aad=aad, key=key
        )

    @given(plaintext=PLAINTEXTS, aad=AADS, other_aad=AADS, material=KEY_MATERIAL)
    def test_two_columns_never_share_an_index_for_the_same_value(
        self, plaintext, aad, other_aad, material
    ):
        assume(aad != other_aad)
        key = EncryptionKey(id="k", material=material)

        assert blind_index.index_value(plaintext, aad=aad, key=key) != blind_index.index_value(
            plaintext, aad=other_aad, key=key
        )

    @given(plaintext=PLAINTEXTS, aad=AADS, material=KEY_MATERIAL, other=KEY_MATERIAL)
    def test_two_keys_never_share_an_index_for_the_same_value(
        self, plaintext, aad, material, other
    ):
        assume(material != other)

        assert blind_index.index_value(
            plaintext, aad=aad, key=EncryptionKey(id="k", material=material)
        ) != blind_index.index_value(plaintext, aad=aad, key=EncryptionKey(id="k", material=other))

    @given(material=KEY_MATERIAL, aad=AADS)
    def test_the_index_key_is_never_the_encryption_key(self, material, aad):
        key = EncryptionKey(id="k", material=material)

        assert blind_index.derive_index_key(key, aad=aad) != material

    @given(plaintext=PLAINTEXTS, aad=AADS, keyset=keysets(max_keys=4))
    def test_candidates_cover_every_key_newest_first_and_start_with_the_write_index(
        self, plaintext, aad, keyset
    ):
        candidates = blind_index.index_candidates(plaintext, aad=aad, keyset=keyset)

        assert len(candidates) == len(keyset)
        assert candidates[0] == blind_index.index_for_write(plaintext, aad=aad, keyset=keyset)
        assert candidates == [
            blind_index.index_value(plaintext, aad=aad, key=key) for key in keyset
        ]
