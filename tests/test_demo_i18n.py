"""
tests/test_demo_i18n.py — the demo project's own translation catalogs (#I18N3)

``snapadmin/locale/*`` covers the package's strings, but the demo project has its
own: model ``verbose_name``s, the landing page, the admin dashboard panel, the
Unfold navigation titles and the Celery beat descriptions. Those had **no**
catalogs at all, so a Russian visitor saw a page half in Russian (Django and
SnapAdmin strings) and half in English (everything the demo itself declares) —
"Categories" next to "Журналы аудита".

These tests pin the fix and guard against the catalogs going stale again.
"""

import ast
import os
import pathlib
import re

import pytest
from django.utils import translation

DEMO_LOCALES = ["en", "ru", "de", "de_CH", "fr", "fr_CH", "es", "it", "pl", "nl"]
LOCALE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "demo" / "locale"


def _parse_po(path):
    """(msgid, msgstr) pairs, concatenating continuation lines."""
    lines = pathlib.Path(path).read_text(encoding="utf-8").split("\n")
    entries, i = [], 0
    while i < len(lines):
        if lines[i].startswith("msgid "):
            mid = eval(lines[i][6:].strip())
            i += 1
            while i < len(lines) and lines[i].startswith('"'):
                mid += eval(lines[i].strip())
                i += 1
            mstr = ""
            if i < len(lines) and lines[i].startswith("msgstr "):
                mstr = eval(lines[i][7:].strip())
                i += 1
                while i < len(lines) and lines[i].startswith('"'):
                    mstr += eval(lines[i].strip())
                    i += 1
            entries.append((mid, mstr))
        else:
            i += 1
    return entries


# ── catalogs exist and are complete ──────────────────────────────────────────

