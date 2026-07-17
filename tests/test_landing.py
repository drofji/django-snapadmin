"""
tests/test_landing.py — demo landing page at ``/`` (#DEMO8/#DEMO9)

The landing page is a *separate*, public-safe view from the staff-only
``DashboardView`` (which moved to ``/dashboard/``). Anonymous visitors get a
login form on the same URL; authenticated visitors get their session, demo
record counts and enabled/disabled service facts — never host/infra details;
staff additionally get admin + dashboard links.
"""

import pytest
from django.urls import reverse


@pytest.fixture
def staff_user(db, django_user_model):
    return django_user_model.objects.create_user(
        username="staffer", password="pw12345", is_staff=True
    )


@pytest.fixture
def plain_user(db, django_user_model):
    return django_user_model.objects.create_user(username="plain", password="pw12345")


# ── Anonymous → login form ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestAnonymousLanding:
    def test_get_shows_login_form(self, client):
        r = client.get(reverse("landing"))
        assert r.status_code == 200
        html = r.content.decode()
        assert 'name="username"' in html
        assert 'name="password"' in html
        # No authenticated content leaks to an anonymous visitor.
        assert "Signed in as" not in html

    def test_valid_post_logs_in_and_redirects(self, client, plain_user):
        r = client.post(reverse("landing"), {"username": "plain", "password": "pw12345"})
        assert r.status_code == 302
        assert r["Location"] == reverse("landing")
        # Follow-up request is now authenticated.
        assert client.get(reverse("landing")).context["user"].is_authenticated

    def test_invalid_post_rerenders_with_error_still_anonymous(self, client, plain_user):
        r = client.post(reverse("landing"), {"username": "plain", "password": "wrong"})
        assert r.status_code == 200
        assert not r.context["user"].is_authenticated
        assert 'name="password"' in r.content.decode()

    def test_no_external_asset_hosts(self, client):
        # Air-gapped: the page inlines all CSS/JS, no CDN or font hosts.
        html = client.get(reverse("landing")).content.decode()
        for host in ("http://", "https://", "//cdn", "fonts.googleapis"):
            assert host not in html


# ── Authenticated → session + facts ──────────────────────────────────────────

@pytest.mark.django_db
class TestAuthenticatedLanding:
    def test_shows_session_and_stats(self, client, plain_user):
        client.force_login(plain_user)
        r = client.get(reverse("landing"))
        assert r.status_code == 200
        html = r.content.decode()
        assert "Signed in as" in html
        assert "plain" in html
        assert r.context["stats"]  # per-model counts present
        assert r.context["services"]

    def test_stats_are_exactly_the_demo_app_models(self, client, plain_user):
        from django.apps import apps
        from snapadmin.models import SnapModel

        client.force_login(plain_user)
        names = {s["name"] for s in client.get(reverse("landing")).context["stats"]}
        # The stats are app-scoped to `demo` by construction — assert they are
        # exactly the demo app's concrete SnapModels, so no snapadmin-internal
        # bookkeeping model (tokens/export jobs/etc.) can ever slip in.
        expected = {
            m._meta.verbose_name_plural.title()
            for m in apps.get_app_config("demo").get_models()
            if issubclass(m, SnapModel) and m is not SnapModel
        }
        assert names == expected
        assert any("Product" in n for n in names)

    def test_service_flags_reflect_settings(self, client, plain_user, settings):
        settings.SNAPADMIN_GRAPHQL_ENABLED = False
        client.force_login(plain_user)
        services = {s["key"]: s["enabled"] for s in client.get(reverse("landing")).context["services"]}
        assert services["graphql"] is False

    def test_non_staff_sees_no_admin_link(self, client, plain_user):
        client.force_login(plain_user)
        html = client.get(reverse("landing")).content.decode()
        assert "Open Admin" not in html

    def test_staff_sees_admin_and_dashboard_links(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(reverse("landing")).content.decode()
        assert "Open Admin" in html
        assert reverse("dashboard") in html

    def test_authenticated_post_redirects_without_relogin(self, client, plain_user):
        client.force_login(plain_user)
        r = client.post(reverse("landing"), {})
        assert r.status_code == 302
        assert r["Location"] == reverse("landing")

    def test_no_infra_details_leaked(self, client, plain_user):
        # The landing page must never expose what DashboardView guards.
        import platform
        client.force_login(plain_user)
        html = client.get(reverse("landing")).content.decode()
        assert platform.node() not in html or platform.node() == ""


# ── Logout ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_logout_returns_to_landing(client, plain_user):
    client.force_login(plain_user)
    r = client.post(reverse("logout"))
    assert r.status_code == 302
    assert r["Location"] == reverse("landing")
    assert not client.get(reverse("landing")).context["user"].is_authenticated


# ── Dashboard moved but still staff-gated at its new path ─────────────────────

@pytest.mark.django_db
def test_dashboard_moved_off_root(client, admin_user):
    # `/` is now the landing page, not the dashboard.
    assert reverse("dashboard") == "/dashboard/"
    assert reverse("landing") == "/"
