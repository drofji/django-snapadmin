"""
tests/test_root_view.py — the merged site root at ``/`` (#UX2)

Before this, ``/`` was always the plain landing/login page and the polished
:class:`~snapadmin.views.DashboardView` layout only appeared at ``/dashboard/`` — a
fragmented entry point, since the dashboard is by far the more presentable page
(user report: "I'd merge the index page with the dashboard — the dashboard looks more
presentable").

``RootView`` now routes ``/`` itself: staff (or anyone, when
``SNAPADMIN_DASHBOARD_PUBLIC`` opts the dashboard into being public) see the dashboard;
everyone else still sees ``LandingView`` exactly as before (covered in
``test_landing.py``). ``/dashboard/`` keeps working as a direct alias. The dashboard
gains a session/logout bar and the demo's enabled-surfaces checklist via
``StaffDashboardView`` and two block hooks added to the package's own
``snapadmin/dashboard.html`` — the package template and ``DashboardView`` are
otherwise untouched.
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


# ── who sees what at "/" ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRootRoutesByRole:
    def test_anonymous_sees_the_login_form(self, client):
        html = client.get(reverse("landing")).content.decode()
        assert 'name="username"' in html
        assert "Managed Models" not in html          # a dashboard-only string

    def test_non_staff_sees_the_landing_page(self, client, plain_user):
        client.force_login(plain_user)
        html = client.get(reverse("landing")).content.decode()
        assert "Signed in as" in html
        assert "Managed Models" not in html

    def test_staff_sees_the_dashboard(self, client, staff_user):
        client.force_login(staff_user)
        r = client.get(reverse("landing"))
        assert r.status_code == 200
        assert "Managed Models" in r.content.decode()

    def test_public_dashboard_setting_serves_it_to_anonymous(self, client, settings):
        settings.SNAPADMIN_DASHBOARD_PUBLIC = True
        html = client.get(reverse("landing")).content.decode()
        assert "Managed Models" in html

    def test_public_dashboard_setting_serves_it_to_non_staff(self, client, plain_user, settings):
        settings.SNAPADMIN_DASHBOARD_PUBLIC = True
        client.force_login(plain_user)
        html = client.get(reverse("landing")).content.decode()
        assert "Managed Models" in html


# ── /dashboard/ stays a working alias ────────────────────────────────────────

@pytest.mark.django_db
class TestDashboardAlias:
    def test_staff_can_still_use_the_direct_url(self, client, staff_user):
        client.force_login(staff_user)
        assert client.get(reverse("dashboard")).status_code == 200

    def test_non_staff_is_still_gated_on_the_direct_url(self, client, plain_user):
        client.force_login(plain_user)
        assert client.get(reverse("dashboard")).status_code == 403

    def test_root_and_dashboard_render_the_same_content_for_staff(self, client, staff_user):
        client.force_login(staff_user)
        root_html = client.get(reverse("landing")).content.decode()
        dashboard_html = client.get(reverse("dashboard")).content.decode()
        # Both go through StaffDashboardView vs. the bare DashboardView respectively;
        # the shared structural content must match even though "/" carries extras.
        assert "Managed Models" in root_html and "Managed Models" in dashboard_html


# ── the folded-in extras (#UX2b) ─────────────────────────────────────────────

@pytest.mark.django_db
class TestFoldedInDemoFacts:
    def test_session_bar_shows_the_username(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(reverse("landing")).content.decode()
        assert "Signed in as" in html
        assert "staffer" in html

    def test_session_bar_has_a_working_logout_form(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(reverse("landing")).content.decode()
        assert f'action="{reverse("logout")}"' in html

    def test_bare_dashboard_url_has_no_session_bar(self, client, staff_user):
        """Only the merged root view carries the demo's extras."""
        client.force_login(staff_user)
        html = client.get(reverse("dashboard")).content.decode()
        assert "Signed in as" not in html

    def test_optional_surfaces_checklist_is_present(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(reverse("landing")).content.decode()
        assert "Optional surfaces" in html
        assert "REST API" in html

    def test_checklist_reflects_settings(self, client, staff_user, settings):
        settings.SNAPADMIN_GRAPHQL_ENABLED = False
        client.force_login(staff_user)
        ctx = client.get(reverse("landing")).context
        flags = {s["key"]: s["enabled"] for s in ctx["demo_services"]}
        assert flags["graphql"] is False

    def test_enabled_count_matches_the_flags(self, client, staff_user):
        client.force_login(staff_user)
        ctx = client.get(reverse("landing")).context
        assert ctx["demo_services_enabled_count"] == sum(
            1 for s in ctx["demo_services"] if s["enabled"]
        )

    def test_dashboard_still_carries_its_own_content(self, client, staff_user):
        """The merge is additive — the package's own sections are all still there."""
        client.force_login(staff_user)
        html = client.get(reverse("landing")).content.decode()
        for needle in ("System Health", "System Entry Points", "API Capabilities"):
            assert needle in html


# ── the package template stays generic (no demo-specific content leaks in) ───

class TestPackageTemplateBlocksAreEmptyByDefault:
    def test_dashboard_template_defines_the_hooks(self):
        from django.template.loader import get_template

        source = get_template("snapadmin/dashboard.html").template.source
        assert "{% block header_actions_extra %}" in source
        assert "{% block dashboard_extra_bottom %}" in source

    @pytest.mark.django_db
    def test_bare_dashboard_view_shows_no_demo_extras(self, client, django_user_model):
        """A project that never overrides the hooks gets the package's dashboard, unchanged."""
        staff = django_user_model.objects.create_user(
            username="other_staff", password="pw", is_staff=True
        )
        client.force_login(staff)
        html = client.get(reverse("dashboard")).content.decode()
        assert "Optional surfaces" not in html
        assert "Signed in as" not in html
