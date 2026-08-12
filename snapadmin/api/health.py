"""
Public health-check endpoint (``GET /api/health/``).

An unauthenticated readiness/liveness probe that reports database reachability (and
overall status) as JSON with an appropriate HTTP status code, for a load balancer or
container orchestrator. The richer operator-facing diagnostics — Elasticsearch,
Celery, per-model inventory — live in :mod:`snapadmin.diagnostics` behind the
``snapadmin_info`` command; this endpoint stays deliberately small and dependency-free.

**The HTTP status is the probe's answer**, because a container runtime looks at that and
not at the body:

``healthy`` → ``200``
    Everything the request path needs is reachable.
``degraded`` → ``200``
    An *optional* subsystem is down (Elasticsearch). The instance can still serve, so
    pulling it out of the load balancer would make the outage worse — the body says
    ``degraded`` for a monitor to alert on.
``unhealthy`` → ``503``
    The database is unreachable; this instance cannot serve. Restarting or replacing it
    is the right response, which is exactly what a ``503`` tells an orchestrator.
"""

from django.db import connections
from django.db.utils import OperationalError
from django.conf import settings
from rest_framework import status as http_status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema

#: Overall status values that must not take an instance out of rotation.
SERVING_STATUSES = frozenset({"healthy", "degraded"})

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
            def _elasticsearch_down() -> None:
                # Only an *upgrade* in severity may overwrite the overall status:
                # a database outage already set "unhealthy", and Elasticsearch —
                # an optional subsystem — must never soften that back to a
                # still-serving "degraded" (which answers 200 and would keep a
                # dead instance in rotation).
                if health_status["status"] == "healthy":
                    health_status["status"] = "degraded"
                health_status["services"]["elasticsearch"] = "offline"

            try:
                from elasticsearch import Elasticsearch
                url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
                es = Elasticsearch([url], request_timeout=2)
                if es.ping():
                    health_status["services"]["elasticsearch"] = "online"
                else:
                    _elasticsearch_down()
            except Exception:
                _elasticsearch_down()
        else:
            health_status["services"]["elasticsearch"] = "disabled"

        code = (
            http_status.HTTP_200_OK
            if health_status["status"] in SERVING_STATUSES
            else http_status.HTTP_503_SERVICE_UNAVAILABLE
        )

        # Anonymous callers (load balancers, uptime probes) get the overall
        # status only; the per-service breakdown reveals infrastructure detail,
        # so it is reserved for authenticated users (admin session or token —
        # the offline layer polls this endpoint from the admin session).
        if not request.user.is_authenticated:
            return Response({"status": health_status["status"]}, status=code)

        return Response(health_status, status=code)
