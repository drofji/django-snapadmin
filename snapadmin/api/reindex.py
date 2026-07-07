"""
snapadmin/api/reindex.py

Admin-only HTTP endpoint to bulk-reindex ES-enabled SnapModels.

    POST /api/es/reindex/            reindex every ES-enabled SnapModel

Off by default: enable it with ``SNAPADMIN_REINDEX_API_ENABLED = True``. The
caller must be a Django staff/superuser (``IsAdminUser``). By default the
reindex runs synchronously and returns a per-model summary; set
``SNAPADMIN_REINDEX_API_ASYNC = True`` to offload it to the
``snapadmin.run_es_reindex`` Celery task instead (202 + task id), which requires
Celery to be installed and a broker configured.
"""

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from snapadmin.api.authentication import SnapAPIAuthMixin
from snapadmin.models import run_reindex


def reindex_api_enabled() -> bool:
    return bool(getattr(settings, "SNAPADMIN_REINDEX_API_ENABLED", False))


def reindex_api_async() -> bool:
    return bool(getattr(settings, "SNAPADMIN_REINDEX_API_ASYNC", False))


class ESReindexView(SnapAPIAuthMixin, APIView):
    """Trigger a bulk Elasticsearch reindex over HTTP (admin only, opt-in)."""

    permission_classes = [permissions.IsAdminUser]

    @extend_schema(summary="Bulk-reindex all ES-enabled SnapModels into Elasticsearch")
    def post(self, request: Request) -> Response:
        # Opt-in surface: when disabled the endpoint behaves as if it does not
        # exist so it never advertises itself on a default install.
        if not reindex_api_enabled():
            return Response(
                {"detail": "The ES reindex API is disabled. "
                           "Set SNAPADMIN_REINDEX_API_ENABLED = True to enable it."},
                status=status.HTTP_404_NOT_FOUND,
            )

        chunk_size = self._chunk_size(request)

        if reindex_api_async():
            # Celery is optional — surface a clear 503 instead of a raw
            # ModuleNotFoundError (500) when it is not installed/configured.
            try:
                from snapadmin.tasks import run_es_reindex
            except ImportError:
                return Response(
                    {"detail": "Async reindex requires Celery. Install it with "
                               "`pip install django-snapadmin[celery]` and configure a broker "
                               "(CELERY_BROKER_URL), or unset SNAPADMIN_REINDEX_API_ASYNC."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            result = run_es_reindex.delay(chunk_size=chunk_size)
            return Response(
                {"detail": "Reindex dispatched.", "task_id": str(result.id), "async": True},
                status=status.HTTP_202_ACCEPTED,
            )

        summary = run_reindex(chunk_size=chunk_size)
        return Response({"async": False, **summary}, status=status.HTTP_200_OK)

    @staticmethod
    def _chunk_size(request: Request) -> int:
        """Optional ``chunk_size`` (body or query); falls back to 500 on junk."""
        raw = request.data.get("chunk_size") if hasattr(request, "data") else None
        if raw is None:
            raw = request.query_params.get("chunk_size")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 500
        return value if value > 0 else 500
