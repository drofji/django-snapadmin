"""
snapadmin/urls.py

URL configuration for the SnapAdmin REST API.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from snapadmin.api.views import (
    APITokenViewSet,
    DynamicModelViewSet,
    ModelSchemaView,
    SnapGraphQLView,
)
from snapadmin.api.health import HealthCheckView
from snapadmin.api.graphql import schema

router = DefaultRouter()
router.register(r"tokens", APITokenViewSet, basename="api-token")

urlpatterns = [
    # Token management
    path("", include(router.urls)),

    # Model introspection
    path("models/schema/", ModelSchemaView.as_view(), name="model-schema"),

    # Health check
    path("health/", HealthCheckView.as_view(), name="api-health"),

    # GraphQL endpoint
    path(
        "graphql/",
        SnapGraphQLView.as_view(graphiql=True, schema=schema),
        name="graphql",
    ),

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
