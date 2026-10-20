"""
tests/test_assertions_can_fail.py

A test that cannot fail is worse than no test: it occupies the name of a
guarantee, counts towards the total, keeps the line covered, and protects
nothing. This suite reads the test suite itself and refuses two shapes of it.

Both were found by hand during #QA1b and both had shipped:

* ``assert result.errors is None or True``, a bare ``assert True`` under a
  comment claiming "no exception means it worked", and ``assert "ERROR" in
  output or True`` **twice** — four assertions that no outcome could fail;
* fifty-one tests that assert nothing at all, where "it did not raise" was the
  entire test. Some of those are legitimate (a validator accepting valid input
  *is* the absence of an exception), but the shape hides the illegitimate ones,
  so the set is frozen below and may only shrink.

The rule this encodes is in ``CONTRIBUTING.md`` → *Testing methodology* (section 5,
"Testing the tests"): an assertion you have never seen fail is a claim, not a test.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

TESTS_ROOT = pathlib.Path(__file__).resolve().parent

#: Ways a test can assert without the word ``assert``: an expected exception, a
#: mock call assertion, a query-count budget, one of Django's ``assert*``
#: helpers. A test using one of these is asserting something real.
_ASSERTION_HELPERS = (
    "raises",
    "warns",
    "assert_called",
    "assert_not_called",
    "assert_called_once",
    "assert_called_with",
    "assert_has_calls",
    "assert_any_call",
    "assertNumQueries",
    "assertRaises",
    "assertWarns",
    "assertRedirects",
    "assertContains",
    "assertTemplateUsed",
    "assertQuerySetEqual",
    "assertFormError",
    "fail",
)

#: Tests whose whole assertion is "this call did not raise", frozen as it stood
#: when the guard was written (#QA1b, 2026-09-14), each with the reason that is
#: its whole contract (#QA2f, 2026-10-19: 51 → 29 — the rest gained assertions
#: about what the call returned, logged or left in place). **This list may only get
#: shorter.** Adding to it needs a deliberate decision and a reason; the default
#: answer is to assert what the call returned or changed instead. Most of these
#: are validators and idempotency checks where raising genuinely is the only
#: failure mode — but a frozen list makes that a stated claim rather than an
#: accident, and stops the shape spreading.
ASSERTION_FREE_TESTS: dict[str, str] = {
    "test_api_token_validation.py::test_valid_format": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_backup.py::test_env_included_with_recipients_ok": (
        "a guard: passing is not raising BackupError — the refusing case is the test above it"
    ),
    "test_export.py::test_masked_field_empty_set_is_a_noop": (
        "a validator over export filters; the rejecting cases are in the same class"
    ),
    "test_export.py::test_valid_filters_pass": (
        "a validator over export filters; the rejecting cases are in the same class"
    ),
    "test_fields.py::test_accepts_e164": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_fields.py::test_accepts_local_format": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_fields.py::test_accepts_lowercase": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_fields.py::test_accepts_six_char_hex": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_fields.py::test_accepts_three_char_hex": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_importing.py::test_masked_field_is_allowed_for_a_superuser_requester": (
        "a write-surface guard: allowing is not raising SnapImportError; the refusals are beside it"
    ),
    "test_importing.py::test_unmasked_writable_model_passes": (
        "a write-surface guard: allowing is not raising SnapImportError; the refusals are beside it"
    ),
    "test_restore.py::test_matching_checksum_passes": (
        "a guard: a matching bundle is one that raises no RestoreError; the mismatch case is beside it"
    ),
    "test_scaffold_validate.py::test_empty_existing_directory_is_fine": (
        "a pre-flight validator; the refusing cases are in the same module"
    ),
    "test_scaffold_validate.py::test_find_spec_error_is_not_treated_as_a_conflict": (
        "a pre-flight validator; the refusing cases are in the same module"
    ),
    "test_scaffold_validate.py::test_missing_directory_is_fine": (
        "a pre-flight validator; the refusing cases are in the same module"
    ),
    "test_scaffold_validate.py::test_valid_unused_name_passes": (
        "a pre-flight validator; the refusing cases are in the same module"
    ),
    "test_validators.py::test_accepts_valid_numbers": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_allowed_extension_passes": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_ascii_content_passes_utf8_check": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_extension_check_is_case_insensitive": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_file_exactly_at_limit_passes": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_file_within_limit_passes": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_multiple_allowed_extensions": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_multiple_encodings_any_match_passes": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_no_extension_restriction_passes_everything": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_no_size_limit_passes_large_file": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_passes_all_constraints": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_string_extension_accepted": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
    "test_validators.py::test_utf8_content_passes": (
        "a validator: accepting is the absence of an exception — its rejecting twins sit beside it"
    ),
}


def _asserting_helpers(tree: ast.AST) -> set[str]:
    """Module-level ``_assert*`` / ``assert_*`` helpers whose own body asserts.

    ``_assert_site_matches(found, site)`` fails a test exactly like an inline
    ``assert``; a helper of that name that asserts nothing does not count.
    """
    return {
        node.name
        for node in getattr(tree, "body", [])
        if isinstance(node, ast.FunctionDef)
        and node.name.lstrip("_").startswith("assert")
        and any(isinstance(inner, (ast.Assert, ast.Raise)) for inner in ast.walk(node))
    }


def _calls_asserting_helper(function: ast.AST, helpers: set[str]) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in helpers
        for node in ast.walk(function)
    )


def _test_functions():
    """(file name, function node) for every test in the suite.

    Walks subdirectories too, so the browser suite under ``tests/e2e/`` is held
    to the same rules; a file there is named by its path relative to
    ``tests/`` (``e2e/test_x.py``), a top-level one by its bare name as before.
    """
    for file_name, node, _helpers in _test_functions_with_helpers():
        yield file_name, node


def _test_functions_with_helpers():
    """Like :func:`_test_functions`, plus each module's asserting helpers."""
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        file_name = path.relative_to(TESTS_ROOT).as_posix()
        helpers = _asserting_helpers(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                yield file_name, node, helpers


def _uses_web_first_assertion(function: ast.AST) -> bool:
    """Whether ``function`` asserts through Playwright's ``expect(...)``.

    ``expect(locator).to_have_count(2)`` fails the test like an ``assert`` does,
    but carries no ``assert`` keyword. Only a call of a ``to_*`` / ``not_to_*``
    method made directly on the result of ``expect(...)`` counts — a bare
    ``expect(...)`` with no matcher asserts nothing and is not accepted.
    """
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith(("to_", "not_to_"))
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "expect"
        ):
            return True
    return False


