"""
tests/test_translation_lint.py

A mechanical lint over every shipped translation catalog (#RM1m).

Whether a German sentence reads naturally is a question for a German speaker;
the docs say which locales still wait for one (``docs/index.html#i18n-review``).
What a machine *can* decide, it decides here — the class of defects
``msgfmt --check`` reports, each of which reaches a user as a crash, a wrong
string or an English one:

* **Format placeholders.** A translation must carry exactly the placeholders of
  its source, with the same conversion: ``%(name)s`` misspelt is a ``KeyError``
  when the string renders, ``%(count)d`` for ``%(count)s`` or one ``%s`` too
  many a ``TypeError``, and one dropped silently loses the value. Checked where
  gettext marks the entry ``python-format`` / ``python-brace-format``, or where
  the source carries a named placeholder — never on prose such as "100% done".
* **Leading and trailing newlines**, which templates and log lines rely on.
* **HTML tags.** A tag lost or added in translation breaks the markup the
  string is rendered into.
* **The header.** ``Language`` names the catalog's own locale, the charset is
  UTF-8, and ``Plural-Forms`` declares ``nplurals`` — which every plural entry
  must then fill completely.
* **No empty translation** in the demo's catalogs either. The package's, and
  fuzzy entries in both, are checked in ``tests/test_i18n.py``, whose catalog
  sweep this module shares.
* **The compiled ``.mo`` is exactly the ``.po`` it sits next to** — every entry,
  the ones cleared or removed since included. Django reads only the ``.mo``; one
  compiled before the last edit ships the old text.

English is the source language and its catalogs are header-only; the checks
that need a translation skip it.
"""

from __future__ import annotations

import ast
import gettext
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest

from tests.test_i18n import _catalogs

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_LOCALE = "en"

#: ``%(name)s`` → ("name", "s"); ``%5.2f`` → ("", "f"); ``%%`` is not matched.
_PERCENT = re.compile(r"%(?:\((\w+)\))?[-#0 +]*(?:\d+|\*)?(?:\.\d+)?([diouxXeEfFgGcrsa%])")
_BRACE = re.compile(r"(?<!\{)\{(\w*)(?:[:!][^{}]*)?\}(?!\})")
_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")


@dataclass
class Entry:
    msgid: str
    msgstr: str = ""
    msgctxt: str | None = None
    msgid_plural: str | None = None
    plural_forms: dict[int, str] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    line: int = 0


def _string(literal: str) -> str:
    return ast.literal_eval(literal)


def parse_po(path: Path) -> list[Entry]:
    """Every active entry of a ``.po`` file, the header (``msgid ""``) first.

    Obsolete entries (``#~``) are commented out and compile to nothing, so they
    are not returned. Multi-line strings are joined.
    """
    entries: list[Entry] = []
    current = Entry(msgid="")
    flags: set[str] = set()
    last_keyword = ""
    plural_index: int | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#~"):
            continue
        if line.startswith("#,"):
            flags |= {flag.strip() for flag in line[2:].split(",")}
            continue
        if line.startswith("#"):
            continue
        if line.startswith('"'):  # a continuation of the last keyword's string
            text = _string(line)
            if plural_index is not None:
                current.plural_forms[plural_index] += text
            else:
                attribute = {"msgctxt": "msgctxt", "msgid": "msgid",
                             "msgid_plural": "msgid_plural", "msgstr": "msgstr"}[last_keyword]
                setattr(current, attribute, getattr(current, attribute) + text)
            continue
        keyword, _, value = line.partition(" ")
        # An entry starts at its msgctxt, or at its msgid when it has none.
        if keyword == "msgctxt" or (keyword == "msgid" and last_keyword != "msgctxt"):
            current = Entry(msgid="", flags=flags, line=number)
            entries.append(current)
            flags = set()
        plural_index = None
        if keyword.startswith("msgstr["):
            plural_index = int(keyword[7:-1])
            current.plural_forms[plural_index] = _string(value)
        else:
            setattr(current, keyword, _string(value))
        last_keyword = keyword
    return entries


