
import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_check_endpoint(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    url = reverse('api-health')
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert 'status' in data
    assert 'services' in data
    assert 'database' in data['services']


@pytest.mark.django_db
def test_dashboard_view(admin_client):
    url = reverse('dashboard')
    response = admin_client.get(url)
    assert response.status_code == 200
    assert b"SnapAdmin Dashboard" in response.content


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard access gate (info-disclosure hardening) + real version
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_dashboard_redirects_anonymous_to_login(client):
    # Anonymous callers must not see infra details — they are sent to login.
    response = client.get(reverse('dashboard'))
    assert response.status_code == 302
    assert '/login' in response['Location'] or 'accounts/login' in response['Location']


@pytest.mark.django_db
def test_dashboard_forbids_non_staff(client, django_user_model):
    user = django_user_model.objects.create_user(username='plain', password='pw')
    client.force_login(user)
    response = client.get(reverse('dashboard'))
    assert response.status_code == 403


@pytest.mark.django_db
def test_dashboard_public_opt_out_allows_anonymous(client, settings):
    settings.SNAPADMIN_DASHBOARD_PUBLIC = True
    response = client.get(reverse('dashboard'))
    assert response.status_code == 200
    assert b"SnapAdmin Dashboard" in response.content


@pytest.mark.django_db
def test_dashboard_loads_no_external_asset_hosts(admin_client):
    # The dashboard must render air-gapped: no CDN/font stylesheets or scripts.
    html = admin_client.get(reverse('dashboard')).content.decode()
    for host in (
        'fonts.googleapis.com',
        'fonts.gstatic.com',
        'cdnjs.cloudflare.com',
        'cdn.jsdelivr.net',
    ):
        assert host not in html, f"external host {host} still referenced"
    # ...and the vendored replacements are wired in.
    assert 'snapadmin/vendor/material-icons.css' in html
    assert 'snapadmin/vendor/chart.umd.min.js' in html
    # font-awesome is gone; the GitHub icon is now inline SVG.
    assert 'fa-github' not in html
    assert '<svg' in html


@pytest.mark.django_db
def test_dashboard_version_read_from_package_metadata(admin_client):
    # The version is no longer hardcoded — it must match the package's __version__.
    import snapadmin

    from snapadmin.views import DashboardView
    ctx = DashboardView().get_context_data()
    assert ctx['version'] == snapadmin.__version__
    assert ctx['version'] != '0.1.0a9'  # the old hardcoded string is gone


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard context - ES storage mode per model
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardEsMode:
    @pytest.mark.django_db
    def test_registered_models_have_es_mode(self, admin_client):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from snapadmin.views import DashboardView

        factory = RequestFactory()
        request = factory.get("/")
        request.user = User.objects.create_superuser("dashtest", password="x")
        view = DashboardView()
        view.request = request
        view.kwargs = {}
        view.args = []
        ctx = view.get_context_data()

        for model in ctx["registered_models"]:
            assert "es_mode" in model, f"Model {model['name']} missing es_mode"
            assert model["es_mode"] in ("db_only", "dual", "es_only")

    @pytest.mark.django_db
    def test_product_model_is_dual_mode(self, admin_client):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from snapadmin.views import DashboardView

        factory = RequestFactory()
        request = factory.get("/")
        request.user = User.objects.create_superuser("dualmodetest", password="x")
        view = DashboardView()
        view.request = request
        view.kwargs = {}
        view.args = []
        ctx = view.get_context_data()

        product_entry = next((m for m in ctx["registered_models"] if m["name"].lower() == "product"), None)
        assert product_entry is not None
        assert product_entry["es_mode"] == "dual"

    @pytest.mark.django_db
    def test_audit_log_has_retention_days(self, admin_client):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from snapadmin.views import DashboardView

        factory = RequestFactory()
        request = factory.get("/")
        request.user = User.objects.create_superuser("retentiontest", password="x")
        view = DashboardView()
        view.request = request
        view.kwargs = {}
        view.args = []
        ctx = view.get_context_data()

        audit = next((m for m in ctx["registered_models"] if m["name"].lower() == "audit log"), None)
        assert audit is not None
        assert audit["retention_days"] == 90


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard context - cron jobs from CELERY_BEAT_SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardCronJobs:
    @pytest.mark.django_db
    def test_cron_jobs_populated_from_beat_schedule(self, admin_client):
        from django.test import RequestFactory, override_settings
        from django.contrib.auth.models import User
        from snapadmin.views import DashboardView

        fake_schedule = {
            "test-task": {
                "task": "demo.tasks.test",
                "schedule": "0 0 * * *",
                "description": "A test task",
            }
        }
        factory = RequestFactory()
        request = factory.get("/")
        request.user = User.objects.create_superuser("crontest", password="x")
        view = DashboardView()
        view.request = request
        view.kwargs = {}
        view.args = []
        with override_settings(CELERY_BEAT_SCHEDULE=fake_schedule):
            ctx = view.get_context_data()

        jobs = ctx["cron_jobs"]
        assert len(jobs) == 1
        assert jobs[0]["name"] == "test-task"
        assert jobs[0]["task"] == "demo.tasks.test"
        assert jobs[0]["description"] == "A test task"

    @pytest.mark.django_db
    def test_cron_jobs_empty_when_no_schedule(self, admin_client):
        from django.test import RequestFactory, override_settings
        from django.contrib.auth.models import User
        from snapadmin.views import DashboardView

        factory = RequestFactory()
        request = factory.get("/")
        request.user = User.objects.create_superuser("crontest2", password="x")
        view = DashboardView()
        view.request = request
        view.kwargs = {}
        view.args = []
        with override_settings(CELERY_BEAT_SCHEDULE={}):
            ctx = view.get_context_data()

        assert ctx["cron_jobs"] == []

    @pytest.mark.django_db
    def test_live_settings_has_all_four_cron_jobs(self, admin_client):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from snapadmin.views import DashboardView

        factory = RequestFactory()
        request = factory.get("/")
        request.user = User.objects.create_superuser("crontest3", password="x")
        view = DashboardView()
        view.request = request
        view.kwargs = {}
        view.args = []
        ctx = view.get_context_data()

        job_names = {j["name"] for j in ctx["cron_jobs"]}
        assert "reindex-products-to-es" in job_names
        assert "purge-expired-data" in job_names
        assert "generate-daily-stats" in job_names
        assert "purge-expired-tokens" in job_names

    @pytest.mark.django_db
    def test_dashboard_html_shows_cron_section(self, admin_client):
        url = reverse('dashboard')
        response = admin_client.get(url)
        assert b"Scheduled Cron Jobs" in response.content
        assert b"reindex-products-to-es" in response.content
