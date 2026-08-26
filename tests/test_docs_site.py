"""
tests/test_docs_site.py

Structural integrity of ``docs/index.html`` itself (#DOC7e), independent of the
``llms.txt`` cross-checks in ``test_ai_entry_points.py``:

* every top-level section gets **exactly one** sidebar link, and every sidebar
  link points at a section that actually exists — the flat 37-link sidebar this
  pins against drifted from that guarantee once before (#DOC7c), which is why
  this test exists;
* every ``<details class="info">`` "?" affordance (#DOC7d) has a non-empty
  ``<summary>``, so a reader never meets a bare "+" with nothing to click for.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_INDEX = REPO_ROOT / "docs" / "index.html"

#: Anchors intentionally not top-level "sections" with their own sidebar entry —
#: UI controls that happen to carry an ``id`` for JS/label wiring, not content.
_NON_SECTION_IDS = {"menuToggle", "themeToggle", "sidebar", "navSearch"}


def _read_docs() -> str:
    return DOCS_INDEX.read_text(encoding="utf-8")


def _body_only(html: str) -> str:
    """Drop everything before ``<body>`` — the ``<style>`` block's own comments

    mention ``<details>`` in prose (documenting the pattern), which a naive
    whole-file regex would misread as a real, malformed tag.
    """
    return html.split("<body>", 1)[1]


def _top_level_section_ids(html: str) -> list[str]:
    """Every ``<h2 id="…">`` plus the hero ``<section id="…">`` — the ids the
    sidebar groups (#DOC7c) were built to cover one-for-one. Deliberately not
    every ``id="…"`` in the file: ``<h3>``/``<h4>`` sub-anchors (``#pii-masking``,
    ``#es-search``, …) are reachable in-page but were never given, and never
    needed, their own top-level sidebar entry — that convention predates this
    round's restructure and this test does not change it.
    """
    h2_ids = re.findall(r'<h2 id="([^"]+)"', html)
    section_ids = re.findall(r'<section id="([^"]+)"', html)
    return h2_ids + section_ids


def _sidebar_hrefs(html: str) -> list[str]:
    nav = re.search(r"<nav>.*?</nav>", html, re.DOTALL)
    assert nav, "docs/index.html must have a <nav> inside the sidebar"
    return [
        href for href in re.findall(r'<a href="#([^"]+)"', nav.group(0))
        if href not in _NON_SECTION_IDS
    ]


class TestSidebarAnchorParity:
    """One sidebar link per section, and vice versa (#DOC7e)."""

    def test_every_section_has_exactly_one_sidebar_link(self):
        html = _read_docs()
        sections = _top_level_section_ids(html)
        hrefs = _sidebar_hrefs(html)
        counts = {section_id: hrefs.count(section_id) for section_id in sections}
        missing = [section_id for section_id, count in counts.items() if count == 0]
        duplicated = [section_id for section_id, count in counts.items() if count > 1]
        assert not missing, f"sections with no sidebar link: {missing}"
        assert not duplicated, f"sections linked more than once: {duplicated}"

    def test_every_sidebar_link_points_at_a_real_section(self):
        html = _read_docs()
        sections = set(_top_level_section_ids(html))
        hrefs = _sidebar_hrefs(html)
        orphaned = [href for href in hrefs if href not in sections]
        assert not orphaned, f"sidebar links with no matching section: {orphaned}"

    def test_no_duplicate_sidebar_hrefs(self):
        hrefs = _sidebar_hrefs(_read_docs())
        seen = set()
        dupes = {href for href in hrefs if href in seen or seen.add(href)}
        assert not dupes, f"the same section is linked twice in the sidebar: {dupes}"


class TestInfoAffordance:
    """Every ``details.info`` "?" block has something to click for (#DOC7d)."""

    def test_every_details_has_a_non_empty_summary(self):
        html = _body_only(_read_docs())
        blocks = re.findall(r"<details\b[^>]*>(.*?)</details>", html, re.DOTALL)
        assert blocks, "expected at least one details.info block after #DOC7d"
        for block in blocks:
            summary = re.search(r"<summary>(.*?)</summary>", block, re.DOTALL)
            assert summary, "a <details> block is missing its <summary>"
            text = re.sub(r"<[^>]+>", "", summary.group(1)).strip()
            assert text, "a <summary> has no visible text"

    def test_details_info_blocks_use_the_shared_pattern(self):
        html = _body_only(_read_docs())
        opens = re.findall(r'<details\b[^>]*>', html)
        assert opens, "expected at least one details.info block after #DOC7d"
        for tag in opens:
            assert 'class="info"' in tag, f"a <details> isn't using the shared info pattern: {tag}"
