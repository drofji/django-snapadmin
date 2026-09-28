"""
tests/test_template_comments.py

Every ``{# … #}`` comment in a shipped template closes on the line it opens.

Django only recognises that comment form on a single line. Spread over two, it
is not a comment at all: its text is rendered into the page. From 0.1.0b6 the
dashboard carried one such "comment" above the chart data, and its words
included a literal ``<script>`` — so the browser opened a script element there,
swallowed the chart's JSON into it as code, threw, and never drew the chart
(found by the browser suite, #QA1e). A multi-line comment belongs in
``{% comment %}…{% endcomment %}``.

The scan covers the package's templates and the demo's, since both reach a
browser; the second test proves the scan can actually see the shape it hunts.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOTS = [
    REPO_ROOT / "snapadmin" / "templates",
    REPO_ROOT / "demo" / "templates",
    REPO_ROOT / "demo" / "apps",
]


def _unclosed_comment_lines(text: str) -> list[int]:
    """Line numbers where a ``{#`` opens and no ``#}`` closes it on that line."""
    offenders = []
    for number, line in enumerate(text.splitlines(), start=1):
        position = line.find("{#")
        while position != -1:
            end = line.find("#}", position + 2)
            if end == -1:
                offenders.append(number)
                break
            position = line.find("{#", end + 2)
    return offenders


def _templates() -> list[Path]:
    return sorted(path for root in TEMPLATE_ROOTS for path in root.rglob("*.html"))


def test_every_shipped_template_comment_closes_on_its_own_line():
    templates = _templates()
    assert len(templates) > 10, "the scan found almost no templates — did a directory move?"

    offenders = {
        str(path.relative_to(REPO_ROOT)): lines
        for path in templates
        if (lines := _unclosed_comment_lines(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}, (
        f"multi-line {{# #}} comment(s), rendered as page text: {offenders}. "
        "Use {% comment %}…{% endcomment %} for anything longer than one line."
    )


def test_the_scan_sees_a_multi_line_comment():
    template = (
        "<p>{# one line is fine #}</p>\n"
        "{# a second comment #}{# and a third, on the same line #}\n"
        "{# this one runs on\n"
        "   to the next line #}\n"
    )

    assert _unclosed_comment_lines(template) == [3]
