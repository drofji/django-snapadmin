"""
tests/test_i18n.py — internationalisation (issue #9)

SnapAdmin ships compiled catalogs for 10 locales, wraps its UI strings in
gettext, renders a language switcher, and falls back to English for anything
untranslated.
"""

import pytest
from django.test import Client
from django.utils import translation

TARGET_LOCALES = ["en", "ru", "de", "de_CH", "fr", "fr_CH", "es", "it", "pl", "nl"]


# ── catalogs resolve ─────────────────────────────────────────────────────────

class TestCatalogs:
    @pytest.mark.parametrize("locale,expected", [
        ("ru", "Состояние системы"),
        ("de", "Systemzustand"),
        ("fr", "État du système"),
        ("es", "Estado del sistema"),
        ("it", "Stato del sistema"),
        ("pl", "Stan systemu"),
        ("nl", "Systeemstatus"),
    ])
    def test_translated(self, locale, expected):
        with translation.override(locale):
            assert translation.gettext("System Health") == expected

    def test_swiss_german_drops_eszett(self):
        # de_CH must never contain "ß" — Swiss orthography uses "ss".
        with translation.override("de"):
            de = translation.gettext("Language")
        with translation.override("de_CH"):
            de_ch = translation.gettext("System Health")
        assert "ß" not in de_ch
        # de_CH inherits the German wording (with ß→ss applied).
        with translation.override("de_CH"):
            assert translation.gettext("Managed Models") == "Verwaltete Modelle"

    def test_english_is_source(self):
        with translation.override("en"):
            assert translation.gettext("System Health") == "System Health"

    def test_missing_string_falls_back_to_english(self):
        # A string with no catalog entry returns the English source, not blank.
        with translation.override("ru"):
            assert translation.gettext("A string nobody translated") == "A string nobody translated"

    def test_all_target_locales_have_compiled_mo(self):
        import os
        import snapadmin
        base = os.path.join(os.path.dirname(snapadmin.__file__), "locale")
        for loc in TARGET_LOCALES:
            mo = os.path.join(base, loc, "LC_MESSAGES", "django.mo")
            assert os.path.exists(mo), f"missing compiled catalog for {loc}"


# ── settings wiring ──────────────────────────────────────────────────────────

class TestSettings:
    def test_languages_configured(self):
        from django.conf import settings
        codes = {c for c, _ in settings.LANGUAGES}
        assert {"en", "ru", "de", "de-ch", "fr", "fr-ch", "es", "it", "pl", "nl"} <= codes

    def test_locale_middleware_installed(self):
        from django.conf import settings
        assert "django.middleware.locale.LocaleMiddleware" in settings.MIDDLEWARE


# ── dashboard renders localised ──────────────────────────────────────────────

@pytest.mark.django_db
class TestDashboardLocalised:
    def test_default_english(self):
        html = Client().get("/").content.decode()
        assert "System Health" in html
        assert '<html lang="en">' in html

    def test_russian_via_language_header(self):
        # Accept-Language drives LocaleMiddleware; the dashboard renders in ru.
        html = Client().get("/", HTTP_ACCEPT_LANGUAGE="ru").content.decode()
        assert "Состояние системы" in html
        assert '<html lang="ru">' in html

    def test_language_switcher_present(self):
        html = Client().get("/").content.decode()
        assert 'name="language"' in html          # the switcher <select>
        assert '/i18n/setlang/' in html            # posts to set_language

    def test_set_language_switches_locale(self):
        client = Client()
        resp = client.post("/i18n/setlang/", {"language": "de", "next": "/"})
        assert resp.status_code in (302, 200)
        html = client.get("/").content.decode()
        assert "Verwaltete Modelle" in html        # "Managed Models" in German