class TestDemoCatalogs:
    @pytest.mark.parametrize("locale", DEMO_LOCALES)
    def test_compiled_catalog_exists(self, locale):
        assert (LOCALE_ROOT / locale / "LC_MESSAGES" / "django.mo").exists()

    @pytest.mark.parametrize("locale", [loc for loc in DEMO_LOCALES if loc != "en"])
    def test_no_empty_msgstr(self, locale):
        entries = _parse_po(LOCALE_ROOT / locale / "LC_MESSAGES" / "django.po")
        missing = [mid for mid, mstr in entries if mid and not mstr]
        assert not missing, f"{locale} has untranslated demo strings: {missing}"

    def test_english_stays_header_only(self):
        entries = _parse_po(LOCALE_ROOT / "en" / "LC_MESSAGES" / "django.po")
        assert [mid for mid, _ in entries if mid] == []

    def test_locales_cover_the_same_msgids(self):
        ref = {mid for mid, _ in _parse_po(LOCALE_ROOT / "ru" / "LC_MESSAGES" / "django.po") if mid}
        assert ref, "the ru demo catalog is empty"
        for loc in DEMO_LOCALES:
            if loc in ("en", "ru"):
                continue
            ids = {mid for mid, _ in _parse_po(LOCALE_ROOT / loc / "LC_MESSAGES" / "django.po") if mid}
            assert ids == ref, f"{loc} demo msgid set diverges from ru"

    def test_swiss_german_drops_eszett(self):
        po = (LOCALE_ROOT / "de_CH" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")
        assert "ß" not in po

    def test_placeholders_survive_translation(self):
        """A dropped %(...)s placeholder raises at render time, not at compile time."""
        for locale in DEMO_LOCALES:
            if locale == "en":
                continue
            for mid, mstr in _parse_po(LOCALE_ROOT / locale / "LC_MESSAGES" / "django.po"):
                for token in ("%(pk)s", "%(name)s", "%(customer)s",
                              "%(product_available)s", "%(product_total)s"):
                    if token in mid:
                        assert token in mstr, f"{locale}: {mid!r} lost {token}"


# ── the demo actually renders in one language ────────────────────────────────

@pytest.mark.django_db
class TestDemoPagesRenderFullyLocalised:
    def test_landing_page_is_all_russian(self, client, django_user_model):
        # A non-staff visitor: since #UX2 merged the dashboard into "/" for staff,
        # this is the case that still exercises LandingView's own tabbed template.
        user = django_user_model.objects.create_user(username="ru_plain", password="pw12345")
        client.force_login(user)
        html = client.get("/", HTTP_ACCEPT_LANGUAGE="ru").content.decode()
        for needle in ["Товары", "Клиенты", "Заказы", "Выйти", "Обзор", "Статистика"]:
            assert needle in html, needle

    def test_root_dashboard_is_all_russian_for_staff(self, client, admin_user):
        # #UX2: staff at "/" get the merged dashboard — its session bar and the
        # folded-in service checklist must be translated too, not just the
        # package dashboard content already covered below.
        client.force_login(admin_user)
        html = client.get("/", HTTP_ACCEPT_LANGUAGE="ru").content.decode()
        assert "Вы вошли как" in html
        assert "Выйти" in html
        assert "Необязательные возможности" in html

    def test_dashboard_model_cards_and_cron_are_russian(self, admin_client):
        html = admin_client.get("/dashboard/", HTTP_ACCEPT_LANGUAGE="ru").content.decode()
        assert "Панель администратора" in html      # a package quick link (#I18N2)
        assert "Товары" in html                     # a demo model verbose_name_plural
        assert "Журналы аудита" in html             # capfirst, not .title()
        assert "Синхронизировать" in html           # a beat-schedule description

    def test_admin_dashboard_panel_is_russian(self, admin_client):
        html = admin_client.get("/admin/", HTTP_ACCEPT_LANGUAGE="ru").content.decode()
        for needle in ["Всего заказов", "Активные клиенты", "Выручка", "Использование диска"]:
            assert needle in html, needle

    def test_model_names_are_not_title_cased(self, client, admin_user):
        """.title() upper-cased every word: "журналы аудита" → "Журналы Аудита"."""
        client.force_login(admin_user)
        html = client.get("/", HTTP_ACCEPT_LANGUAGE="ru").content.decode()
        assert "Журналы Аудита" not in html

    def test_beat_descriptions_are_translatable(self):
        from django.conf import settings

        with translation.override("de"):
            desc = str(settings.CELERY_BEAT_SCHEDULE["reindex-products-to-es"]["description"])
        assert desc.startswith("Alle Product-Datensätze")

    def test_service_labels_translated(self):
        from demo.apps.shop.views import LandingView

        with translation.override("ru"):
            labels = {f["key"]: str(f["label"]) for f in LandingView._service_flags()}
        assert labels["audit"] == "Журнал аудита"
        assert labels["user_api"] == "API пользователей"
        assert labels["rest"] == "REST API"          # product name, untranslated


# ── every translatable string the demo declares reaches the catalogs ──────────

DEMO_ROOT = LOCALE_ROOT.parent

#: The callables Django's ``makemessages`` treats as translation markers.
_GETTEXT_CALLABLES = frozenset(
    {"gettext", "gettext_lazy", "ugettext", "ugettext_lazy", "_", "pgettext", "ngettext"}
)

_TEMPLATE_TRANS_TAG = re.compile(r"{%\s*(?:trans|translate)\s+([\"'])(.+?)\1", re.S)


def _python_translatable_literals() -> set[str]:
    """Every literal passed to a gettext callable in the demo's Python source."""
    literals: set[str] = set()
    for source_file in DEMO_ROOT.rglob("*.py"):
        if "locale" in source_file.parts or "migrations" in source_file.parts:
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            first = node.args[0]
            if (
                called in _GETTEXT_CALLABLES
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                literals.add(first.value)
    return literals


def _template_translatable_literals() -> set[str]:
    literals: set[str] = set()
    for template in DEMO_ROOT.rglob("*.html"):
        for _quote, text in _TEMPLATE_TRANS_TAG.findall(
            template.read_text(encoding="utf-8")
        ):
            literals.add(text)
    return literals


class TestEveryDemoStringHasAMsgid:
    """A ``_()`` the catalogs never heard of ships untranslated in all ten locales.

    The rest of this file compares the catalogs to each other — empty msgstrs, a
    locale whose msgid set has drifted from ``ru``, a dropped placeholder — so it
    can only see a string the catalogs already know about. It could not see a
    string that was never extracted at all, and that is the failure that
    actually happened: #FIX1g added seven translatable strings and the whole
    suite stayed green with them missing from all ten catalogs (caught by hand).
    This compares the **source** to the catalog instead. (#FIX2 section C,
    closed in #QA1b.)

    ``ru`` is the reference catalog for the same reason the rest of the file uses
    it: it is the one locale with a reviewer.
    """

    def _russian_msgids(self) -> set[str]:
        return {
            msgid
            for msgid, _msgstr in _parse_po(LOCALE_ROOT / "ru" / "LC_MESSAGES" / "django.po")
            if msgid
        }

    def test_every_python_literal_was_extracted(self):
        missing = sorted(_python_translatable_literals() - self._russian_msgids())
        assert not missing, (
            "translatable string(s) in demo/*.py with no msgid in the ru catalog: "
            f"{missing}. Regenerate per locale — `makemessages -a` is a no-op in "
            "this repo — then fill the new msgids in."
        )

    def test_every_template_tag_was_extracted(self):
        missing = sorted(_template_translatable_literals() - self._russian_msgids())
        assert not missing, (
            "{% trans %} string(s) in demo templates with no msgid in the ru "
            f"catalog: {missing}."
        )

    def test_the_sweep_actually_finds_the_demo_s_strings(self):
        """A guard on the guard: a broken extractor would pass silently."""
        python_literals = _python_translatable_literals()
        assert len(python_literals) > 100, len(python_literals)
        assert len(_template_translatable_literals()) > 20
