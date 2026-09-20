"""
tests/test_fuzz_sanitize.py

Adversarial HTML against the wysiwyg sanitizer (#QA1d, part 4).

Rich-text values are written by whoever can write the field — an API token, a
low-privileged staff account, an import — and rendered in an administrator's
changelist. ``hypothesis`` assembles hostile documents out of the classic XSS
building blocks (script-bearing elements, event-handler attributes, script URL
schemes) and the obfuscations that get past naive filters (case games,
whitespace and control characters inside the scheme, HTML entities, nesting,
unclosed tags, comments). The invariants, checked by parsing the output rather
than by searching its text:

* **nothing executable survives** — no script-capable element, no ``on*``
  attribute, no URL attribute whose scheme (after entity decoding and the
  whitespace/control stripping browsers do) is ``javascript:``/``vbscript:``/
  ``data:``;
* **sanitizing is idempotent** — the value is cleaned on write *and* on render,
  so a second pass must not change a first pass's output;
* **empty stays empty**.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from hypothesis import given
from hypothesis import strategies as st

from snapadmin.sanitize import sanitize_html

EXECUTABLE_ELEMENTS = {"script", "iframe", "object", "embed", "frame", "frameset", "applet",
                       "base", "meta", "link", "style", "form", "svg", "math", "template"}
URL_ATTRIBUTES = {"href", "src", "action", "formaction", "xlink:href", "data", "poster", "background"}
UNSAFE_SCHEMES = ("javascript:", "vbscript:", "data:")


class _Inspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in EXECUTABLE_ELEMENTS:
            self.violations.append(f"<{tag}>")
        for name, value in attrs:
            if name.startswith("on"):
                self.violations.append(f"{tag}[{name}]")
            if name in URL_ATTRIBUTES and value is not None:
                # Browsers drop ASCII whitespace and control characters inside
                # a scheme before resolving it: "java\tscript:" is javascript:.
                scheme = re.sub(r"[\x00-\x20]", "", html.unescape(value)).lower()
                if scheme.startswith(UNSAFE_SCHEMES):
                    self.violations.append(f"{tag}[{name}={value!r}]")

    handle_startendtag = handle_starttag


def _violations(markup: str) -> list[str]:
    inspector = _Inspector()
    inspector.feed(markup)
    inspector.close()
    return inspector.violations


SCHEME_OBFUSCATIONS = st.sampled_from([
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "java\nscript:alert(1)",
    " javascript:alert(1)", "\x01javascript:alert(1)", "&#106;avascript:alert(1)",
    "&#x6A;avascript:alert(1)", "javascript&colon;alert(1)", "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>", "jav&#x09;ascript:alert(1)",
])
EVENT_ATTRIBUTES = st.sampled_from(["onerror", "onload", "onclick", "ONMOUSEOVER", "onfocus", "onbegin"])
TAGS = st.sampled_from(["p", "b", "a", "img", "div", "span", "svg", "iframe", "script", "object",
                        "embed", "math", "style", "form", "details", "video", "SCRIPT", "ScRiPt"])


@st.composite
def hostile_fragment(draw) -> str:
    tag = draw(TAGS)
    kind = draw(st.sampled_from(["event", "url", "body", "comment", "unclosed", "nested"]))
    if kind == "event":
        return f'<{tag} {draw(EVENT_ATTRIBUTES)}="alert(1)">x</{tag}>'
    if kind == "url":
        attribute = draw(st.sampled_from(["href", "src", "action", "formaction", "xlink:href"]))
        return f'<{tag} {attribute}="{draw(SCHEME_OBFUSCATIONS)}">x</{tag}>'
    if kind == "body":
        return f"<{tag}>alert(1)</{tag}>"
    if kind == "comment":
        return f"<!--<{tag}>--><{tag}>alert(1)</{tag}>"
    if kind == "unclosed":
        return f"<{tag} {draw(EVENT_ATTRIBUTES)}=alert(1) "
    return f"<{tag}><{draw(TAGS)} {draw(EVENT_ATTRIBUTES)}=alert(1)>" + draw(st.text(max_size=10))


HOSTILE_HTML = st.lists(st.one_of(hostile_fragment(), st.text(max_size=20)), min_size=1, max_size=6).map("".join)


class TestSanitizerInvariants:
    @given(markup=HOSTILE_HTML)
    def test_nothing_executable_survives(self, markup):
        cleaned = sanitize_html(markup)

        assert _violations(cleaned) == [], (markup, cleaned)

    @given(markup=st.one_of(HOSTILE_HTML, st.text()))
    def test_a_second_pass_changes_nothing(self, markup):
        once = sanitize_html(markup)

        assert sanitize_html(once) == once

    def test_empty_stays_empty(self):
        assert sanitize_html("") == ""

    def test_the_inspector_would_catch_each_shape_it_looks_for(self):
        """The oracle itself must have teeth, or every law above is vacuous."""
        assert _violations('<a href="java\tscript:x">') == ["a[href='java\\tscript:x']"]
        assert _violations('<img src="&#106;avascript:x">') == ["img[src='javascript:x']"]
        assert _violations("<p onclick=x>") == ["p[onclick]"]
        assert _violations("<svg><script>") == ["<svg>", "<script>"]
