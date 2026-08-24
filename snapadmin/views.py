"""
Server-rendered views for SnapAdmin.

``DashboardView`` is the staff-gated landing dashboard (system/health/version
summary, rendered from :mod:`snapadmin.diagnostics`-style data), and
``StaffRequiredMixin`` is the shared ``AccessMixin`` that restricts a view to staff
users. The REST/GraphQL API surfaces live under :mod:`snapadmin.api`; this module is
the small HTML side.
"""

import os
import platform
from django.contrib.auth.mixins import AccessMixin
from django.views.generic import TemplateView
from django.conf import settings
from django.urls import reverse
from django.db import connections
from django.db.utils import OperationalError
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from snapadmin import __version__

#: Display label per service-probe state. The raw state stays on the context as
#: ``status`` because the template turns it into a CSS class (``status-online``);
#: only ``status_label`` is shown to the reader.
SERVICE_STATUS_LABELS = {
    "online": _("online"),
    "offline": _("offline"),
    "disabled": _("disabled"),
}


class StaffRequiredMixin(AccessMixin):
    """Gate a view behind an authenticated staff user unless opted out.

    The dashboard exposes infrastructure details (hostname, processor, database
    name, service health, ``ALLOWED_HOSTS``), so it must not be world-readable by
    default. Access requires ``is_staff``; unauthenticated callers are sent to the
    login page and authenticated non-staff get a 403. Set
    ``SNAPADMIN_DASHBOARD_PUBLIC = True`` to serve it without any gate (e.g. an
    intentionally public status page).
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(settings, "SNAPADMIN_DASHBOARD_PUBLIC", False):
            return super().dispatch(request, *args, **kwargs)
        user = request.user
        if not (user.is_authenticated and user.is_staff):
            # Anonymous → redirect to login; authenticated-but-not-staff → 403.
            self.raise_exception = user.is_authenticated
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class DashboardView(StaffRequiredMixin, TemplateView):
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
            {"name": _("Admin Panel"), "url": reverse("admin:index"), "icon": "admin_panel_settings"},
        ]

        if getattr(settings, "SNAPADMIN_REST_API_ENABLED", True):
            links.append({"name": _("REST API Root"), "url": "/api/", "icon": "api"})
            if getattr(settings, "SNAPADMIN_SWAGGER_ENABLED", True):
                links.append({"name": _("Swagger Docs"), "url": reverse("swagger-ui"), "icon": "menu_book"})

        if getattr(settings, "SNAPADMIN_GRAPHQL_ENABLED", True):
            links.append({"name": _("GraphQL API"), "url": "/api/graphql/", "icon": "account_tree"})

        # Registered Models
        from django.apps import apps
        from django.contrib import admin
        from django.db.models import Count

        from snapadmin.registry import get_model_meta, is_registered
        registered_models = []

        # Stats for charts
        chart_data = {
            "labels": [],
            "counts": []
        }

        from snapadmin.models import EsStorageMode
        for model in apps.get_models():
            if is_registered(model):
                # A model can opt out of the admin entirely (admin_enabled = False),
                # in which case it has no changelist to link to — reverse() would
                # raise NoReverseMatch and take the whole dashboard down with it.
                # Such a model isn't "managed" in any admin sense, so it is simply
                # not a dashboard card.
                if model not in admin.site._registry:
                    continue

                count = 0
                try:
                    count = model.objects.count() if model._meta.managed else 0
                except Exception:
                    pass

                es_mode = get_model_meta(model, "es_storage_mode", EsStorageMode.DB_ONLY)
                # capfirst on the *plural* name, not .title(): a card counts many
                # rows, and .title() both force-evaluates the lazy translation and
                # upper-cases every word — which mangles non-English names
                # ("журналы аудита" → "Журналы Аудита").
                label = capfirst(model._meta.verbose_name_plural)
                model_info = {
                    "name": label,
                    "app": model._meta.app_label,
                    "count": count,
                    "url": reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"),
                    "es_mode": es_mode.value if hasattr(es_mode, "value") else str(es_mode),
                    "retention_days": get_model_meta(model, "data_retention_days", None),
                }
                registered_models.append(model_info)

                if model._meta.app_label != 'snapadmin':
                    chart_data["labels"].append(str(label))
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
            "version": __version__,
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
            "name": _("Database (%(name)s)") % {"name": db_name},
            "status": db_status,
            "status_label": SERVICE_STATUS_LABELS[db_status],
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
        else:
            es_status = "disabled"
        # "Elasticsearch" is a product name — never translated.
        services.append({
            "name": "Elasticsearch",
            "status": es_status,
            "status_label": SERVICE_STATUS_LABELS[es_status],
        })

        return services

    def _get_environment_details(self):
        # Environment Detection
        is_docker = os.path.exists('/.dockerenv')
        if not is_docker and os.path.exists('/proc/self/cgroup'):
            with open('/proc/self/cgroup') as cgroup_file:
                is_docker = any('docker' in line for line in cgroup_file)

        return {
            # "Docker" is a product name; "Local" is prose and gets translated.
            "mode": "Docker" if is_docker else _("Local"),
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
                "description": info.get("description") or _("No description provided.")
            })

        return jobs
