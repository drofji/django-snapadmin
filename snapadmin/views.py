
from django.views.generic import TemplateView
from django.conf import settings
from django.urls import reverse
from django.db import connections
from django.db.utils import OperationalError

class DashboardView(TemplateView):
    template_name = "snapadmin/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Service status
        services = []

        # Database
        db_status = "offline"
        try:
            connections['default'].cursor()
            db_status = "online"
        except OperationalError:
            pass
        services.append({"name": "PostgreSQL", "status": db_status, "url": "#"})

        # Elasticsearch
        es_status = "offline"
        if getattr(settings, "ELASTICSEARCH_ENABLED", False):
            try:
                from elasticsearch import Elasticsearch
                url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
                es = Elasticsearch([url], request_timeout=1)
                if es.ping():
                    es_status = "online"
            except Exception:
                pass
            services.append({"name": "Elasticsearch", "status": es_status, "url": "#"})

        # Links
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
            "debug": settings.DEBUG,
            "allowed_hosts": settings.ALLOWED_HOSTS,
            "version": "0.1.0a1",
        })
        return context