def _is_truthy_constant(node: ast.AST) -> bool:
    """Whether ``node`` is a literal that is always true."""
    return isinstance(node, ast.Constant) and bool(node.value)


class TestNoAssertionIsUnfalsifiable:
    """An assertion whose truth does not depend on the code under test."""

    def test_no_assertion_is_a_truthy_constant(self):
        offenders = [
            f"{file_name}:{node.lineno}"
            for file_name, fn in _test_functions()
            for node in ast.walk(fn)
            if isinstance(node, ast.Assert) and _is_truthy_constant(node.test)
        ]
        assert not offenders, (
            f"assert(s) on a literal that is always true: {offenders}. "
            "`assert True` under a comment explaining what it means is not a test — "
            "assert the value, the call, or the side effect the comment describes."
        )

    def test_no_assertion_is_short_circuited_by_a_truthy_constant(self):
        """``assert x or True`` passes whatever ``x`` is."""
        offenders = []
        for file_name, fn in _test_functions():
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assert):
                    continue
                test = node.test
                if (
                    isinstance(test, ast.BoolOp)
                    and isinstance(test.op, ast.Or)
                    and any(_is_truthy_constant(value) for value in test.values)
                ):
                    offenders.append(f"{file_name}:{node.lineno}")
        assert not offenders, (
            f"assert(s) short-circuited by a truthy literal: {offenders}. "
            "`... or True` makes the whole assertion unfalsifiable; decide what the "
            "expected outcome actually is and assert that."
        )


class TestEveryTestAssertsSomething:
    """"It did not raise" is a claim that has to be made deliberately."""

    def _assertion_free(self) -> set[str]:
        found = set()
        for file_name, fn, helpers in _test_functions_with_helpers():
            if any(isinstance(node, ast.Assert) for node in ast.walk(fn)):
                continue
            if _calls_asserting_helper(fn, helpers):
                continue
            if _uses_web_first_assertion(fn):
                continue
            source = ast.dump(fn)
            if any(helper in source for helper in _ASSERTION_HELPERS):
                continue
            found.add(f"{file_name}::{fn.name}")
        return found

    def test_no_new_test_asserts_nothing(self):
        new = sorted(self._assertion_free() - set(ASSERTION_FREE_TESTS))
        assert not new, (
            f"test(s) with no assertion of any kind: {new}. Assert what the call "
            "returned, wrote or changed. If the absence of an exception really is the "
            "whole contract, say so in the test and add it to ASSERTION_FREE_TESTS "
            "with a reason."
        )

    def test_the_frozen_list_has_no_stale_entries(self):
        """A test that gained an assertion must leave the list, or it rots."""
        stale = sorted(set(ASSERTION_FREE_TESTS) - self._assertion_free())
        assert not stale, (
            f"these now assert something and must come out of ASSERTION_FREE_TESTS: {stale}"
        )

    def test_the_list_only_ever_shrinks(self):
        """A ceiling, so the shape cannot spread while the list is being burned down."""
        assert len(ASSERTION_FREE_TESTS) <= 29, len(ASSERTION_FREE_TESTS)


class TestWebFirstAssertionsAreRecognisedExactly:
    """The browser suite asserts through ``expect(...).to_*()``; the guard must
    accept exactly that and nothing that merely looks like it."""

    @pytest.mark.parametrize(
        "body, recognised",
        [
            ("expect(page).to_have_url('/admin/')", True),
            ("expect(rows).not_to_be_visible()", True),
            ("expect(page)", False),
            ("result.to_have_count(1)", False),
            ("helpers.expect(page).to_have_url('/')", False),
        ],
    )
    def test_only_a_matcher_called_on_expect_counts(self, body, recognised):
        function = ast.parse(f"def test_x():\n    {body}\n").body[0]

        assert _uses_web_first_assertion(function) is recognised


class TestAssertingHelpersCount:
    """A test that delegates to ``_assert_*`` asserts — when the helper itself does."""

    SOURCE = '''
def _assert_matches(found, site):
    assert found == site

def _assert_nothing(found):
    return found

def test_uses_a_real_helper():
    _assert_matches(1, 1)

def test_uses_an_empty_helper():
    _assert_nothing(1)
'''

    def test_only_a_helper_that_asserts_counts(self):
        tree = ast.parse(self.SOURCE)
        helpers = _asserting_helpers(tree)
        tests = {node.name: node for node in tree.body if node.name.startswith("test_")}

        assert helpers == {"_assert_matches"}
        assert _calls_asserting_helper(tests["test_uses_a_real_helper"], helpers) is True
        assert _calls_asserting_helper(tests["test_uses_an_empty_helper"], helpers) is False

    def test_every_frozen_entry_carries_a_reason(self):
        assert all(reason.strip() for reason in ASSERTION_FREE_TESTS.values())
