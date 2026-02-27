"""
snapadmin/api/views.py

SnapAdmin REST API views.
"""

import logging

from django.apps import apps
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from snapadmin.api.authentication import APITokenAuthentication, token_has_permission
from snapadmin.models import APIToken, SnapModel
from snapadmin.api.serializers import (
    APITokenCreateSerializer,
    APITokenSerializer,
    get_serializer_for_model,
)

logger = logging.getLogger("snapadmin.api.views")

# Global cache for model field introspection to avoid repeated _meta.get_fields() calls
_model_field_cache = {}


def _get_cached_fields(model_class):
    """
    Returns a tuple of (fk_fields, m2m_fields) for the given model class.
    Results are cached to improve performance in high-traffic API endpoints.
    """
    if model_class not in _model_field_cache:
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
        _model_field_cache[model_class] = (fk_fields, m2m_fields)
    return _model_field_cache[model_class]


class IsTokenOwnerOrAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj: APIToken):
        return obj.user == request.user or request.user.is_superuser


class TokenModelPermission(permissions.BasePermission):
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


class APITokenViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    authentication_classes = [APITokenAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsTokenOwnerOrAdmin]

    def get_queryset(self):
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
        output = APITokenSerializer(token)
        return Response(output.data, status=status.HTTP_201_CREATED)


class DynamicModelViewSet(viewsets.ModelViewSet):
    authentication_classes = [APITokenAuthentication]
    permission_classes = [permissions.IsAuthenticated, TokenModelPermission]

    def _get_model_class(self):
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

        # Optimization: Use cached field introspection to avoid _meta overhead on every request
        fk_fields, m2m_fields = _get_cached_fields(model_class)

        if fk_fields:
            # Use select_related for ForeignKeys to avoid N+1 queries if the model's
            # __str__ or other properties access related objects during serialization.
            qs = qs.select_related(*fk_fields)
        if m2m_fields:
            # Use prefetch_related for Many-to-Many to avoid N+1 queries
            # when serializing lists of related IDs.
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


class ModelSchemaView(APIView):
    authentication_classes = [APITokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(summary="List all available model API endpoints")
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