def _locale(path: Path) -> str:
    return path.parts[-3]


def _catalog_id(path: Path) -> str:
    return f"{path.parts[-5]}-{_locale(path)}"


def _translated(path: Path) -> list[Entry]:
    """The entries that carry a translation — the header excluded."""
    return [entry for entry in parse_po(path) if entry.msgid and "fuzzy" not in entry.flags]


def _translations(entry: Entry) -> list[tuple[str, str]]:
    """``(source, translation)`` pairs to compare: each plural form against the
    plural source, except form 0, which is the singular."""
    if entry.msgid_plural is None:
        return [(entry.msgid, entry.msgstr)]
    return [
        (entry.msgid if index == 0 else entry.msgid_plural, text)
        for index, text in sorted(entry.plural_forms.items())
    ]


TRANSLATED_CATALOGS = [path for path in _catalogs() if _locale(path) != SOURCE_LOCALE]


@cache
def _header(path: Path) -> dict[str, str]:
    header = parse_po(path)[0]
    assert header.msgid == "", f"{path}: the first entry is not the header"
    fields = {}
    for line in header.msgstr.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


# ─────────────────────────────────────────────────────────────────────────────
# The parser itself
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_PO = r'''
msgid ""
msgstr ""
"Language: ru\n"
"Plural-Forms: nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : 1);\n"

#: a.py:1
#, python-format
msgid "Hello %(name)s"
msgstr "Привет, "
"%(name)s"

msgctxt "button"
msgid "Save"
msgstr "Сохранить"

msgid "%(count)d row"
msgid_plural "%(count)d rows"
msgstr[0] "%(count)d строка"
msgstr[1] "%(count)d строки"
msgstr[2] "%(count)d строк"

#, fuzzy
msgid "Guessed"
msgstr "Угадано"

#~ msgid "Gone"
#~ msgstr "Ушло"
'''


def test_the_parser_reads_contexts_plurals_continuations_and_flags(tmp_path):
    sample = tmp_path / "django.po"
    sample.write_text(SAMPLE_PO, encoding="utf-8")

    header, hello, save, rows, guessed = parse_po(sample)

    assert header.msgid == "" and "nplurals=3" in header.msgstr
    assert (hello.msgid, hello.msgstr, hello.flags) == (
        "Hello %(name)s", "Привет, %(name)s", {"python-format"}
    )
    assert (save.msgctxt, save.msgid, save.msgstr) == ("button", "Save", "Сохранить")
    assert rows.msgid_plural == "%(count)d rows"
    assert rows.plural_forms == {0: "%(count)d строка", 1: "%(count)d строки", 2: "%(count)d строк"}
    assert guessed.flags == {"fuzzy"}


def test_the_sweep_sees_every_catalog():
    """A wrong glob would make every check below pass on nothing."""
    catalogs = _catalogs()

    assert len(catalogs) == 20
    assert {_locale(path) for path in catalogs} == {
        "en", "ru", "de", "de_CH", "fr", "fr_CH", "es", "it", "pl", "nl"
    }
    assert all(len(_translated(path)) > 10 for path in TRANSLATED_CATALOGS)


# ─────────────────────────────────────────────────────────────────────────────
# Each translation against its source
# ─────────────────────────────────────────────────────────────────────────────

def _percent_placeholders(text: str) -> tuple[Counter, list[str]]:
    """Named placeholders with their conversion, and the positional conversions in order."""
    found = [(name, kind) for name, kind in _PERCENT.findall(text) if kind != "%"]
    return Counter(item for item in found if item[0]), [kind for name, kind in found if not name]


