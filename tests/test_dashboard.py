
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
