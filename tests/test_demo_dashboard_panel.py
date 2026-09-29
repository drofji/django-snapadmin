"""
tests/test_demo_dashboard_panel.py

The demo overrides ``admin/index.html`` with its own dashboard panel.

Regression cover for #UX1: the panel used to live in a template that extended an
Unfold layout and was pulled in with ``{% include %}``, so rendering ``/admin/``
emitted the entire admin shell — ``<html>``, header, sidebar, menu — a second time
inside the content block. The body is now a partial fed by the ``{% demo_dashboard %}``
inclusion tag, which cannot re-enter a layout.

Also pins that the panel shows *live* figures. It previously rendered invented
placeholders ("--", "5,678", "New order #890 by John Doe"), which is the worst
possible thing for a project whose demo doubles as its documentation.
"""

from decimal import Decimal

import pytest

from demo.apps.shop.templatetags import demo_dashboard as tag_module


# ─────────────────────────────────────────────────────────────────────────────
# #UX1 — the admin shell must be rendered exactly once
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminIndexRendersOneShell:
    def test_single_html_document(self, admin_client):
        html = admin_client.get("/admin/").content.decode()
        assert html.count("<html") == 1
        assert html.count("</body>") == 1

    def test_single_sidebar(self, admin_client):
        html = admin_client.get("/admin/").content.decode()
        assert html.count('id="nav-sidebar"') <= 1
        # Unfold renders one <aside> shell; a duplicated layout doubles it.
        assert html.count("<aside") <= 1

    def test_panel_body_is_not_a_layout(self):
        """The partial must never extend a template — that is what caused #UX1."""
        from django.template.loader import get_template

        source = get_template("demo/_dashboard_body.html").template.source
        assert "{% extends" not in source


# ─────────────────────────────────────────────────────────────────────────────
# Live figures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDemoDashboardTag:
    def test_counts_are_live(self, order, customer_inactive, product, product_unavailable):
        from tests.conftest import DEFAULT_TEST_TENANT
        from snapadmin.tenancy import use_tenant

        # demo_dashboard() reads the ambient tenant context — in a real admin
        # render that is bound by SnapTenantMiddleware for the viewer's own
        # session (see test_rendered_panel_shows_the_numbers below, which
        # exercises exactly that path); called directly here, this test
        # binds the same tenant the `order` fixture stamped its row with.
        with use_tenant(DEFAULT_TEST_TENANT):
            ctx = tag_module.demo_dashboard()
        assert ctx["total_orders"] == 1
        assert ctx["active_customers"] == 1          # `customer`, not `customer_inactive`
        assert ctx["revenue"] == Decimal("99.99")
        assert ctx["product_total"] == 2
        assert ctx["product_available"] == 1
        assert ctx["availability_percent"] == 50

    def test_empty_database_does_not_divide_by_zero(self):
        ctx = tag_module.demo_dashboard()
        assert ctx["total_orders"] == 0
        assert ctx["revenue"] == Decimal("0")
        assert ctx["availability_percent"] == 0

    def test_recent_activity_lists_real_rows(self, order):
        from tests.conftest import DEFAULT_TEST_TENANT
        from snapadmin.tenancy import use_tenant

        with use_tenant(DEFAULT_TEST_TENANT):
            rows = tag_module.demo_dashboard()["recent_activity"]
        texts = " ".join(str(r["text"]) for r in rows)
        assert f"#{order.pk}" in texts
        assert "Alice" in texts                      # the `customer` fixture

    def test_disk_percent_is_a_percentage(self):
        assert 0 <= tag_module.demo_dashboard()["disk_percent"] <= 100

    def test_disk_percent_handles_empty_volume(self, monkeypatch):
        monkeypatch.setattr(
            tag_module.shutil, "disk_usage",
            lambda path: type("U", (), {"total": 0, "used": 0, "free": 0})(),
        )
        assert tag_module._disk_usage_percent() == 0

    def test_rendered_panel_shows_the_numbers(self, admin_client, order):
        html = admin_client.get("/admin/").content.decode()
        assert "99.99" in html
        assert "5,678" not in html                   # the old hardcoded placeholder


# ─────────────────────────────────────────────────────────────────────────────
# The index works under either admin theme (#QA1e)
# ─────────────────────────────────────────────────────────────────────────────

def _unfold_installed(monkeypatch, installed: bool) -> None:
    """Answer ``apps.is_installed("unfold")`` with ``installed``, and nothing else."""
    from django.apps import apps

    real = apps.is_installed
    monkeypatch.setattr(
        apps, "is_installed", lambda name: installed if name == "unfold" else real(name)
    )


@pytest.mark.django_db
class TestAdminIndexUnderEitherTheme:
    """The demo's index used to extend ``unfold/layouts/base.html`` directly, so
    ``/admin/`` answered 500 on the stock admin the package promises to support.
    It now extends the next ``admin/index.html`` in the loader chain and shows
    the Unfold-only panel only when that chain is Unfold's — when ``unfold`` is
    an installed app. The real stock-admin page is rendered by the browser
    suite's stock run; here the answer is flipped in-process to pin the
    template's two branches."""

    PANEL_HEADING = "SnapAdmin Custom Dashboard"

    def test_the_themed_admin_shows_the_panel(self, admin_client):
        html = admin_client.get("/admin/").content.decode()

        assert self.PANEL_HEADING in html

    def test_the_stock_admin_keeps_the_app_list_instead(self, admin_client, monkeypatch):
        _unfold_installed(monkeypatch, False)

        response = admin_client.get("/admin/")

        html = response.content.decode()
        assert response.status_code == 200
        assert self.PANEL_HEADING not in html
        assert 'href="/admin/demo/product/"' in html

    @pytest.mark.parametrize("unfold_installed, expected", [(True, "unfold"), (False, "stock")])
    def test_the_theme_tag_follows_the_installed_apps(
        self, monkeypatch, unfold_installed, expected
    ):
        _unfold_installed(monkeypatch, unfold_installed)

        assert tag_module.demo_admin_theme() == expected
