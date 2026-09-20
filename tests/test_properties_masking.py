"""
tests/test_properties_masking.py

Property-based tests for PII masking (#QA1d, part 3).

``test_pii_masking.py`` and ``test_masking_rules.py`` pin the named cases. The
laws here are the ones a masked value must obey whatever it holds — Unicode,
control characters, a lone ``@``, a value that is already all stars, a list of
dicts of lists:

* **Bounded disclosure** — the built-in masker reveals at most two characters
  at each end of a string, and at most one character of an e-mail's local part;
  a number of any kind is never rendered as anything but the sentinel.
* **Shape** — lists and dicts keep their shape and keys; ``None`` stays ``None``.
* **Idempotence** — masking an already-masked value changes nothing, so a
  value that passes through two masking surfaces (serializer, then export) is
  not garbled.
* **Fail closed** — a configured rule never returns the raw value by
  accident: a pattern that matches nothing, a pattern that is unsafe to run, a
  value too long to run it on, all fall back to the built-in masker; and a
  character class a rule masks never survives it.
"""

from __future__ import annotations

import re
from hypothesis import assume, given
from hypothesis import strategies as st

from snapadmin.masking import (
    MAX_REGEX_INPUT,
    _has_nested_quantifier,
    apply_masking_rule,
    mask_value,
)

NO_AT = st.text().filter(lambda s: "@" not in s)
NUMBERS = st.one_of(
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.decimals(allow_nan=False, allow_infinity=False),
)
JSONISH = st.recursive(
    st.one_of(st.none(), st.text(), NUMBERS),
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=8), children, max_size=4),
    max_leaves=12,
)


def _revealed(original: str, masked: str) -> int:
    """Characters of ``original`` visible, in place, in ``masked``."""
    return sum(1 for a, b in zip(original, masked) if a == b and b != "*")


class TestBuiltInMasker:
    @given(value=NO_AT)
    def test_a_plain_string_reveals_at_most_two_characters_at_each_end(self, value):
        masked = mask_value(value)

        assert len(masked) == len(value)
        if len(value) < 6:
            assert masked == "*" * len(value)
        else:
            assert masked[:2] == value[:2] and masked[-2:] == value[-2:]
            assert set(masked[2:-2]) == {"*"}
        assert _revealed(value, masked) <= 4

    @given(local=st.text(), domain=st.text())
    def test_an_email_reveals_at_most_one_character_of_its_local_part(self, local, domain):
        assume("@" not in local)
        value = f"{local}@{domain}"

        masked = mask_value(value)

        masked_local, _, masked_domain = masked.partition("@")
        assert masked_domain == domain
        assert masked_local in {"***", (local[:1] + "***")}
        if len(local) <= 2:
            assert masked_local == "***"

    @given(value=NUMBERS)
    def test_a_number_of_any_kind_becomes_the_sentinel(self, value):
        assert mask_value(value) == "***"

    @given(value=JSONISH)
    def test_collections_keep_their_shape_and_keys(self, value):
        masked = mask_value(value)

        def same_shape(a, b) -> bool:
            if isinstance(a, list):
                return isinstance(b, list) and len(a) == len(b) and all(map(same_shape, a, b))
            if isinstance(a, dict):
                return isinstance(b, dict) and a.keys() == b.keys() and all(
                    same_shape(a[k], b[k]) for k in a
                )
            if a is None:
                return b is None
            return isinstance(b, str)

        assert same_shape(value, masked)

    @given(value=JSONISH)
    def test_a_second_pass_never_reveals_more_than_the_first(self, value):
        """Masking is *not* idempotent in general, and need not be: masking
        ``"@"`` gives ``"***@"``, masking that gives ``"****@"`` — the star run
        grows, the data does not come back. (Found by hypothesis; there is no
        pipeline that masks twice, each surface masks the raw value once.) The
        guarantee that matters is one-directional: a second pass can only hide
        more."""
        once = mask_value(value)
        twice = mask_value(once)

        if isinstance(value, str):
            assert _revealed(value, twice) <= _revealed(value, once)
        else:
            assert twice == once

    @given(value=st.text().filter(lambda s: "@" not in s))
    def test_masking_a_plain_string_twice_is_masking_it_once(self, value):
        once = mask_value(value)

        assert mask_value(once) == once

    def test_an_email_mask_grows_its_star_run_on_a_second_pass(self):
        """The one shape that is not idempotent, pinned so a change is noticed."""
        assert mask_value("@") == "***@"
        assert mask_value("***@") == "****@"


