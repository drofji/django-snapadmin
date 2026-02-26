"""
api/views.py

SnapAdmin REST API views.

Provides:
  - APITokenViewSet       : CRUD management for the caller's own API tokens.
  - DynamicModelViewSet   : Auto-generated CRUD for any registered Django model.
  - api_schema_metadata   : Returns a list of available model endpoints.

All endpoints require Token authentication. CRUD actions are additionally
guarded by Django's standard model permissions.
"""

import logging

from django.apps import apps
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiParameter

from api.authentication import APITokenAuthentication, token_has_permission
from api.models import APIToken
from api.serializers import (
    APITokenCreateSerializer,
    APITokenSerializer,
    get_serializer_for_model,
)
from snapadmin.models import SnapModel

logger = logging.getLogger("snapadmin.api.views")


# ─────────────────────────────────────────────────────────────────────────────
# Permission helpers
# ─────────────────────────────────────────────────────────────────────────────

class IsTokenOwnerOrAdmin(permissions.BasePermission):
    """Allow access only to the token's owner or a Django superuser."""

    def has_object_permission(self, request, view, obj: APIToken):
        return obj.user == request.user or request.user.is_superuser


class TokenModelPermission(permissions.BasePermission):
    """
    Combines APIToken model-scope checks with Django's standard permissions.

    Requires ``request.auth`` to be an APIToken instance.
    """

    _action_map = {
        "list":    "view",
        "retrieve": "view",
        "create":  "add",
        "update":  "change",
        "partial_update": "change",
        "destroy": "delete",
    }

    def has_permission(self, request: Request, view) -> bool:
        token = getattr(request, "auth", None)
        if not isinstance(token, APIToken):
            return False

        app_label  = view.kwargs.get("app_label", "")
        model_name = view.kwargs.get("model_name", "")
        action_str = self._action_map.get(view.action, "view")

        return token_has_permission(
            token, request.user, app_label, model_name, action_str
        )


# ─────────────────────────────────────────────────────────────────────────────
# Token management viewset
# ─────────────────────────────────────────────────────────────────────────────

class APITokenViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Manage API tokens for the authenticated user.

    list:   GET  /api/tokens/
    create: POST /api/tokens/
    detail: GET  /api/tokens/{id}/
    delete: DELETE /api/tokens/{id}/
    """

    authentication_classes = [APITokenAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsTokenOwnerOrAdmin]

    def get_queryset(self):
        """Restrict to tokens owned by the current user (unless superuser)."""
        if self.request.user.is_superuser:
            return APIToken.objects.select_related("user").all()
        return APIToken.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return APITokenCreateSerializer
        return APITokenSerializer

    @extend_schema(summary="Create a new API token")
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        token = serializer.save()
        # Return the full token key once — callers must save it now
        output = APITokenSerializer(token)
        return Response(output.data, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic model viewset
# ─────────────────────────────────────────────────────────────────────────────

class DynamicModelViewSet(viewsets.ModelViewSet):
    """
    Auto-generated CRUD ViewSet for any registered Django model.

    URL pattern:
        /api/models/{app_label}/{model_name}/
        /api/models/{app_label}/{model_name}/{pk}/

    Access is controlled by:
      1. A valid APIToken in the Authorization header.
      2. The token's ``allowed_models`` list (empty = unrestricted).
      3. The user's standard Django CRUD permissions.
    """

    authentication_classes = [APITokenAuthentication]
    permission_classes = [permissions.IsAuthenticated, TokenModelPermission]

    def _get_model_class(self):
        """Resolve and return the model class from URL kwargs."""
        app_label  = self.kwargs["app_label"]
        model_name = self.kwargs["model_name"]
        try:
            return apps.get_model(app_label, model_name)
        except LookupError:
            return None

    def get_queryset(self):
        model_class = self._get_model_class()
        if model_class is None:
            return []

        qs = model_class.objects.all()

        # Apply select_related / prefetch_related for FK/M2M fields
        fk_fields = [
            f.name
            for f in model_class._meta.get_fields()
            if hasattr(f, "many_to_one") and f.many_to_one
        ]
        m2m_fields = [
            f.name
            for f in model_class._meta.get_fields()
            if hasattr(f, "many_to_many") and f.many_to_many and not f.auto_created
        ]

        if fk_fields:
            qs = qs.select_related(*fk_fields)
        if m2m_fields:
            qs = qs.prefetch_related(*m2m_fields)

        return qs

    def get_serializer_class(self):
        app_label  = self.kwargs.get("app_label", "")
        model_name = self.kwargs.get("model_name", "")
        try:
            return get_serializer_for_model(app_label, model_name)
        except LookupError:
            return None

    def list(self, request, *args, **kwargs):
        model_class = self._get_model_class()
        if model_class is None:
            return Response(
                {"detail": f"Model '{kwargs.get('model_name')}' not found in app '{kwargs.get('app_label')}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return super().list(request, *args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# API schema metadata endpoint
# ─────────────────────────────────────────────────────────────────────────────

class ModelSchemaView(APIView):
    """
    Return a list of all SnapModel-backed endpoints available via the API.

    GET /api/schema/models/
    """

    authentication_classes = [APITokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="List all available model API endpoints",
        description="Returns app label, model name, and the endpoint URL for every SnapModel.",
    )
    def get(self, request: Request) -> Response:
        token = request.auth
        results = []

        for model in apps.get_models():
            if not (issubclass(model, SnapModel) and model is not SnapModel):
                continue

            app_label  = model._meta.app_label
            model_name = model.__name__

            if isinstance(token, APIToken) and not token.can_access_model(app_label, model_name):
                continue

            results.append({
                "app_label":  app_label,
                "model_name": model_name,
                "verbose_name": str(model._meta.verbose_name),
                "verbose_name_plural": str(model._meta.verbose_name_plural),
                "endpoint": request.build_absolute_uri(
                    f"/api/models/{app_label}/{model_name}/"
                ),
                "fields": [
                    {
                        "name": f.name,
                        "type": f.__class__.__name__,
                    }
                    for f in model._meta.get_fields()
                    if hasattr(f, "name")
                ],
            })

        return Response({"models": results, "count": len(results)})
