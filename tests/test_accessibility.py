"""
tests/test_accessibility.py — WCAG 2.1 AA / EAA compliance (issue #8)

Renders the SnapAdmin dashboard and the SSO login partial and asserts the
accessibility affordances are present: a keyboard skip link, ARIA landmarks,
accessible names on icon-only controls, decorative icons hidden from screen
readers, table header scopes, a text alternative for the chart, and a document
language.
"""

import pytest
from django.template import Context, Template


@pytest.mark.django_db
class TestDashboardAccessibility:
    @pytest.fixture
    def html(self, admin_client):
        # The dashboard is staff-gated; render it as an authenticated admin.
        return admin_client.get("/dashboard/").content.decode()

    def test_document_language(self, html):
        assert '<html lang="en">' in html

    def test_skip_link_to_main(self, html):
        assert 'class="skip-link" href="#main-content"' in html
        assert 'Skip to main content' in html

    def test_landmarks(self, html):
        assert 'role="banner"' in html
        assert 'id="main-content" role="main"' in html
        assert '<footer' in html

    def test_icon_only_link_has_accessible_name(self, html):
        assert 'aria-label="SnapAdmin on GitHub (opens in a new tab)"' in html

    def test_decorative_icons_hidden(self, html):
        # No Material icon is exposed to assistive tech without aria-hidden.
        assert '<i class="material-icons">' not in html
        assert 'class="material-icons" aria-hidden="true"' in html

    def test_section_titles_are_headings(self, html):
        assert 'class="section-title" role="heading" aria-level="2"' in html

    def test_chart_has_text_alternative(self, html):
        assert 'role="img" aria-label="Bar chart of record counts per managed model"' in html

    def test_new_tab_link_is_safe(self, html):
        # target=_blank paired with rel=noopener (no reverse-tabnabbing).
        assert 'rel="noopener"' in html


@pytest.mark.django_db
class TestCronTableScope:
    def test_headers_have_scope(self, admin_client, settings):
        # The cron table only renders when a beat schedule is configured.
        settings.CELERY_BEAT_SCHEDULE = {
            "demo": {"task": "x", "schedule": "*/5", "description": "d"}
        }
        html = admin_client.get("/dashboard/").content.decode()
        assert '<th scope="col">Name</th>' in html


class TestSsoPartialAccessibility:
    def test_sso_buttons_group_semantics(self):
        from snapadmin.sso import get_sso_providers
        from django.test import override_settings
        with override_settings(SNAPADMIN_SSO_PROVIDERS={"azure": {"label": "MS", "url": "/a/"}}):
            html = Template('{% include "snapadmin/sso_buttons.html" %}').render(
                Context({"snapadmin_sso_providers": get_sso_providers()})
            )
        assert 'role="group"' in html
        assert 'aria-label=' in html
        assert 'aria-hidden="true"' in html or 'snapadmin-sso__icon' not in html
