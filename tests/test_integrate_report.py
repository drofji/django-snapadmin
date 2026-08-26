"""Tests for :mod:`snapadmin.integrate.report` (#DOC9c)."""

from __future__ import annotations

import json
from pathlib import Path

from snapadmin.integrate.detect import ProjectContext
from snapadmin.integrate.report import render_json, render_text
from snapadmin.integrate.steps import Step


def _project(**kwargs):
    defaults = dict(
        project_dir=Path("."),
        settings_path=None,
        settings_text="",
        urls_path=None,
        urls_text="",
        requirements_text="",
    )
    defaults.update(kwargs)
    return ProjectContext(**defaults)


class TestRenderTextIcons:
    def test_present_step(self):
        step = Step("x", "X present", True, "snippet-x")
        out = render_text([step], _project())
        assert "✅ X present: already present" in out

    def test_missing_step_includes_snippet(self):
        step = Step("x", "X missing", False, "pip install x")
        out = render_text([step], _project())
        assert "❌ X missing: add this —" in out
        assert "pip install x" in out

    def test_not_checked_step_never_a_false_green(self):
        step = Step("x", "X unknown", None, "", note="run `manage.py check` to find out")
        out = render_text([step], _project())
        assert "⚠️ X unknown: not checked" in out
        assert "run `manage.py check`" in out
        assert "✅" not in out

    def test_note_on_present_step_is_shown(self):
        step = Step("x", "X present", True, "", note="a caveat worth knowing")
        out = render_text([step], _project())
        assert "a caveat worth knowing" in out


class TestRenderTextGroups:
    def test_only_populated_groups_get_a_header(self):
        out = render_text([Step("a", "A", True, "", group="must_work")], _project())
        assert "Must work" in out
        assert "Data safety" not in out
        assert "Should be configured" not in out

    def test_group_order_matches_docs_checklist(self):
        steps = [
            Step("c", "C", True, "", group="data_safety"),
            Step("a", "A", True, "", group="must_work"),
            Step("b", "B", True, "", group="should_configure"),
        ]
        out = render_text(steps, _project())
        assert out.index("Must work") < out.index("Should be configured") < out.index("Data safety")


class TestRenderJson:
    def test_includes_group_and_null_present(self):
        steps = [Step("x", "X", None, "", note="", group="data_safety")]
        payload = json.loads(render_json(steps, _project()))
        assert payload["steps"][0]["group"] == "data_safety"
        assert payload["steps"][0]["present"] is None
