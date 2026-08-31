"""
Tests for the shipped ``snapadmin/js/admin.js`` asset (#JS2a, #JS2d).

Pattern matches ``tests/test_offline.py``: the shipped JS has no runtime test
harness in this Python suite, so these tests read the asset as text and pin
the tokens that make the fix real — the select2 feature-detection guard, the
opt-in selector, independent ready handlers, and English-only, correctly
linked shipped comments.
"""

import re
from pathlib import Path

import pytest

STATIC_ROOT = Path(__file__).resolve().parent.parent / "snapadmin" / "static"
ADMIN_JS = STATIC_ROOT / "snapadmin" / "js" / "admin.js"


@pytest.fixture(scope="module")
def source():
    assert ADMIN_JS.exists(), f"missing asset: {ADMIN_JS}"
    return ADMIN_JS.read_text(encoding="utf-8")


class TestSelect2Guard:
    def test_guards_on_select2_feature_detection(self, source):
        assert "$.fn.select2" in source

    def test_guard_covers_both_select2_call_sites(self, source):
        """The guard must protect the #changelist-filter block and the
        opt-in block alike — a single early return in activateSelect2()."""
        activate = source.split("function activateSelect2", 1)[1].split("function activateRowClick", 1)[0]
        guard_index = activate.index("$.fn.select2")
        first_call = activate.index(".select2(")
        assert guard_index < first_call, "the guard must run before the first .select2() call"
        assert activate.count(".select2(") == 2


class TestOptInSelector:
    """#JS2a — DECISIONS.md D3: opt-in, never opt-out."""

    def test_opt_in_marker_is_present(self, source):
        assert "snapadmin-select2" in source
        assert "data-snapadmin-select2" in source

    def test_no_unscoped_select_initialisation_remains(self, source):
        """The old opt-out selector — every <select> minus a denylist — is
        exactly what reached the changelist action dropdown and broke bulk
        actions on the themed admin with no error anywhere."""
        assert "$('select').not(" not in source
        assert '$("select").not(' not in source


class TestIndependentReadyHandlers:
    """#JS2a — a TypeError in one initialiser must not take the other down.

    jQuery 3's ``.ready(fn)`` attaches each callback as its own promise
    handler off the same resolved deferred, so two separate ``.ready()``
    registrations really are independent — one throwing does not stop the
    other from running.
    """

    def test_select2_and_row_click_register_independently(self, source):
        assert source.count("$(document).ready(") >= 2

    def test_activate_calls_are_not_in_the_same_ready_block(self, source):
        blocks = re.findall(r"\$\(document\)\.ready\(function\s*\(\)\s*\{([^}]*)\}\);", source)
        assert blocks, "no $(document).ready(...) blocks found"
        assert not any("activateSelect2" in b and "activateRowClick" in b for b in blocks)


class TestShippedHygiene:
    """#JS2d — hygiene sweep: no retired package name, no non-English comment."""

    def test_no_reference_to_the_retired_import_root(self, source):
        assert "drofji_admin" not in source

    def test_header_names_the_current_shipped_path(self, source):
        first_line = source.splitlines()[0]
        assert first_line == "// snapadmin/static/snapadmin/js/admin.js"

    def test_no_cyrillic_comment(self, source):
        assert not re.search(r"[Ѐ-ӿ]", source)
