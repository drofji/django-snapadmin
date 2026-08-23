"""
tests/test_ai_entry_points.py

SnapAdmin ships two machine-readable entry points so an AI coding assistant can
work with the library without guessing:

* the ``snapadmin`` package docstring — a quickstart plus a module map, the only
  layer that reaches every ``pip install`` (no network, no GitHub);
* ``llms.txt`` (llmstxt.org) — a map of the documentation, published at
  ``https://drofji.github.io/django-snapadmin/llms.txt`` from ``docs/`` and
  mirrored at the repo root so it travels with the sdist.

Both go stale silently: a renamed module or a dropped docs section breaks nothing
at import time, it just teaches an assistant something false. These tests pin the
map to reality — every module the docstring names must import, and every docs
anchor ``llms.txt`` links to must exist in ``docs/index.html``.
"""

import importlib
import re
from pathlib import Path

import pytest

import snapadmin


REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_LLMS = REPO_ROOT / "llms.txt"
DOCS_LLMS = REPO_ROOT / "docs" / "llms.txt"
DOCS_INDEX = REPO_ROOT / "docs" / "index.html"

DOCS_BASE = "https://drofji.github.io/django-snapadmin/"

#: Modules named in the docstring's "Module map" that must stay importable.
#: Django-backed modules are imported through the configured test settings.
MAPPED_MODULES = [
    "snapadmin.models",
    "snapadmin.fields",
    "snapadmin.validators",
    "snapadmin.admin",
    "snapadmin.widgets",
    "snapadmin.nesting",
    "snapadmin.sanitize",
    "snapadmin.views",
    "snapadmin.urls",
    "snapadmin.api.views",
    "snapadmin.api.serializers",
    "snapadmin.api.filters",
    "snapadmin.api.graphql",
    "snapadmin.api.authentication",
    "snapadmin.sso",
    "snapadmin.api.exports",
    "snapadmin.exporting",
    "snapadmin.api.users",
    "snapadmin.api.health",
    "snapadmin.api.reindex",
    "snapadmin.api.offline",
    "snapadmin.audit",
    "snapadmin.masking",
    "snapadmin.backup",
    "snapadmin.monitoring",
    "snapadmin.health",
    "snapadmin.alerts",
    "snapadmin.logging_config",
    "snapadmin.reindexing",
    "snapadmin.etl",
    "snapadmin.db",
    "snapadmin.tasks",
    "snapadmin.registry",
    "snapadmin.checks",
    "snapadmin.diagnostics",
    "snapadmin.licensing",
    "snapadmin.quickstart",
    "snapadmin.integrate",
]

#: Management commands the docstring advertises, as command-module names.
MAPPED_COMMANDS = [
    "snapadmin_info",
    "snapadmin_license_check",
    "snapadmin_reindex",
    "snapadmin_audit_export",
    "snapadmin_health_alert",
    "snapadmin_db_backup",
    "snapadmin_purge_expired_data",
    "snapadmin_send_error_digest",
]


def _docstring() -> str:
    assert snapadmin.__doc__ is not None, "snapadmin package docstring went missing"
    return snapadmin.__doc__


# ─────────────────────────────────────────────────────────────────────────────
# The package docstring — the layer that ships with every install
# ─────────────────────────────────────────────────────────────────────────────

class TestPackageDocstring:
    def test_has_quickstart_and_module_map_sections(self):
        doc = _docstring()
        for heading in ("Quickstart", "Module map", "Settings", "Optional extras"):
            assert heading in doc, f"docstring lost its {heading!r} section"

    def test_quickstart_shows_the_three_steps(self):
        """An assistant should be able to copy the whole flow out of the docstring."""
        doc = _docstring()
        for step in (
            "class Product(snap_models.SnapModel):",
            "SNAPADMIN_REST_API_ENABLED = True",
            "SnapModel.register_all_admins()",
        ):
            assert step in doc, f"quickstart no longer shows {step!r}"

    def test_points_at_the_machine_readable_docs_map(self):
        assert f"{DOCS_BASE}llms.txt" in _docstring()

    @pytest.mark.parametrize("module_path", MAPPED_MODULES)
    def test_mapped_module_is_named_and_importable(self, module_path):
        """A module map that names a module which no longer exists is worse than none."""
        assert module_path in _docstring(), f"{module_path} dropped out of the module map"
        importlib.import_module(module_path)

    @pytest.mark.parametrize("command", MAPPED_COMMANDS)
    def test_mapped_management_command_exists(self, command):
        assert command in _docstring(), f"{command} dropped out of the docstring"
        importlib.import_module(f"snapadmin.management.commands.{command}")

    def test_documented_extras_match_pyproject(self):
        """Every extra offered by the package must be listed, and vice versa."""
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        extras_block = pyproject.split("[tool.poetry.extras]", 1)[1].split("\n[", 1)[0]
        declared = {
            line.split("=", 1)[0].strip()
            for line in extras_block.splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
        doc = _docstring()
        for extra in declared:
            assert f"``{extra}``" in doc, f"extra {extra!r} is undocumented in the docstring"


# ─────────────────────────────────────────────────────────────────────────────
# llms.txt — the docs map for assistants with web access
# ─────────────────────────────────────────────────────────────────────────────

class TestLlmsTxt:
    def test_exists_in_both_locations(self):
        assert DOCS_LLMS.is_file(), "docs/llms.txt is what GitHub Pages publishes"
        assert ROOT_LLMS.is_file(), "the root copy is what ships in the sdist"

    def test_the_two_copies_are_identical(self):
        """Two copies that drift give an assistant two different answers."""
        assert ROOT_LLMS.read_bytes() == DOCS_LLMS.read_bytes()

    def test_follows_the_llmstxt_structure(self):
        text = DOCS_LLMS.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0] == "# SnapAdmin (django-snapadmin)", "must open with a single H1 title"
        assert any(
            line.startswith("> ") for line in lines[:5]
        ), "must carry a blockquote summary right after the title"
        assert "## Getting started" in text
        assert "## Configuration reference" in text

    def test_is_shipped_in_the_sdist(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # The block ends at the first line that is just "]" — inner "]" characters
        # belong to per-entry `format = [...]` tables.
        include_block = pyproject.split("include = [", 1)[1].split("\n]", 1)[0]
        assert 'path = "llms.txt"' in include_block
        assert '"sdist"' in include_block

    def test_every_docs_anchor_it_links_to_exists(self):
        """Guards against a docs section being renamed out from under the map."""
        index_html = DOCS_INDEX.read_text(encoding="utf-8")
        anchors = set(re.findall(r'id="([^"]+)"', index_html))

        linked = re.findall(rf"{re.escape(DOCS_BASE)}#([\w-]+)", DOCS_LLMS.read_text(encoding="utf-8"))
        assert linked, "llms.txt stopped linking into the documentation"

        missing = sorted({anchor for anchor in linked if anchor not in anchors})
        assert not missing, f"llms.txt links to docs anchors that no longer exist: {missing}"

    def test_covers_the_primary_documentation_sections(self):
        """The sections an assistant needs most must never be dropped from the map."""
        text = DOCS_LLMS.read_text(encoding="utf-8")
        for anchor in (
            "#installation",
            "#snap-model",
            "#snap-fields",
            "#admin-registration",
            "#api-rest",
            "#api-graphql",
            "#elasticsearch",
            "#env-vars",
        ):
            assert f"{DOCS_BASE}{anchor}" in text, f"llms.txt no longer links {anchor}"

    def test_readme_advertises_it(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert f"{DOCS_BASE}llms.txt" in readme
