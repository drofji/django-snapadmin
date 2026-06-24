"""
snapadmin/api/offline.py

Endpoints that back the admin offline layer (connectivity.js + offline.js):

* ``OfflineModelsView`` (``GET /api/offline-models/``) reports which registered
  SnapModels have ``offline_mode = True`` and each model's cache limit, so the
  connectivity layer can badge sidebar links and label cached counts.
* ``OfflineModelDataView`` (``GET /api/offline-data/<app>/<model>/``) returns the
  most-recent rows of one offline-capable model, serialized, so offline.js can
  prefetch them into IndexedDB for offline viewing.

Both use session authentication (``IsAuthenticated`` with DRF's default auth)
because they are called from the admin browser session — unlike the token-only
``DynamicModelViewSet``.
"""

from django.apps import apps
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema


def _offline_models() -> list:
    """Return the registered offline-capable SnapModel classes."""
    from snapadmin.models import SnapModel

    models = []
    for model in apps.get_models():
        if not (issubclass(model, SnapModel) and model is not SnapModel):
            continue
        if getattr(model, "offline_mode", False):
            models.append(model)
    return models


def get_offline_model_keys() -> list[str]:
    """Return sorted ``"app_label/model_name"`` keys for offline-capable models."""
    keys = [f"{m._meta.app_label}/{m._meta.model_name}" for m in _offline_models()]
    return sorted(keys)


def get_offline_model_limits() -> dict[str, int]:
    """Map each offline-capable model key to its ``offline_cache_limit``."""
    return {
        f"{m._meta.app_label}/{m._meta.model_name}": int(getattr(m, "offline_cache_limit", 100))
        for m in _offline_models()
    }


class OfflineModelsView(APIView):
    """List the models that support offline mode and their cache limits."""

    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List models that support offline mode")
    def get(self, request) -> Response:
        return Response({
            "models": get_offline_model_keys(),
            "limits": get_offline_model_limits(),
        })


class OfflineModelDataView(APIView):
    """Return the most-recent serialized rows of one offline-capable model.

    Only models with ``offline_mode = True`` are exposed — this is the offline
    cache feed, not a general data API (that is ``DynamicModelViewSet``).
    """

    permission_classes = [IsAuthenticated]

    def _get_offline_model(self, app_label: str, model_name: str):
        from snapadmin.models import SnapModel

        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return None
        if not (issubclass(model, SnapModel) and model is not SnapModel):
            return None
        if not getattr(model, "offline_mode", False):
            return None
        return model

    def _resolve_limit(self, request, model) -> int:
        cap = int(getattr(model, "offline_cache_limit", 100))
        raw = request.query_params.get("limit")
        if raw is None:
            return cap
        try:
            requested = int(raw)
        except (TypeError, ValueError):
            return cap
        if requested <= 0:
            return cap
        return min(requested, cap)

    @extend_schema(summary="Recent rows of an offline-capable model for caching")
    def get(self, request, app_label: str, model_name: str) -> Response:
        from snapadmin.api.serializers import get_serializer_for_model

        model = self._get_offline_model(app_label, model_name)
        if model is None:
            raise NotFound("Model is not available for offline mode.")

        limit = self._resolve_limit(request, model)

        # Reuse the DynamicModelViewSet relation strategy: join FKs and prefetch
        # M2M so __str__ / serialization never trigger per-row N+1 queries.
        fields = model._meta.get_fields()
        fk_fields = [f.name for f in fields if getattr(f, "many_to_one", False)]
        m2m_fields = [
            f.name for f in fields
            if getattr(f, "many_to_many", False) and not getattr(f, "auto_created", False)
        ]
        qs = model.objects.all().order_by("-pk")
        if fk_fields:
            qs = qs.select_related(*fk_fields)
        if m2m_fields:
            qs = qs.prefetch_related(*m2m_fields)

        objects = list(qs[:limit])
        serializer_class = get_serializer_for_model(app_label, model_name)
        data = serializer_class(objects, many=True).data

        labels = [str(f.verbose_name) for f in model._meta.fields]

        return Response({
            "model": f"{app_label}/{model_name}",
            "limit": limit,
            "count": len(data),
            "fields": labels,
            "objects": data,
        })
