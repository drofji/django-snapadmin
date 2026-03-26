"""
snapadmin/urls.py

URL configuration for the SnapAdmin REST API.
"""

from django.urls import path, include
from django.conf import settings
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from snapadmin.api.views import APITokenViewSet, DynamicModelViewSet, ModelSchemaView
from snapadmin.api.health import HealthCheckView
from snapadmin.api.sw_view import service_worker

REST_API_ENABLED = getattr(settings, "SNAPADMIN_REST_API_ENABLED", True)
SWAGGER_ENABLED = getattr(settings, "SNAPADMIN_SWAGGER_ENABLED", True)
GRAPHQL_ENABLED = getattr(settings, "SNAPADMIN_GRAPHQL_ENABLED", True)

router = DefaultRouter()
router.register(r"tokens", APITokenViewSet, basename="api-token")

urlpatterns = []

if REST_API_ENABLED:
    urlpatterns += [
        # Token management
        path("", include(router.urls)),

        # Model introspection
        path("models/schema/", ModelSchemaView.as_view(), name="model-schema"),

        # Health check
        path("health/", HealthCheckView.as_view(), name="api-health"),

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
    ]

if SWAGGER_ENABLED:
    urlpatterns += [
        # OpenAPI schema & interactive docs
        path("schema/",  SpectacularAPIView.as_view(),      name="api-schema"),
        path("docs/",    SpectacularSwaggerView.as_view(url_name="api-schema"), name="swagger-ui"),
        path("redoc/",   SpectacularRedocView.as_view(url_name="api-schema"),   name="redoc"),
    ]

urlpatterns += [
    path("sw.js", service_worker, name="service-worker"),
]

if GRAPHQL_ENABLED:
    try:
        from graphene_django.views import GraphQLView
        from snapadmin.api.graphql import schema
        urlpatterns += [
            path("graphql/", GraphQLView.as_view(graphiql=True, schema=schema), name="graphql"),
            path("graphql", GraphQLView.as_view(graphiql=True, schema=schema)),
        ]
    except Exception as e:
        # Log error or print for debugging
        print(f"GraphQL Error: {e}")
        pass
