"""
Public health-check endpoint (``GET /api/health/``).

An unauthenticated readiness/liveness probe that reports database reachability (and
overall status) as JSON with an appropriate HTTP status code, for a load balancer or
container orchestrator. The richer operator-facing diagnostics — Elasticsearch,
Celery, per-model inventory — live in :mod:`snapadmin.diagnostics` behind the
``snapadmin_info`` command; this endpoint stays deliberately small and dependency-free.
"""

from django.db import connections
from django.db.utils import OperationalError
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema

class HealthCheckView(APIView):
    """
    Check the health of the system.
    """
    permission_classes = [AllowAny]

    @extend_schema(summary="Health check for services")
    def get(self, request):
        health_status = {
            "status": "healthy",
            "services": {
                "database": "offline",
                "elasticsearch": "offline",
            }
        }

        # Check Database
        try:
            db_conn = connections['default']
            db_conn.cursor()
            health_status["services"]["database"] = "online"
        except OperationalError:
            health_status["status"] = "unhealthy"
            health_status["services"]["database"] = "offline"

        # Check Elasticsearch
        if getattr(settings, "ELASTICSEARCH_ENABLED", False):
            try:
                from elasticsearch import Elasticsearch
                url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
                es = Elasticsearch([url], request_timeout=2)
                if es.ping():
                    health_status["services"]["elasticsearch"] = "online"
                else:
                    health_status["status"] = "degraded"
                    health_status["services"]["elasticsearch"] = "offline"
            except Exception:
                health_status["status"] = "degraded"
                health_status["services"]["elasticsearch"] = "offline"
        else:
            health_status["services"]["elasticsearch"] = "disabled"

        # Anonymous callers (load balancers, uptime probes) get the overall
        # status only; the per-service breakdown reveals infrastructure detail,
        # so it is reserved for authenticated users (admin session or token —
        # the offline layer polls this endpoint from the admin session).
        if not request.user.is_authenticated:
            return Response({"status": health_status["status"]})

        return Response(health_status)
