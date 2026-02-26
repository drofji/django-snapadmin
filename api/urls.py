"""
api/urls.py

URL configuration for the SnapAdmin REST API.

Endpoint layout:
  /api/tokens/                              — Token management (list, create)
  /api/tokens/{id}/                         — Token detail (retrieve, delete)
  /api/models/schema/                       — List all available model endpoints
  /api/models/{app_label}/{model_name}/     — Model list + create
  /api/models/{app_label}/{model_name}/{pk}/— Model detail (retrieve, update, delete)
  /api/docs/                                — Swagger UI (drf-spectacular)
  /api/schema/                              — OpenAPI 3 JSON schema download
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from api.views import APITokenViewSet, DynamicModelViewSet, ModelSchemaView

router = DefaultRouter()
router.register(r"tokens", APITokenViewSet, basename="api-token")

# TODO - Die API soll komplett ohne zusatz API-app (loeschen) in Snapadmin als auch ModelAdmin. In Sandbox soll nur ein URLs includiers werden, von snapadmin
urlpatterns = [
    # Token management
    path("", include(router.urls)),

    # Model introspection
    path("models/schema/", ModelSchemaView.as_view(), name="model-schema"),

    # Dynamic model CRUD  ─  list + create
    path(
        "models/<str:app_label>/<str:model_name>/",
        DynamicModelViewSet.as_view({
            "get":  "list",
            "post": "create",
        }),
        name="model-list",
    ),

    # Dynamic model CRUD  ─  detail + update + delete
    path(
        "models/<str:app_label>/<str:model_name>/<int:pk>/",
        DynamicModelViewSet.as_view({
            "get":    "retrieve",
            "put":    "update",
            "patch":  "partial_update",
            "delete": "destroy",
        }),
        name="model-detail",
    ),

    # OpenAPI schema & interactive docs
    path("schema/",  SpectacularAPIView.as_view(),      name="api-schema"),
    path("docs/",    SpectacularSwaggerView.as_view(url_name="api-schema"), name="swagger-ui"),
    path("redoc/",   SpectacularRedocView.as_view(url_name="api-schema"),   name="redoc"),
]
