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
not repo state, and are checked by hand at release time rather than here.

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


# ─────────────────────────────────────────────────────────────────────────────
# llms.txt — the AI-facing entry point, and the one release-state site nothing
# in this file used to check
# ─────────────────────────────────────────────────────────────────────────────

#: Words that describe the package as not yet stable. ``llms.txt`` is usually the first and
#: sometimes the only thing an AI assistant reads about SnapAdmin, so a stale one of these does not
#: just misinform — it gets repeated as advice ("pin it, the package is beta").
_PRERELEASE_WORDS = ("beta", "alpha", "pre-release", "prerelease")

#: A version is a stable release when it carries no PEP 440 pre-release/dev segment.
_PRERELEASE_MARKER = re.compile(r"(a|b|rc|\.dev)\d*$")


class TestLlmsTxtMatchesTheReleaseState:
    """``llms.txt`` survived the 1.0.0 cut still opening with "the package is beta".

    Every other release-state site in this file was checked at release time and updated; this one
    was not, because nothing asserted it. The two copies were byte-identical and every anchor it
    linked to resolved, so ``test_ai_entry_points.py`` passed throughout — structure was pinned,
    the claim was not.
    """

    def _summary_line(self) -> str:
        """The blockquote directly under the H1 — llmstxt.org's one-paragraph summary."""
        for path in (REPO_ROOT / "llms.txt", REPO_ROOT / "docs" / "llms.txt"):
            assert path.exists(), f"{path} is missing"
        text = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
        match = re.search(r"^> (.+)$", text, re.MULTILINE)
        assert match, "llms.txt has no `> ...` summary line under its H1"
        return match.group(1)

    def test_summary_does_not_call_a_stable_release_a_prerelease(self):
        if _PRERELEASE_MARKER.search(VERSION):
            return  # genuinely a pre-release — saying so is correct
        summary = self._summary_line().lower()
        stale = [word for word in _PRERELEASE_WORDS if word in summary]
        assert not stale, (
            f"llms.txt's summary still calls the package {stale} while pyproject.toml is at the "
            f"stable version {VERSION!r} — an assistant reading it will repeat that as advice"
        )

    def test_a_prerelease_is_still_described_as_one(self):
        """The inverse: going back to a pre-release must not leave a 'stable' claim behind."""
        if not _PRERELEASE_MARKER.search(VERSION):
            return
        summary = self._summary_line().lower()
        assert any(word in summary for word in _PRERELEASE_WORDS), (
            f"pyproject.toml is at the pre-release {VERSION!r} but llms.txt's summary does not say "
            "so — it reads as a stable-release claim"
        )

    def test_both_copies_carry_the_same_summary(self):
        """Cheap here, and the failure it catches (an edit applied to one copy) is common."""
        root = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
        served = (REPO_ROOT / "docs" / "llms.txt").read_text(encoding="utf-8")
        assert root == served, "llms.txt and docs/llms.txt have drifted — edit one, cp it over the other"


class TestLlmsTxtCarriesTheAdoptionDecisions:
    """The section an assistant is meant to raise with a developer before writing any wiring.

    Without it the decisions are still *documented* — but only spread across the Operations link
    lines, which is where they were before, and which is why an assistant reading llms.txt could
    describe every feature accurately and still never mention that ``subject_path`` is mandatory.
    """

    def _text(self) -> str:
        return (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")

    def test_the_section_exists(self):
        assert "## Decisions to settle with the developer" in self._text()

    def test_it_names_the_decisions_that_fail_quietly(self):
        text = self._text()
        section = text.split("## Decisions to settle with the developer", 1)[1].split("\n## ", 1)[0]
        # Each of these is either mandatory (subject_path), or defaults to the permissive answer
        # (write allowlists, masking), or does nothing without infrastructure (retention purge).
        for token in (
            "subject_path",
            "snapadmin.E011",
            "api_write_fields",
            "SNAPADMIN_MASKED_FIELDS",
            "api_field_permissions",
            "tenant_scoped",
            "SNAPADMIN_BACKUP_AGE_RECIPIENTS",
            "CELERY_BEAT_SCHEDULE",
            "SNAPADMIN_PROFILE",
        ):
            assert token in section, f"the decisions section never mentions {token}"

    def test_it_links_the_runnable_form_of_itself(self):
        """The prose list is the conversation; the checklist is how each answer gets proven."""
        section = self._text().split("## Decisions to settle with the developer", 1)[1]
        assert "#integration-checklist" in section.split("\n## ", 1)[0]
