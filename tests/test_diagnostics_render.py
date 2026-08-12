"""
tests/test_diagnostics_render.py — the ``snapadmin_info`` text renderer (#CLI7)

Two collector shapes used to render unreadably, and both are common:

* a **uniform list of dicts** (the model inventory) repeated every key name once per row —
  11 models × 5 keys = 55 lines where a table needs 13;
* a **run of booleans** (the feature-adoption checklist) took one line per flag, so the
  "what's on / what's off" question the checklist exists to answer needed a scroll.

These tests pin the table and the grouped on/off runs, and the fallbacks that keep the
generic renderer in charge of anything that is not exactly those shapes.
"""

import pytest

from snapadmin.diagnostics.registry import Collector
from snapadmin.diagnostics.render import render_report


def _collector(name="demo", title="Demo", icon="🧪"):
    return Collector(
        name=name, title=title, icon=icon, order=1, health_probe=False, fn=lambda verbose: {}
    )


def _render(data, *, brief=False):
    return render_report([(_collector(), data)], brief=brief)


# ── uniform list of dicts → table ────────────────────────────────────────────

class TestTableRendering:
    ROWS = [
        {"model": "demo.Alpha", "es_mode": "DB_ONLY", "retention_days": 90, "masked": True},
        {"model": "demo.LongerName", "es_mode": "DUAL", "retention_days": None, "masked": False},
    ]

    def test_headers_are_rendered_once(self):
        text = _render({"items": self.ROWS})
        assert text.count("Es mode") == 1
        assert text.count("Retention days") == 1

    def test_one_line_per_row(self):
        lines = _render({"items": self.ROWS}).splitlines()
        # section header + "Items:" + column header + rule + 2 rows
        assert len(lines) == 6

    def test_columns_are_aligned(self):
        rows = [
            line for line in _render({"items": self.ROWS}).splitlines()
            if "demo." in line
        ]
        assert len({line.index("DB_ONLY") if "DB_ONLY" in line else line.index("DUAL")
                    for line in rows}) == 1

    def test_scalars_keep_their_formatting(self):
        text = _render({"items": self.ROWS})
        assert "✓" in text and "✗" in text and "—" in text

    def test_single_item_list_is_not_a_table(self):
        """One row is a key/value block; a header + rule would be noise."""
        text = _render({"items": self.ROWS[:1]})
        assert "─" not in text
        assert "Model: demo.Alpha" in text

    def test_ragged_dicts_fall_back(self):
        text = _render({"items": [{"a": 1, "b": 2}, {"a": 3}]})
        assert "─" not in text

    def test_nested_values_fall_back(self):
        text = _render({"items": [{"a": {"x": 1}}, {"a": {"x": 2}}]})
        assert "─" not in text

    def test_scalar_list_is_untouched(self):
        text = _render({"tasks": ["alpha", "beta"]})
        assert "- alpha" in text and "- beta" in text

    def test_mixed_list_falls_back(self):
        text = _render({"items": [{"a": 1}, "loose"]})
        assert "─" not in text
        assert "- loose" in text

    def test_underscore_keys_are_not_columns(self):
        rows = [{"_meta": "x", "name": "a"}, {"_meta": "y", "name": "b"}]
        assert "Meta" not in _render({"items": rows})

    def test_all_underscore_keys_falls_back(self):
        assert "─" not in _render({"items": [{"_a": 1}, {"_a": 2}]})


# ── boolean runs → grouped on/off lines ──────────────────────────────────────

class TestFlagGrouping:
    FLAGS = {"rest_api": True, "graphql": True, "backups": False, "sso": False}

    def test_grouped_into_two_lines(self):
        lines = _render(self.FLAGS).splitlines()[1:]
        assert len(lines) == 2
        assert lines[0].strip().startswith("✓ on")
        assert lines[1].strip().startswith("✗ off")

    def test_every_flag_is_listed(self):
        text = _render(self.FLAGS)
        for label in ("Rest api", "Graphql", "Backups", "Sso"):
            assert label in text

    def test_no_key_value_lines_for_grouped_flags(self):
        assert "Rest api:" not in _render(self.FLAGS)

    def test_all_on_emits_only_the_on_line(self):
        text = _render({f"f{i}": True for i in range(4)})
        assert "✓ on" in text and "✗ off" not in text

    def test_all_off_emits_only_the_off_line(self):
        text = _render({f"f{i}": False for i in range(4)})
        assert "✗ off" in text and "✓ on" not in text

    def test_below_threshold_stays_key_value(self):
        """Three flags read fine as lines; grouping them would only obscure."""
        text = _render({"a": True, "b": False, "c": True})
        assert "A: ✓" in text
        assert "✓ on" not in text

    def test_non_boolean_siblings_still_render(self):
        text = _render({**self.FLAGS, "total": 4})
        assert "✓ on" in text
        assert "Total: 4" in text

    def test_long_run_wraps_with_hanging_indent(self):
        flags = {f"capability_number_{i}": True for i in range(20)}
        lines = _render(flags).splitlines()[1:]
        assert len(lines) > 1
        assert all(len(line) <= 100 for line in lines)
        # Continuation lines are indented past the "✓ on" marker, not back to the margin.
        indent = len(lines[1]) - len(lines[1].lstrip())
        assert indent >= lines[0].index("✓ on") + len("✓ on")


# ── unchanged conventions ────────────────────────────────────────────────────

class TestExistingConventions:
    def test_disabled_section_collapses(self):
        assert _render({"enabled": False}) == "🧪 Demo: disabled"

    def test_brief_skips_containers(self):
        text = _render({"version": "1.0", "items": [{"a": 1}, {"a": 2}]}, brief=True)
        assert "Version: 1.0" in text
        assert "─" not in text

    def test_empty_string_renders_as_a_dash(self):
        """A bare `Port:` reads like a truncated report."""
        assert "Port: —" in _render({"port": ""})

    def test_none_renders_as_a_dash(self):
        assert "Port: —" in _render({"port": None})

    def test_underscore_keys_are_hidden(self):
        assert "Secret" not in _render({"_secret": "x", "shown": 1})

    def test_nested_list_inside_a_list(self):
        text = _render({"a": [["x", "y"]]})
        assert "- x" in text and "- y" in text
