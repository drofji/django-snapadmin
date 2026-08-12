"""
tests/test_i18n.py — internationalisation (issue #9)

SnapAdmin ships compiled catalogs for 10 locales, wraps its UI strings in
gettext, renders a language switcher, and falls back to English for anything
untranslated.
"""

import pytest
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


# ── catalogs stay complete (guards against re-staling) ───────────────────────

def _parse_po(path):
    """Return a list of (msgid, msgstr) pairs, concatenating multi-line values."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
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


class TestCatalogCompleteness:
    """Every shipped translatable string must carry a translation in every
    locale (en is the source and stays header-only). This is the regression
    guard for #I18N1 — a new ``_()`` string added without regenerating the
    catalogs will leave an empty ``msgstr`` and fail here."""

    def _po_path(self, loc):
        import os
        import snapadmin
        return os.path.join(
            os.path.dirname(snapadmin.__file__), "locale", loc, "LC_MESSAGES", "django.po"
        )

    @pytest.mark.parametrize("locale", [l for l in TARGET_LOCALES if l != "en"])
    def test_no_empty_msgstr(self, locale):
        entries = _parse_po(self._po_path(locale))
        missing = [mid for mid, mstr in entries if mid and not mstr]
        assert not missing, f"{locale} has untranslated strings: {missing}"

    def test_locales_cover_the_same_msgids(self):
        # Every non-en catalog must expose the same set of source strings, so a
        # string translated in one locale is never silently absent from another.
        ref = {mid for mid, _ in _parse_po(self._po_path("ru")) if mid}
        for loc in TARGET_LOCALES:
            if loc in ("en", "ru"):
                continue
            ids = {mid for mid, _ in _parse_po(self._po_path(loc)) if mid}
            assert ids == ref, f"{loc} msgid set diverges from ru"

    @pytest.mark.parametrize("locale,source,expected", [
        ("ru", "Audit Log", "Журнал аудита"),
        ("de", "Export Job", "Exportauftrag"),
        ("fr", "Resume Cursor (PK)", "Curseur de reprise (PK)"),
        ("es", "Error Event", "Evento de error"),
        ("it", "API Token", "Token API"),
        ("nl", "Owner", "Eigenaar"),
        ("pl", "Traceback", "Ślad stosu"),
        ("ru", "Reindex Job", "Задание переиндексации"),
        ("de", "Reindex Jobs", "Reindex-Aufträge"),
    ])
    def test_new_model_strings_translated(self, locale, source, expected):
        with translation.override(locale):
            assert translation.gettext(source) == expected

    def test_format_placeholders_preserved(self):
        # A translated format string must keep its named %(...)s placeholders,
        # or interpolation raises at render time.
        with translation.override("ru"):
            msg = translation.gettext(
                "File extension '%(ext)s' is not allowed. Allowed: %(allowed)s"
            )
        assert "%(ext)s" in msg and "%(allowed)s" in msg


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
    # The dashboard is staff-gated, so render it through an authenticated admin.
    def test_default_english(self, admin_client):
        html = admin_client.get("/dashboard/").content.decode()
        assert "System Health" in html
        assert '<html lang="en">' in html

    def test_russian_via_language_header(self, admin_client):
        # Accept-Language drives LocaleMiddleware; the dashboard renders in ru.
        html = admin_client.get("/dashboard/", HTTP_ACCEPT_LANGUAGE="ru").content.decode()
        assert "Состояние системы" in html
        assert '<html lang="ru">' in html

    def test_language_switcher_present(self, admin_client):
        html = admin_client.get("/dashboard/").content.decode()
        assert 'name="language"' in html          # the switcher <select>
        assert '/i18n/setlang/' in html            # posts to set_language

    def test_set_language_switches_locale(self, admin_client):
        resp = admin_client.post("/i18n/setlang/", {"language": "de", "next": "/dashboard/"})
        assert resp.status_code in (302, 200)
        html = admin_client.get("/dashboard/").content.decode()
        assert "Verwaltete Modelle" in html        # "Managed Models" in German


# ── #I18N2: the dashboard's own dynamic strings ──────────────────────────────

@pytest.mark.django_db
class TestDashboardDynamicStringsLocalised:
    """The dashboard builds much of its content in Python, not in the template.

    Quick links, service names, status badges, the environment mode and the cron
    fallback text were plain English literals, so a Russian dashboard rendered
    half in Russian (template strings) and half in English (view strings).
    """

    def _context(self, locale):
        from django.contrib.auth.models import User
        from django.test import RequestFactory

        from snapadmin.views import DashboardView

        request = RequestFactory().get("/dashboard/")
        request.user = User.objects.create_superuser(f"i18n-{locale}", password="x")
        view = DashboardView()
        view.request, view.args, view.kwargs = request, [], {}
        with translation.override(locale):
            ctx = view.get_context_data()
            # Force the lazy strings while the override is still active.
            return {
                "links": [str(link["name"]) for link in ctx["links"]],
                "services": [(str(s["name"]), str(s["status_label"])) for s in ctx["services"]],
                "mode": str(ctx["env_details"]["mode"]),
                "models": [str(m["name"]) for m in ctx["registered_models"]],
            }

    def test_quick_links_translated(self):
        assert "Панель администратора" in self._context("ru")["links"]
        assert "Admin-Bereich" in self._context("de")["links"]

    def test_service_name_and_status_translated(self):
        services = dict(self._context("ru")["services"])
        assert any(name.startswith("База данных (") for name in services)
        assert services["Elasticsearch"] == "отключено"     # ES is off in the test settings

    def test_environment_mode_translated(self):
        assert self._context("ru")["mode"] in ("Локально", "Docker")

    def test_model_cards_use_the_translated_plural_name(self):
        """capfirst(verbose_name_plural), not verbose_name.title().

        ``.title()`` upper-cased every word, so "журналы аудита" rendered as the
        mangled "Журналы Аудита"; it also force-evaluated the lazy string.
        """
        names = self._context("ru")["models"]
        assert "Журналы аудита" in names
        assert "Журналы Аудита" not in names

    def test_cron_fallback_description_translated(self):
        from django.test import override_settings

        from snapadmin.views import DashboardView

        schedule = {"nameless": {"task": "demo.tasks.noop", "schedule": 60}}
        with override_settings(CELERY_BEAT_SCHEDULE=schedule), translation.override("ru"):
            jobs = DashboardView()._get_cron_jobs()
            assert str(jobs[0]["description"]) == "Описание не указано."

    def test_chart_label_survives_an_apostrophe_in_the_translation(self, admin_client):
        """fr renders "Nombre d'enregistrements" into a single-quoted JS literal.

        Django marks a ``{% translate %}`` result safe, so the apostrophe went in
        raw, closed the string early and broke the whole inline <script> — the
        chart silently never rendered for French users.
        """
        html = admin_client.get("/dashboard/", HTTP_ACCEPT_LANGUAGE="fr").content.decode()
        assert "Nombre d\\u0027enregistrements" in html
        assert "label: 'Nombre d'enregistrements'" not in html

    def test_chart_data_is_json_serialisable(self):
        """It is rendered through json_script, so no lazy proxies may survive."""
        import json

        from django.contrib.auth.models import User
        from django.test import RequestFactory

        from snapadmin.views import DashboardView

        request = RequestFactory().get("/dashboard/")
        request.user = User.objects.create_superuser("chartjson", password="x")
        view = DashboardView()
        view.request, view.args, view.kwargs = request, [], {}
        with translation.override("ru"):
            json.dumps(view.get_context_data()["chart_data"])

    def test_chart_labels_are_not_pasted_raw_into_the_script(self, admin_client):
        html = admin_client.get("/dashboard/").content.decode()
        assert 'id="snap-chart-data"' in html
        assert 'type="application/json"' in html
