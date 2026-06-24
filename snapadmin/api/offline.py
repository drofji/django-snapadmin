"""
snapadmin/api/offline.py

Lightweight endpoint that reports which registered SnapModels have
``offline_mode = True``. The admin connectivity layer (connectivity.js) fetches
this list to badge sidebar links and to decide whether the current page can be
edited while offline.
"""

from django.apps import apps
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema


def get_offline_model_keys() -> list[str]:
    """Return sorted ``"app_label/model_name"`` keys for offline-capable models."""
    from snapadmin.models import SnapModel

    keys = []
    for model in apps.get_models():
        if not (issubclass(model, SnapModel) and model is not SnapModel):
            continue
        if getattr(model, "offline_mode", False):
            keys.append(f"{model._meta.app_label}/{model._meta.model_name}")
    return sorted(keys)


class OfflineModelsView(APIView):
    """List the models that support offline mode."""

    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List models that support offline mode")
    def get(self, request) -> Response:
        return Response({"models": get_offline_model_keys()})
