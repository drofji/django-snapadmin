"""
tests/test_fuzz_encryption_config.py

Adversarial key configuration for field encryption (#QA1d, part 4).

Encryption keys are hand-written into an environment variable, a mounted file,
settings or a provider's return value — and the reliable way to get key
material into an exception is to write an entry wrong: backwards
(``<key>:<id>``), quoted, padded, truncated, doubled, or as the wrong type. For
every such configuration ``hypothesis`` can assemble out of real keys, the
invariants are:

* configuration either yields a keyset or raises ``ImproperlyConfigured`` —
  never ``AttributeError``/``TypeError``/``binascii.Error`` from the parser;
* no error message ever carries any real key's material, in any spelling.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from hypothesis import given
from hypothesis import strategies as st

from snapadmin.encryption.keys import (
    KEY_BYTES,
    KeySource,
    _keys_from_mappings,
    _parse_entries,
    encode_key,
)

MATERIAL = st.binary(min_size=KEY_BYTES, max_size=KEY_BYTES)
IDS = st.one_of(st.from_regex(r"\A[A-Za-z0-9_-]{1,20}\Z"), st.text(max_size=12))


def _spellings(encoded: str) -> set[str]:
    """Every way a key's material could appear in text: padded, unpadded, and
    long enough fragments of it that a partial echo still counts."""
    unpadded = encoded.rstrip("=")
    return {encoded, unpadded, unpadded[:20], unpadded[-20:]}


@st.composite
def env_entries(draw) -> tuple[str, list[str]]:
    """A key-file / env-var body mixing correct and mistaken entries."""
    keys = [encode_key(draw(MATERIAL)) for _ in range(draw(st.integers(1, 3)))]
    lines = []
    for encoded in keys:
        key_id = draw(IDS)
        shape = draw(st.sampled_from(["ok", "reversed", "bare", "quoted", "truncated", "doubled", "spaced"]))
        lines.append(
            {
                "ok": f"{key_id}:{encoded}",
                "reversed": f"{encoded}:{key_id}",
                "bare": encoded,
                "quoted": f'"{key_id}:{encoded}"',
                "truncated": f"{key_id}:{encoded[:-3]}",
                "doubled": f"{key_id}:{encoded}{encoded}",
                "spaced": f"  {key_id} : {encoded}  ",
            }[shape]
        )
    separator = draw(st.sampled_from([",", "\n", ", ", "\n# rotated\n"]))
    return separator.join(lines), keys


class TestKeyEntryParsing:
    @given(body_and_keys=env_entries())
    def test_a_key_file_parses_or_refuses_without_echoing_material(self, body_and_keys):
        body, keys = body_and_keys

        try:
            parsed = _parse_entries(body, KeySource.ENV)
        except ImproperlyConfigured as exc:
            message = str(exc)
            for encoded in keys:
                for spelling in _spellings(encoded):
                    assert spelling not in message, message
            return
        assert parsed
        assert all(len(key.material) == KEY_BYTES for key in parsed)

    @given(
        key_value=st.one_of(
            st.integers(), st.binary(), st.none(), st.lists(st.text(), max_size=2),
            st.dictionaries(st.text(max_size=3), st.text(max_size=3), max_size=2),
        ),
        key_id=st.one_of(st.none(), st.integers(), st.text(max_size=8)),
    )
    def test_a_mapping_with_a_non_string_key_is_improperly_configured(self, key_value, key_id):
        """Regression (#QA1d): a provider returning ``{"id": ..., "key": 123}`` or
        bytes raised ``AttributeError``/``TypeError`` out of the parser instead of
        the configuration error every other mistake produces."""
        with pytest.raises(ImproperlyConfigured):
            _keys_from_mappings([{"id": key_id, "key": key_value}], KeySource.PROVIDER)

    @given(material=MATERIAL, key_id=st.from_regex(r"\A[A-Za-z0-9_-]{1,20}\Z"))
    def test_a_well_formed_mapping_round_trips(self, material, key_id):
        [key] = _keys_from_mappings([{"id": key_id, "key": encode_key(material)}], KeySource.SETTINGS)

        assert (key.id, key.material) == (key_id, material)
        assert encode_key(material) not in repr(key)
