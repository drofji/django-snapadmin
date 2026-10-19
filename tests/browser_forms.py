"""
tests/browser_forms.py — submit a rendered form the way a browser does (#QA2c).

Django's test client posts whatever a test writes — ``"true"`` for a boolean, the
pk of a choice — so a widget that a real browser cannot drive passes every test.
A checkbox posts ``"on"`` and only when checked; a ``<select>`` posts its selected
option; a field the user never sees still posts its hidden input. This module
reads the HTML a view rendered and builds exactly that payload, so a test can
"submit the page unchanged" and assert that nothing changed.

Standard library only (``html.parser``): the test suite carries no HTML parser.
"""

from __future__ import annotations

from html.parser import HTMLParser


class _FormFields(HTMLParser):
    """Collects the successful controls of one ``<form method="post">`` — the one
    with ``form_id``, or the first (an admin page's first post form is the
    header's logout form, so admin tests name theirs)."""

    def __init__(self, form_id: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.in_form = False
        self.done = False
        self.fields: list[tuple[str, str]] = []
        self._select: str | None = None
        self._select_multiple = False
        self._select_first: str | None = None
        self._select_chosen = False
        self._in_option = False
        self._option_value: str | None = None
        self._option_text: list[str] = []
        self._option_selected = False
        self._textarea: str | None = None
        self._textarea_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value if value is not None else "") for key, value in attrs}
        if tag == "form" and attr.get("method", "").lower() == "post" and not self.done:
            self.in_form = self.form_id is None or attr.get("id") == self.form_id
            return
        if not self.in_form:
            return
        if tag == "input":
            self._input(attr)
        elif tag == "select" and attr.get("name"):
            self._select = attr["name"]
            self._select_multiple = "multiple" in attr
            self._select_first = None
            self._select_chosen = False
        elif tag == "option" and self._select is not None:
            self._in_option = True
            self._option_value = attr.get("value")
            self._option_text = []
            self._option_selected = "selected" in attr
        elif tag == "textarea" and attr.get("name"):
            self._textarea = attr["name"]
            self._textarea_text = []

    def _input(self, attr: dict[str, str]) -> None:
        name = attr.get("name")
        kind = attr.get("type", "text").lower()
        if not name or "disabled" in attr or kind in {"submit", "button", "reset", "image", "file"}:
            return
        if kind in {"checkbox", "radio"}:
            if "checked" in attr:
                self.fields.append((name, attr.get("value", "on") or "on"))
            return
        self.fields.append((name, attr.get("value", "")))

    def handle_data(self, data: str) -> None:
        if self._in_option:
            self._option_text.append(data)
        if self._textarea is not None:
            self._textarea_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_form:
            self.in_form = False
            self.done = True
        elif tag == "option" and self._in_option:
            self._in_option = False
            value = self._option_value if self._option_value is not None else "".join(self._option_text).strip()
            if self._select_first is None:
                self._select_first = value
            if self._option_selected and (self._select_multiple or not self._select_chosen):
                self.fields.append((self._select, value))
                self._select_chosen = True
        elif tag == "select" and self._select is not None:
            # A single select with nothing marked selected submits its first option.
            if not self._select_multiple and not self._select_chosen and self._select_first is not None:
                self.fields.append((self._select, self._select_first))
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            text = "".join(self._textarea_text)
            # The HTML spec drops one leading newline in a <textarea>.
            self.fields.append((self._textarea, text[1:] if text.startswith("\n") else text))
            self._textarea = None


def browser_payload(html: str, form_id: str | None = None) -> dict[str, list[str]]:
    """What a browser would post for a post form in ``html``, untouched."""
    parser = _FormFields(form_id)
    parser.feed(html)
    payload: dict[str, list[str]] = {}
    for name, value in parser.fields:
        payload.setdefault(name, []).append(value)
    return payload


def submit_like_a_browser(
    client, url: str, changes: dict[str, list[str] | None] | None = None, form_id: str | None = None
):
    """GET ``url``, then POST its form back as a browser would.

    ``changes`` edits the payload first: a list replaces a control's values, and
    ``None`` removes the control — an unchecked checkbox, for one, is simply absent.
    """
    page = client.get(url)
    assert page.status_code == 200, (url, page.status_code)
    payload = browser_payload(page.content.decode(), form_id)
    for name, values in (changes or {}).items():
        if values is None:
            payload.pop(name, None)
        else:
            payload[name] = values
    return client.post(url, payload)
