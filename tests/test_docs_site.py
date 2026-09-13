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


# ---------------------------------------------------------------------------
# Liquid escaping in the Markdown docs (#DOCS1b)
# ---------------------------------------------------------------------------

#: Markdown under ``docs/`` is what GitHub Pages renders through Jekyll's Liquid
#: engine. ``docs/index.html`` and ``docs/llms.txt`` are deliberately out of
#: scope: neither carries YAML front matter, so Jekyll copies them verbatim as
#: static files rather than parsing them — which is why the three ``include`` /
#: ``url`` examples living in ``index.html`` have never broken a build. Markdown
#: is different: GitHub Pages runs ``jekyll-optional-front-matter``, so every
#: ``.md`` file becomes a page and is parsed whether it has front matter or not.
_DOCS_MARKDOWN_GLOB = "**/*.md"

#: Jekyll/Liquid tags this project deliberately writes **unescaped**, i.e. meant
#: for Jekyll to execute. Empty on purpose: the docs are plain Markdown with no
#: Jekyll templating of their own, so every Liquid tag that survives to the site
#: is an example of *Django* template syntax and belongs inside a raw block.
#: Add a name here only when a real Jekyll tag is introduced deliberately.
_JEKYLL_TAGS_ALLOWED_UNESCAPED: frozenset[str] = frozenset()

#: Matches the opening of any Liquid tag, capturing its name when it has one.
_LIQUID_TAG = re.compile(r"\{%-?\s*(\w+)?")

DOCS_DIR = REPO_ROOT / "docs"


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _markdown_docs() -> list[Path]:
    return sorted(DOCS_DIR.glob(_DOCS_MARKDOWN_GLOB))


def _unescaped_liquid_tags(text: str) -> list[str]:
    """Every Liquid tag in ``text`` that Jekyll would try to execute.

    Returns human-readable ``line N: …`` strings. Code fences are **not** a
    shelter: Liquid runs before the Markdown converter, so a Django tag inside a
    fenced block breaks the build exactly like one in prose. Structural problems
    with the raw blocks themselves (a nested opener, a stray closer, an opener
    that is never closed) are reported here too — each one silently changes
    which of the surrounding tags are escaped.
    """
    problems: list[str] = []
    in_raw = False
    raw_opened_at = 0
    for match in _LIQUID_TAG.finditer(text):
        tag = match.group(1)
        line = _line_of(text, match.start())
        if tag is None:
            snippet = text[match.start():match.start() + 20]
            problems.append(f"line {line}: malformed Liquid tag {snippet!r}")
            continue
        if tag == "raw":
            if in_raw:
                problems.append(f"line {line}: nested raw block (one is already open at line {raw_opened_at})")
            in_raw, raw_opened_at = True, line
        elif tag == "endraw":
            if not in_raw:
                problems.append(f"line {line}: endraw with no matching raw")
            in_raw = False
        elif not in_raw and tag not in _JEKYLL_TAGS_ALLOWED_UNESCAPED:
            problems.append(f"line {line}: unescaped Liquid tag {tag!r}")
    if in_raw:
        problems.append(f"line {raw_opened_at}: raw block is never closed")
    return problems


class TestMarkdownLiquidEscaping:
    """No Markdown doc hands Jekyll a template tag it cannot parse (#DOCS1b).

    GitHub Pages builds the docs with Jekyll, and an unknown Liquid tag is a hard
    build failure, not a rendering glitch: ``Liquid syntax error: Unknown tag
    'static'`` took the whole site down twice, each time discovered only after
    the push. Django's template syntax is spelled exactly like Liquid's, so
    writing a ``static``/``url`` tag in an example is a normal thing to want and
    a build break every time. Wrapping it in a raw block is the fix; this test is
    what makes forgetting it a local failure instead of a site outage.
    """

    def test_docs_markdown_is_discovered(self):
        assert _markdown_docs(), "expected Markdown files under docs/ to check"

    def test_every_liquid_tag_is_inside_a_raw_block(self):
        offenders = {
            path.relative_to(REPO_ROOT).as_posix(): problems
            for path in _markdown_docs()
            if (problems := _unescaped_liquid_tags(path.read_text(encoding="utf-8")))
        }
        assert not offenders, (
            "Jekyll will fail the GitHub Pages build on these Liquid tags — wrap "
            f"each one in a raw/endraw block: {offenders}"
        )

    def test_the_checker_catches_an_unescaped_tag(self):
        unescaped = "Use `" + "{% static \"x.css\" %}" + "` here."
        assert _unescaped_liquid_tags(unescaped) == ["line 1: unescaped Liquid tag 'static'"]
        escaped = "`" + "{% raw %}" + "{% static \"x.css\" %}" + "{% endraw %}" + "`"
        assert _unescaped_liquid_tags(escaped) == []

    def test_the_checker_catches_broken_raw_blocks(self):
        assert _unescaped_liquid_tags("{% raw %}\n{% url %}") == [
            "line 1: raw block is never closed"
        ]
        assert _unescaped_liquid_tags("{% endraw %}") == [
            "line 1: endraw with no matching raw"
        ]
        assert _unescaped_liquid_tags("{% raw %}{% raw %}{% endraw %}") == [
            "line 1: nested raw block (one is already open at line 1)"
        ]
