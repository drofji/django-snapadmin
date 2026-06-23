
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
        ]

        if getattr(settings, "SNAPADMIN_REST_API_ENABLED", True):
            links.append({"name": "REST API Root", "url": "/api/", "icon": "api"})
            if getattr(settings, "SNAPADMIN_SWAGGER_ENABLED", True):
                links.append({"name": "Swagger Docs", "url": reverse("swagger-ui"), "icon": "menu_book"})

        if getattr(settings, "SNAPADMIN_GRAPHQL_ENABLED", True):
            links.append({"name": "GraphQL API", "url": "/api/graphql/", "icon": "account_tree"})

        # Registered Models
        from snapadmin.models import SnapModel
        from django.apps import apps
        from django.db.models import Count
        registered_models = []

        # Stats for charts
        chart_data = {
            "labels": [],
            "counts": []
        }

        for model in apps.get_models():
            if issubclass(model, SnapModel) and model is not SnapModel:
                count = 0
                try:
                    count = model.objects.count() if model._meta.managed else 0
                except Exception:
                    pass

                model_info = {
                    "name": model._meta.verbose_name.title(),
                    "app": model._meta.app_label,
                    "count": count,
                    "url": reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
                }
                registered_models.append(model_info)

                if model._meta.app_label != 'snapadmin':
                    chart_data["labels"].append(model._meta.verbose_name.title())
                    chart_data["counts"].append(count)

        context.update({
            "services": services,
            "links": links,
            "registered_models": registered_models,
            "chart_data": chart_data,
            "env_details": env_details,
            "cron_jobs": cron_jobs,
            "debug": settings.DEBUG,
            "allowed_hosts": settings.ALLOWED_HOSTS,
            "version": "0.1.0a1",
            "graphql_enabled": getattr(settings, "SNAPADMIN_GRAPHQL_ENABLED", True),
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