def _placeholder_problem(entry: Entry, source: str, translation: str) -> bool:
    percent = "python-format" in entry.flags or "%(" in entry.msgid
    brace = "python-brace-format" in entry.flags
    if percent and _percent_placeholders(source) != _percent_placeholders(translation):
        return True
    return brace and Counter(_BRACE.findall(source)) != Counter(_BRACE.findall(translation))


@pytest.mark.parametrize(
    "source, translation, flags, broken",
    [
        ("%(name)s saved", "%(name)s gespeichert", {"python-format"}, False),
        ("%(name)s saved", "%(nom)s gespeichert", {"python-format"}, True),
        ("%(name)s saved", "%(name)d gespeichert", {"python-format"}, True),
        ("%s of %d", "%s von %d", {"python-format"}, False),
        ("%s of %d", "%d von %s", {"python-format"}, True),
        ("100%% done", "100 %% fertig", {"python-format"}, False),
        ("Progress: 100% done", "Progression : 100 % terminé", set(), False),
        ("{count} rows", "{count} Zeilen", {"python-brace-format"}, False),
        ("{count} rows", "{anzahl} Zeilen", {"python-brace-format"}, True),
    ],
)
def test_the_placeholder_check_itself(source, translation, flags, broken):
    entry = Entry(msgid=source, msgstr=translation, flags=flags)

    assert _placeholder_problem(entry, source, translation) is broken


@pytest.mark.parametrize("catalog", TRANSLATED_CATALOGS, ids=_catalog_id)
def test_every_translation_keeps_its_placeholders(catalog):
    problems = []
    for entry in _translated(catalog):
        for source, translation in _translations(entry):
            if translation and _placeholder_problem(entry, source, translation):
                problems.append(f"line {entry.line}: {source!r} -> {translation!r}")

    assert problems == [], (
        f"{_catalog_id(catalog)}: a translation whose placeholders differ from its source "
        f"raises or loses a value when it renders: {problems}"
    )


@pytest.mark.parametrize("catalog", TRANSLATED_CATALOGS, ids=_catalog_id)
def test_every_translation_keeps_its_leading_and_trailing_newlines(catalog):
    problems = [
        f"line {entry.line}: {source!r}"
        for entry in _translated(catalog)
        for source, translation in _translations(entry)
        if translation
        and (source.startswith("\n"), source.endswith("\n"))
        != (translation.startswith("\n"), translation.endswith("\n"))
    ]

    assert problems == [], f"{_catalog_id(catalog)}: newline mismatch in {problems}"


@pytest.mark.parametrize("catalog", TRANSLATED_CATALOGS, ids=_catalog_id)
def test_every_translation_keeps_its_markup(catalog):
    problems = [
        f"line {entry.line}: {source!r} -> {translation!r}"
        for entry in _translated(catalog)
        for source, translation in _translations(entry)
        if translation and Counter(_TAG.findall(source)) != Counter(_TAG.findall(translation))
    ]

    assert problems == [], f"{_catalog_id(catalog)}: HTML tags lost or added in {problems}"


@pytest.mark.parametrize("catalog", [p for p in TRANSLATED_CATALOGS if p.parts[-5] == "demo"], ids=_catalog_id)
def test_no_demo_string_is_left_untranslated(catalog):
    empty = [
        entry.msgid
        for entry in _translated(catalog)
        if not entry.msgstr and not any(entry.plural_forms.values())
    ]

    assert empty == [], f"{_catalog_id(catalog)} ships these strings in English: {empty}"


# ─────────────────────────────────────────────────────────────────────────────
# The header, and plural entries against it
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("catalog", _catalogs(), ids=_catalog_id)
def test_the_header_names_its_own_locale_in_utf_8(catalog):
    header = _header(catalog)

    assert header.get("Language") == _locale(catalog)
    assert header.get("Content-Type") == "text/plain; charset=UTF-8"


