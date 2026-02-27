
import os
import platform
from django.views.generic import TemplateView
from django.conf import settings
from django.urls import reverse
from django.db import connections
from django.db.utils import OperationalError

class DashboardView(TemplateView):
    """
    Main SnapAdmin dashboard view providing system health monitoring,
    quick links, and environment details.
    """
    template_name = "snapadmin/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 1. Database & Integration Layer
        services = self._get_service_status()

        # 2. System Architecture & Environment
        env_details = self._get_environment_details()

        # 3. Cron Job Monitor
        cron_jobs = self._get_cron_jobs()

        # Dashboard Quick Links
        links = [
            {"name": "Admin Panel", "url": reverse("admin:index"), "icon": "admin_panel_settings"},
            {"name": "API Root", "url": "/api/", "icon": "api"},
            {"name": "API Schema (OpenAPI)", "url": reverse("api-schema"), "icon": "description"},
            {"name": "Swagger UI", "url": reverse("swagger-ui"), "icon": "menu_book"},
            {"name": "Redoc", "url": reverse("redoc"), "icon": "library_books"},
        ]

        context.update({
            "services": services,
            "links": links,
            "env_details": env_details,
            "cron_jobs": cron_jobs,
            "debug": settings.DEBUG,
            "allowed_hosts": settings.ALLOWED_HOSTS,
            "version": "0.1.0a1",
        })
        return context

    def _get_service_status(self):
        services = []

        # PostgreSQL Monitoring
        db_conn = connections['default']
        db_name = db_conn.settings_dict.get('NAME', 'unknown')
        db_status = "offline"
        try:
            db_conn.cursor()
            db_status = "online"
        except OperationalError:
            pass

        services.append({
            "name": f"Database ({db_name})",
            "status": db_status,
            "is_live": db_status == "online"
        })

        # Elasticsearch Monitoring
        if getattr(settings, "ELASTICSEARCH_ENABLED", False):
            es_status = "offline"
            try:
                from elasticsearch import Elasticsearch
                url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
                es = Elasticsearch([url], request_timeout=1)
                if es.ping():
                    es_status = "online"
            except Exception:
                pass
            services.append({"name": "Elasticsearch", "status": es_status})
        else:
            services.append({"name": "Elasticsearch", "status": "disabled"})

        return services

    def _get_environment_details(self):
        # Environment Detection
        is_docker = os.path.exists('/.dockerenv') or os.path.exists('/proc/self/cgroup') and any('docker' in line for line in open('/proc/self/cgroup'))

        return {
            "mode": "Docker" if is_docker else "Local",
            "os": f"{platform.system()} {platform.release()}",
            "hostname": platform.node(),
            "processor": platform.processor(),
        }

    def _get_cron_jobs(self):
        # Fetch from Celery Beat settings if available
        jobs = []
        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})

        for name, info in beat_schedule.items():
            jobs.append({
                "name": name,
                "task": info.get("task"),
                "schedule": str(info.get("schedule")),
                "description": info.get("description", "No description provided.")
            })

        return jobs
