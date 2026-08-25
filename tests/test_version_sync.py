"""
tests/test_version_sync.py

The project version lives in six places in the repo, plus a handful of
release-state sites (``README.md``, ``docs/releases/``, ``CHANGELOG.md``).
During the 0.1.0b7 release these were briefly inconsistent: ``docs/index.html``
announced b7 while ``pyproject.toml``, ``SECURITY.md`` and three other sites
still read b6. This file reads ``pyproject.toml`` once, as the single source
of truth, and asserts every other site agrees — so a version bump can never
again leave a site behind unnoticed.

Deliberately offline: the git tag and the PyPI release are VCS/network state,
not repo state, and are checked by hand at release time
(``.claude/commands/release.md``) rather than here.

Sites are located by their surrounding markup, not by line number — several
sections of ``docs/index.html`` grow independently of this file.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_INDEX = REPO_ROOT / "docs" / "index.html"


def _pyproject_version() -> str:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match, "pyproject.toml has no `[tool.poetry] version` line to read from"
    return match.group(1)


VERSION = _pyproject_version()


def _find(pattern: str, text: str, site: str) -> str:
    match = re.search(pattern, text, re.DOTALL | re.MULTILINE)
    assert match, f"{site}: could not locate a version string — surrounding markup changed"
    return match.group(1)


def _assert_site_matches(found: str, site: str) -> None:
    assert found == VERSION, (
        f"{site}: expected {VERSION!r} (from pyproject.toml) but found {found!r}"
    )


class TestSixVersionSites:
    """The six sites `/release` and #AUDIT1a both enumerate."""

    def test_security_md_supported_versions_row(self):
        text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        site = "SECURITY.md supported-versions row"
        found = _find(r"Latest release on PyPI \(currently `([^`]+)`\)", text, site)
        _assert_site_matches(found, site)

    def test_docs_index_sidebar_badge(self):
        text = DOCS_INDEX.read_text(encoding="utf-8")
        site = "docs/index.html sidebar badge"
        found = _find(
            r'<div class="sidebar-logo">.*?<span>Documentation v([^<]+)</span>', text, site
        )
        _assert_site_matches(found, site)

    def test_docs_index_whats_new_hero(self):
        text = DOCS_INDEX.read_text(encoding="utf-8")
        site = 'docs/index.html "What\'s New" hero'
        found = _find(r"What's New in v([^\s]+) —", text, site)
        _assert_site_matches(found, site)

    def test_docs_index_snapadmin_info_sample_output(self):
        text = DOCS_INDEX.read_text(encoding="utf-8")
        section_site = "docs/index.html #snapadmin-info section"
        section = _find(r'(<h2 id="snapadmin-info">.*?)(?=<h2)', text, section_site)
        site = "docs/index.html snapadmin_info sample output"
        found = _find(r"Version:\s*([^\s<]+)", section, site)
        _assert_site_matches(found, site)

    def test_docs_index_footer(self):
        text = DOCS_INDEX.read_text(encoding="utf-8")
        site = "docs/index.html page footer"
        found = _find(r"SnapAdmin v([^\s]+) — MIT License", text, site)
        _assert_site_matches(found, site)

    def test_pyproject_toml_is_the_version_that_gets_checked(self):
        """Sanity check on the fixture itself: it must actually read a version."""
        assert re.fullmatch(r"[0-9][0-9a-zA-Z.]*", VERSION), (
            f"pyproject.toml version {VERSION!r} doesn't look like a version string"
        )


class TestReleaseStateSites:
    """Same class of bug, different shape: presence/absence and release-state sites."""

    def test_readme_carries_no_version(self):
        """Correct since the #MKT3 rewrite — assert it, don't assume it."""
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert VERSION not in text, (
            f"README.md now contains the version {VERSION!r} — it is meant to carry none"
        )

    def test_docs_releases_has_a_file_for_the_released_version(self):
        released = REPO_ROOT / "docs" / "releases" / f"{VERSION}.txt"
        assert released.is_file(), (
            f"docs/releases/{VERSION}.txt is missing — the release notes for the "
            f"current pyproject.toml version were never filed"
        )

    def test_docs_releases_has_a_fresh_unreleased_file(self):
        unreleased = REPO_ROOT / "docs" / "releases" / "Unreleased.txt"
        assert unreleased.is_file(), "docs/releases/Unreleased.txt is missing"
        first_line = unreleased.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "Unreleased", (
            f"docs/releases/Unreleased.txt no longer starts with the 'Unreleased' "
            f"header (found {first_line!r}) — it looks like a release's notes were "
            f"never renamed out of it"
        )

    def test_changelog_top_section_matches_the_released_version(self):
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased_heading = re.search(r"^## Unreleased\s*$", text, re.MULTILINE)
        assert unreleased_heading, "CHANGELOG.md lost its `## Unreleased` heading"
        rest = text[unreleased_heading.end():]
        site = "CHANGELOG.md top released section"
        found = _find(r"^## ([^\s]+) — ", rest, site)
        _assert_site_matches(found, site)
