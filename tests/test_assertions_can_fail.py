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

The rule this encodes lives in ``.claude/rules.md`` → "Testing rules";
``QUALITY.md`` §13 and §30 are what it comes from.
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
#: when the guard was written (#QA1b, 2026-09-14). **This list may only get
#: shorter.** Adding to it needs a deliberate decision and a reason; the default
#: answer is to assert what the call returned or changed instead. Most of these
#: are validators and idempotency checks where raising genuinely is the only
#: failure mode — but a frozen list makes that a stated claim rather than an
#: accident, and stops the shape spreading.
ASSERTION_FREE_TESTS = frozenset(
    {
        "test_admin_site.py::test_register_admin_idempotent",
        "test_api_token_validation.py::test_valid_format",
        "test_backup.py::test_env_included_with_recipients_ok",
        "test_backup.py::test_manifest_is_never_encrypted_even_with_recipients",
        "test_checks.py::test_default_config_is_clean",
        "test_checks.py::test_register_is_idempotent",
        "test_export.py::test_masked_field_empty_set_is_a_noop",
        "test_export.py::test_missing_job_is_a_noop",
        "test_export.py::test_valid_filters_pass",
        "test_extra_settings_sync.py::test_sync_is_safe_when_extra_settings_unavailable",
        "test_fields.py::test_accepts_e164",
        "test_fields.py::test_accepts_local_format",
        "test_fields.py::test_accepts_lowercase",
        "test_fields.py::test_accepts_six_char_hex",
        "test_fields.py::test_accepts_three_char_hex",
        "test_i18n.py::test_chart_data_is_json_serialisable",
        "test_importing.py::test_local_storage_is_a_no_op_when_paths_already_match",
        "test_importing.py::test_masked_field_is_allowed_for_a_superuser_requester",
        "test_importing.py::test_skipped_run_is_reported",
        "test_importing.py::test_unmasked_writable_model_passes",
        "test_limits.py::test_decrement_swallows_an_already_expired_key",
        "test_models.py::test_register_already_registered_does_not_raise",
        "test_restore.py::test_matching_checksum_passes",
        "test_scaffold_render.py::test_no_leftover_placeholders",
        "test_scaffold_render.py::test_no_leftover_placeholders",
        "test_scaffold_validate.py::test_empty_existing_directory_is_fine",
        "test_scaffold_validate.py::test_find_spec_error_is_not_treated_as_a_conflict",
        "test_scaffold_validate.py::test_missing_directory_is_fine",
        "test_scaffold_validate.py::test_valid_unused_name_passes",
        "test_search.py::test_skips_when_es_unavailable",
        "test_search.py::test_skips_when_es_unavailable",
        "test_snap_model_decorator.py::test_the_post_migrate_es_hook_skips_it_instead_of_crashing",
        "test_task_discovery.py::test_all_tasks_importable_from_snapadmin_tasks",
        "test_validators.py::test_accepts_valid_numbers",
        "test_validators.py::test_allowed_extension_passes",
        "test_validators.py::test_ascii_content_passes_utf8_check",
        "test_validators.py::test_extension_check_is_case_insensitive",
        "test_validators.py::test_file_exactly_at_limit_passes",
        "test_validators.py::test_file_within_limit_passes",
        "test_validators.py::test_multiple_allowed_extensions",
        "test_validators.py::test_multiple_encodings_any_match_passes",
        "test_validators.py::test_no_extension_restriction_passes_everything",
        "test_validators.py::test_no_size_limit_passes_large_file",
        "test_validators.py::test_passes_all_constraints",
        "test_validators.py::test_string_extension_accepted",
        "test_validators.py::test_utf8_content_passes",
        "test_version_sync.py::test_docs_index_footer",
        "test_version_sync.py::test_docs_index_sidebar_badge",
        "test_version_sync.py::test_docs_index_snapadmin_info_sample_output",
        "test_version_sync.py::test_docs_index_whats_new_hero",
        "test_version_sync.py::test_security_md_supported_versions_row",
    }
)


def _test_functions():
    """(file name, function node) for every test in the suite."""
    for path in sorted(TESTS_ROOT.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                yield path.name, node


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
        for file_name, fn in _test_functions():
            if any(isinstance(node, ast.Assert) for node in ast.walk(fn)):
                continue
            source = ast.dump(fn)
            if any(helper in source for helper in _ASSERTION_HELPERS):
                continue
            found.add(f"{file_name}::{fn.name}")
        return found

    def test_no_new_test_asserts_nothing(self):
        new = sorted(self._assertion_free() - ASSERTION_FREE_TESTS)
        assert not new, (
            f"test(s) with no assertion of any kind: {new}. Assert what the call "
            "returned, wrote or changed. If the absence of an exception really is the "
            "whole contract, say so in the test and add it to ASSERTION_FREE_TESTS "
            "with a reason."
        )

    def test_the_frozen_list_has_no_stale_entries(self):
        """A test that gained an assertion must leave the list, or it rots."""
        stale = sorted(ASSERTION_FREE_TESTS - self._assertion_free())
        assert not stale, (
            f"these now assert something and must come out of ASSERTION_FREE_TESTS: {stale}"
        )

    def test_the_list_only_ever_shrinks(self):
        """A ceiling, so the shape cannot spread while the list is being burned down."""
        assert len(ASSERTION_FREE_TESTS) <= 51, len(ASSERTION_FREE_TESTS)
