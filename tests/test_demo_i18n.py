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

import os
import pathlib

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