@pytest.mark.parametrize("catalog", TRANSLATED_CATALOGS, ids=_catalog_id)
def test_every_plural_entry_fills_every_form_the_header_declares(catalog):
    plural_forms = _header(catalog).get("Plural-Forms", "")
    declared = re.search(r"nplurals\s*=\s*(\d+)", plural_forms)
    plural_entries = [entry for entry in _translated(catalog) if entry.msgid_plural is not None]
    # Declared even with no plural entry yet: without it gettext falls back to
    # English plural rules, and the first plural string would silently use them.
    assert declared, f"{_catalog_id(catalog)} declares no Plural-Forms"

    problems = [
        f"line {entry.line}: {entry.msgid!r} has forms {sorted(entry.plural_forms)}"
        for entry in plural_entries
        if sorted(entry.plural_forms) != list(range(int(declared[1])))
        or not all(entry.plural_forms.values())
    ]

    assert problems == [], f"{_catalog_id(catalog)}: incomplete plural entries {problems}"


# ─────────────────────────────────────────────────────────────────────────────
# The compiled catalog is the source catalog
# ─────────────────────────────────────────────────────────────────────────────

def _expected_catalog(path: Path) -> dict[object, str]:
    """What a correct ``django.mo`` holds, in ``GNUTranslations``' own keys:
    ``msgid`` (``"ctx\\x04msgid"`` with a context), ``(msgid, n)`` per plural form.
    An empty or fuzzy entry compiles to nothing, so it is absent here too."""
    expected: dict[object, str] = {}
    for entry in _translated(path):
        key = f"{entry.msgctxt}\x04{entry.msgid}" if entry.msgctxt is not None else entry.msgid
        if entry.msgid_plural is not None:
            if all(entry.plural_forms.values()):
                expected.update({(key, index): text for index, text in entry.plural_forms.items()})
        elif entry.msgstr:
            expected[key] = entry.msgstr
    return expected


@pytest.mark.parametrize("catalog", TRANSLATED_CATALOGS, ids=_catalog_id)
def test_the_compiled_catalog_is_exactly_the_source_catalog(catalog):
    with catalog.with_suffix(".mo").open("rb") as compiled_file:
        compiled = gettext.GNUTranslations(compiled_file)
    shipped = {key: text for key, text in compiled._catalog.items() if key != ""}
    expected = _expected_catalog(catalog)

    stale = sorted(str(key) for key in shipped.keys() | expected.keys()
                   if shipped.get(key) != expected.get(key))
    assert stale == [], (
        f"{_catalog_id(catalog)}: django.mo differs from django.po for {stale[:10]} — "
        f"run compilemessages"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The review status a person needs is where they will read it
# ─────────────────────────────────────────────────────────────────────────────

#: Locales a native speaker has reviewed. The lint above proves the catalogs are
#: well-formed; it cannot prove a sentence reads naturally, and the docs must not
#: let anyone believe otherwise.
NATIVE_REVIEWED = {"en", "ru"}


def test_the_docs_name_exactly_the_locales_awaiting_native_review():
    html = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    callout = html.split('id="i18n-review"', 1)[1].split("</div>", 1)[0]
    reviewed, awaiting = callout.split("awaiting a native speaker's review:", 1)

    shipped = {_locale(path) for path in _catalogs()}
    assert set(re.findall(r"<code>(\w+)</code>", reviewed)) == NATIVE_REVIEWED
    assert set(re.findall(r"<code>(\w+)</code>", awaiting.split("</code>.", 1)[0] + "</code>")) == (
        shipped - NATIVE_REVIEWED
    )


@pytest.mark.parametrize("document", ["README.md", "llms.txt", "docs/llms.txt", "CONTRIBUTING.md"])
def test_every_entry_point_says_which_locales_are_native_reviewed(document):
    text = (REPO_ROOT / document).read_text(encoding="utf-8")

    assert "native" in text.lower(), document
    assert "`en`" in text and "`ru`" in text, f"{document} does not name the reviewed locales"
    assert "test_translation_lint.py" in text or "linted mechanically" in text, document
