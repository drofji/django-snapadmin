
import logging
from django.db import connections
from django.db.utils import OperationalError
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema

logger = logging.getLogger("snapadmin.api.health")

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

        return Response(health_status)
