"""
tests/test_api_surface_defaults.py

One question, asked of every layer: **what do the API surface switches resolve to when a project
never mentions them?**

``SNAPADMIN_REST_API_ENABLED`` and ``SNAPADMIN_GRAPHQL_ENABLED`` flipped from ``True`` to ``False``
at 1.0 (D4/#DEF2a). The flip landed in ``snapadmin/urls.py`` — which decides what is *mounted* —
and nowhere else, so every layer that decides what to *report* kept answering ``True``. A fresh
install mounted no API while ``manage.py check`` demanded the ``[api]``/``[graphql]`` extras for it
(``snapadmin.E010``), ``snapadmin_info --section features`` listed both surfaces as adopted, and the
dashboard linked to ``/api/``.

The whole suite missed it because ``demo/core/settings.py`` sets all three switches explicitly, so
the unset path — the only path on which a default is observable at all — was never executed. Line
coverage stayed at 100% throughout: every one of those ``get_setting(..., True)`` calls ran, always
with the setting present, always returning the project's value instead of the default.

So this file does two things:

* :class:`TestNoReadSiteHardcodesTheDefault` is the structural guard. It parses ``snapadmin/`` and
  fails if any read site writes the default as a literal next to the setting name, which is what
  let eleven call sites disagree in the first place. Adding a twelfth read site with a hardcoded
  ``True`` fails here, not in production.
* the behavioural classes pin what a project that never configured SnapAdmin's API actually gets.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import pathlib
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import clear_url_caches, reverse

from snapadmin import checks
from snapadmin.conf import GRAPHQL_ENABLED_DEFAULT, REST_API_ENABLED_DEFAULT, get_setting

SNAPADMIN_ROOT = pathlib.Path(checks.__file__).parent

#: The two switches whose default moved at 1.0. ``SWAGGER``/``GRAPHIQL`` are deliberately absent:
#: neither has a default of its own — each follows its parent surface's *resolved* value, which is
#: why passing a name rather than a literal is correct for them.
SWITCHES = ("SNAPADMIN_REST_API_ENABLED", "SNAPADMIN_GRAPHQL_ENABLED")


@contextmanager
def api_switches_unset():
    """A project that never mentions the API switches — the fresh-install path.

    ``override_settings`` can only *set* a name, and a default is observable only while the name is
    genuinely absent. Entering an empty override block swaps in a ``UserSettingsHolder``, which
    supports deletion, so the names can be removed without touching the real settings module;
    everything is restored on exit.
    """
    # Django loads the root URLconf lazily, so snapadmin.urls may still be unimported here — and
    # its mounted routes are import-time constants. Importing it inside the block below would
    # freeze an API-less URLconf into sys.modules for the rest of the session, and the next test to
    # reverse() an API route would fail for reasons that have nothing to do with it. Warm it under
    # the real settings first; the one test that wants the unset value reloads it deliberately.
    importlib.import_module("snapadmin.urls")
    with override_settings():
        for name in (
            "SNAPADMIN_REST_API_ENABLED",
            "SNAPADMIN_GRAPHQL_ENABLED",
            "SNAPADMIN_SWAGGER_ENABLED",
            "SNAPADMIN_GRAPHIQL_ENABLED",
            # The demo's test settings dogfood SNAPADMIN_PROFILE = "full", which legitimately
            # turns both surfaces on. A fresh install has no profile either, and the profile
            # tier sits above the default in get_setting()'s order — leaving it set would mean
            # this helper never reaches the default it exists to observe.
            "SNAPADMIN_PROFILE",
        ):
            delattr(settings, name)
        try:
            yield
        finally:
            # These switches decide what snapadmin.urls mounts, and Django caches the resolved
            # URLconf. Anything reversed while they were unset leaves that cache describing an
            # API-less install, which would then leak into whatever test runs next.
            clear_url_caches()


# ─────────────────────────────────────────────────────────────────────────────
# The structural guard — no read site may spell the default out for itself
# ─────────────────────────────────────────────────────────────────────────────

def _literal_defaults_in(path: pathlib.Path) -> list[str]:
    """Every place in ``path`` where a switch name sits next to a bare boolean.

    Covers both shapes the package actually uses: a ``get_setting(NAME, True)`` call and a
    ``(label, NAME, True)`` row in a feature table. In both the name and its default are siblings,
    so one walk over parents catches either.
    """
    offenders: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Keywords as well as positionals: get_setting(NAME, default=True) is the
            # same regression written differently, and a guard that only reads .args
            # waves it straight through.
            children = list(node.args) + [kw.value for kw in node.keywords]
        elif isinstance(node, (ast.Tuple, ast.List)):
            children = list(node.elts)          # (label, NAME, default) feature-table row
        else:
            continue
        names = [c for c in children if isinstance(c, ast.Constant) and c.value in SWITCHES]
        if not names:
            continue
        bools = [c for c in children if isinstance(c, ast.Constant) and isinstance(c.value, bool)]
        for name in names:
            for flag in bools:
                offenders.append(f"{path.name}:{flag.lineno} — {name.value} defaulted to {flag.value}")
    return offenders


class TestNoReadSiteHardcodesTheDefault:
    def test_no_module_spells_the_default_out_inline(self):
        offenders: list[str] = []
        for path in sorted(SNAPADMIN_ROOT.rglob("*.py")):
            offenders.extend(_literal_defaults_in(path))
        assert not offenders, (
            "these read sites hardcode a default for an API surface switch instead of importing "
            "the shared constant from snapadmin.conf — that is exactly how urls.py and the ten "
            "other read sites drifted apart at 1.0:\n  " + "\n  ".join(offenders)
        )

    def test_the_shared_constants_are_the_1_0_values(self):
        """Pins the flip itself, so restoring the pre-1.0 default is a deliberate, visible edit."""
        assert REST_API_ENABLED_DEFAULT is False
        assert GRAPHQL_ENABLED_DEFAULT is False

    @pytest.mark.parametrize(
        "source",
        [
            'X = get_setting("SNAPADMIN_REST_API_ENABLED", True)',
            'X = get_setting("SNAPADMIN_REST_API_ENABLED", default=True)',
            'ROW = ("rest_api", "SNAPADMIN_REST_API_ENABLED", True)',
        ],
        ids=["positional", "keyword", "table-row"],
    )
    def test_the_guard_would_catch_a_regression(self, source, tmp_path):
        """The guard is only worth having if it fails on every spelling it exists to forbid."""
        probe = tmp_path / "probe.py"
        probe.write_text(source + "\n", encoding="utf-8")
        offenders = _literal_defaults_in(probe)
        assert len(offenders) == 1 and "SNAPADMIN_REST_API_ENABLED" in offenders[0]


# ─────────────────────────────────────────────────────────────────────────────
# What a project that never configured the API actually gets
# ─────────────────────────────────────────────────────────────────────────────

class TestFreshInstallResolvesBothOff:
    def test_get_setting_agrees_with_urls(self):
        with api_switches_unset():
            assert get_setting("SNAPADMIN_REST_API_ENABLED", REST_API_ENABLED_DEFAULT) is False
            assert get_setting("SNAPADMIN_GRAPHQL_ENABLED", GRAPHQL_ENABLED_DEFAULT) is False

    def test_urls_module_mounts_nothing(self):
        """urls.py already had the 1.0 value — this pins that it still agrees after the refactor."""
        urls = importlib.import_module("snapadmin.urls")
        try:
            with api_switches_unset():
                reloaded = importlib.reload(urls)
                assert reloaded.REST_API_ENABLED is False
                assert reloaded.GRAPHQL_ENABLED is False
                assert reloaded.SWAGGER_ENABLED is False  # follows REST
        finally:
            # Restore *outside* the override block: these are import-time module constants, so
            # reloading while the settings are still unset would leave the live URLconf — and
            # Django's cached resolvers — holding the API-less patterns for every later test.
            importlib.reload(urls)
            clear_url_caches()


class TestFreshInstallDoesNotDemandTheExtras:
    """The regression that broke ``manage.py check`` on a base ``pip install django-snapadmin``."""

    def test_no_e010_when_the_surfaces_are_off_and_the_extras_absent(self, monkeypatch):
        real = importlib.util.find_spec
        absent = {"rest_framework", "drf_spectacular", "django_filters", "graphene_django"}
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a, **k: None if name in absent else real(name, *a, **k),
        )
        with api_switches_unset():
            assert checks.check_api_extras_installed(None) == []

    def test_e010_still_fires_once_a_surface_is_turned_on(self, monkeypatch):
        """The check must stay useful — silence is only correct while nothing is mounted."""
        real = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a, **k: None if name == "graphene_django" else real(name, *a, **k),
        )
        with override_settings(SNAPADMIN_GRAPHQL_ENABLED=True):
            ids = [e.id for e in checks.check_api_extras_installed(None)]
        assert "snapadmin.E010" in ids


@pytest.mark.django_db
class TestFreshInstallSkipsTheApiChecks:
    def test_write_surface_checks_are_silent(self):
        with api_switches_unset():
            assert checks.check_api_write_fields(None) == []
            assert checks.check_api_read_only(None) == []

    def test_snap_action_conflict_check_is_silent(self):
        with api_switches_unset():
            assert checks.check_snap_action_read_only_conflict(None) == []


@pytest.mark.django_db
class TestFreshInstallReportsBothOff:
    """``snapadmin_info`` must not claim adoption of a surface that was never mounted."""

    def test_feature_adoption_lists_both_off(self):
        from snapadmin.diagnostics import features

        with api_switches_unset():
            rows = dict(features.collect(verbose=False))
        assert rows["rest_api"] is False
        assert rows["graphql"] is False

    def test_version_collector_lists_both_off(self):
        from snapadmin.diagnostics import version

        with api_switches_unset():
            flags = version.collect(verbose=False)["features"]
        assert flags["rest_api"] is False
        assert flags["graphql"] is False
        assert flags["swagger"] is False  # follows rest_api

    def test_dashboard_offers_no_api_links(self, admin_client):
        with api_switches_unset():
            body = admin_client.get(reverse("dashboard")).content
        assert b"/api/graphql/" not in body
        assert b"REST API Root" not in body


class TestSwaggerFollowsRestEvenUnderAProfile:
    """Swagger has no value of its own — it follows whatever REST *resolved* to.

    The ``api``/``full`` presets nearly pinned ``SNAPADMIN_SWAGGER_ENABLED = True``
    alongside the two switches they do set. That reads harmlessly and breaks the one case
    the cascade exists for: a project on ``SNAPADMIN_PROFILE = "full"`` that then turns
    REST off explicitly would still have mounted ``/api/docs/`` — an OpenAPI page
    documenting a surface with no routes, and an ``ImproperlyConfigured`` at import when
    the ``[api]`` extra is absent, which is exactly what ``urls.py`` says following REST
    prevents.
    """

    def _resolve(self) -> tuple[bool, bool]:
        rest = get_setting("SNAPADMIN_REST_API_ENABLED", REST_API_ENABLED_DEFAULT)
        return rest, get_setting("SNAPADMIN_SWAGGER_ENABLED", rest)

    @pytest.mark.parametrize("profile", ["api", "full"])
    def test_profile_alone_turns_swagger_on_with_rest(self, profile):
        with override_settings(SNAPADMIN_PROFILE=profile):
            for name in ("SNAPADMIN_REST_API_ENABLED", "SNAPADMIN_SWAGGER_ENABLED"):
                delattr(settings, name)
            assert self._resolve() == (True, True)

    @pytest.mark.parametrize("profile", ["api", "full"])
    def test_explicitly_turning_rest_off_takes_swagger_with_it(self, profile):
        with override_settings(SNAPADMIN_PROFILE=profile, SNAPADMIN_REST_API_ENABLED=False):
            delattr(settings, "SNAPADMIN_SWAGGER_ENABLED")
            assert self._resolve() == (False, False)

    def test_swagger_can_still_be_pinned_on_against_the_cascade(self):
        """Following REST is the default, not a rule — an explicit setting still wins."""
        with override_settings(
            SNAPADMIN_PROFILE="full",
            SNAPADMIN_REST_API_ENABLED=False,
            SNAPADMIN_SWAGGER_ENABLED=True,
        ):
            assert self._resolve() == (False, True)


@pytest.mark.django_db
class TestExplicitTrueRestoresThePreviousBehaviour:
    """The documented upgrade path: pin either switch to True and 1.0 behaves like 0.1.0b7."""

    def test_features_and_dashboard_come_back(self, admin_client):
        with override_settings(SNAPADMIN_REST_API_ENABLED=True, SNAPADMIN_GRAPHQL_ENABLED=True):
            from snapadmin.diagnostics import features

            rows = dict(features.collect(verbose=False))
            assert rows["rest_api"] is True
            assert rows["graphql"] is True
            assert b"REST API Root" in admin_client.get(reverse("dashboard")).content


# ─────────────────────────────────────────────────────────────────────────────
# The dashboard's quick links must be reversed, never spelled out
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDashboardLinksAreReversed:
    """The REST and GraphQL quick links used to be the literals ``/api/`` and
    ``/api/graphql/`` while the Swagger link beside them already used ``reverse()``.

    ``SNAPADMIN_URL_PREFIX`` relocates the whole surface, and a project may
    ``include(snapadmin.urls)`` anywhere it likes — either turned those two literals
    into 404s on a dashboard that had just reported both surfaces as enabled.

    Asserted by making ``reverse()`` return something no literal could contain, rather
    than by relocating the URLconf: the mounted routes are import-time constants in
    ``snapadmin.urls``, so a reload-based test passes alone and fails as soon as another
    test has already resolved a URL — which is a fact about Django's resolver cache, not
    about the behaviour under test.
    """

    ROUTES = {"api-root": "/SENTINEL-REST/", "graphql": "/SENTINEL-GRAPHQL/"}

    def test_both_links_come_from_reverse(self, admin_client, monkeypatch):
        from snapadmin import views

        real = views.reverse
        monkeypatch.setattr(
            views, "reverse",
            lambda name, *a, **kw: self.ROUTES.get(name) or real(name, *a, **kw),
        )
        with override_settings(
            SNAPADMIN_REST_API_ENABLED=True, SNAPADMIN_GRAPHQL_ENABLED=True,
        ):
            body = admin_client.get(reverse("dashboard")).content.decode()
        for name, sentinel in self.ROUTES.items():
            assert sentinel in body, f"the {name} link is not reversed — it is hardcoded"

    def test_a_surface_enabled_without_its_routes_costs_a_link_not_the_page(
        self, admin_client, monkeypatch,
    ):
        """A project can switch GraphQL on and still never include the URLconf.

        Reversing an absent route would then raise inside ``get_context_data`` and turn
        the whole dashboard into a 500 — a worse outcome than one missing link.
        """
        from django.urls import NoReverseMatch

        from snapadmin import views

        real = views.reverse

        def _fail_graphql(name, *args, **kwargs):
            if name == "graphql":
                raise NoReverseMatch(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(views, "reverse", _fail_graphql)
        with override_settings(SNAPADMIN_GRAPHQL_ENABLED=True):
            response = admin_client.get(reverse("dashboard"))
        assert response.status_code == 200
        assert "/api/graphql/" not in response.content.decode()