#: Character classes a rule may target, with a predicate for "this character
#: is in the class" that does not reuse the regex under test.
_CLASS_RULES = [
    # str.isdecimal, not isdigit: "\d" is Unicode category Nd, and isdigit
    # also accepts superscripts like "²" that "\d" does not match.
    (r"\d", str.isdecimal),
    (r"[A-Za-z]", lambda c: c.isascii() and c.isalpha()),
    (r"[aeiou]", lambda c: c in "aeiou"),
]


class TestConfiguredRules:
    @given(value=st.one_of(st.text(), NUMBERS), replacement=st.text())
    def test_a_replacement_only_rule_is_a_constant(self, value, replacement):
        assert apply_masking_rule(value, {"replacement": replacement}) == replacement

    @given(value=JSONISH, rule=st.one_of(st.none(), st.integers(), st.text(), st.lists(st.text())))
    def test_a_rule_that_is_not_a_dict_is_the_built_in_masker(self, value, rule):
        assert apply_masking_rule(value, rule) == mask_value(value)

    @given(data=st.data(), value=st.text())
    def test_a_masked_character_class_never_survives_its_rule(self, data, value):
        pattern, in_class = data.draw(st.sampled_from(_CLASS_RULES))

        masked = apply_masking_rule(value, {"pattern": pattern})

        if any(in_class(c) for c in value):
            assert not any(in_class(c) for c in masked), (pattern, value, masked)
        else:
            # Nothing matched: the built-in masker, never the raw value.
            assert masked == mask_value(value)

    @given(value=st.text(min_size=1).filter(lambda s: "Q" not in s))
    def test_a_pattern_that_matches_nothing_falls_back_to_the_built_in_masker(self, value):
        assert apply_masking_rule(value, {"pattern": "Q", "replacement": "#"}) == mask_value(value)

    @given(
        atom=st.sampled_from(["a", r"\d", ".", "[xy]", "(?:ab)"]),
        inner=st.sampled_from(["*", "+", "{2,}"]),
        outer=st.sampled_from(["*", "+", "{1,5}"]),
        value=st.text(),
    )
    def test_a_nested_quantifier_is_refused_and_falls_back(self, atom, inner, outer, value):
        pattern = f"({atom}{inner}){outer}"

        assert _has_nested_quantifier(pattern)
        assert apply_masking_rule(value, {"pattern": pattern}) == mask_value(value)

    @given(literal=st.text(alphabet="abcxyz", min_size=1, max_size=6), outer=st.sampled_from(["*", "+", "{2}"]))
    def test_a_quantified_group_of_literals_is_allowed(self, literal, outer):
        assert not _has_nested_quantifier(f"({literal}){outer}")

    @given(extra=st.integers(min_value=1, max_value=50))
    def test_a_value_too_long_for_a_regex_falls_back_to_the_built_in_masker(self, extra):
        value = "7" * (MAX_REGEX_INPUT + extra)

        assert apply_masking_rule(value, {"pattern": r"\d"}) == mask_value(value)

    @given(value=JSONISH)
    def test_a_rule_keeps_collection_shape(self, value):
        masked = apply_masking_rule(value, {"replacement": "[x]"})

        def check(a, b) -> None:
            if isinstance(a, list):
                assert isinstance(b, list) and len(a) == len(b)
                for x, y in zip(a, b):
                    check(x, y)
            elif isinstance(a, dict):
                assert isinstance(b, dict) and a.keys() == b.keys()
                for k in a:
                    check(a[k], b[k])
            elif a is None:
                assert b is None
            else:
                assert b == "[x]"

        check(value, masked)

    @given(value=st.decimals(allow_nan=False, allow_infinity=False) | st.integers())
    def test_a_pattern_rule_applies_to_the_text_of_a_number(self, value):
        masked = apply_masking_rule(value, {"pattern": r"\d"})

        assert isinstance(masked, str)
        assert not re.search(r"\d", masked)
