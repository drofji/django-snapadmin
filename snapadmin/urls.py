"""
snapadmin/urls.py

URL configuration for the SnapAdmin REST API.
"""

import structlog
from django.core.exceptions import ImproperlyConfigured
from django.urls import path, include
from django.conf import settings

REST_API_ENABLED = getattr(settings, "SNAPADMIN_REST_API_ENABLED", True)
# Swagger documents the REST API, so it follows it by default: with the REST API
# switched off there is nothing to document, and defaulting to True would make
# SNAPADMIN_REST_API_ENABLED = False insufficient on its own to load this URLconf
# without drf-spectacular installed. An explicit setting still wins either way.
SWAGGER_ENABLED = getattr(settings, "SNAPADMIN_SWAGGER_ENABLED", REST_API_ENABLED)
GRAPHQL_ENABLED = getattr(settings, "SNAPADMIN_GRAPHQL_ENABLED", True)
# Admin-only user management API — off by default (opt-in surface).
USER_API_ENABLED = getattr(settings, "SNAPADMIN_USER_API_ENABLED", False)
# Optional extra path segment prepended to *every* snapadmin route. Projects that
# already own the mount point (e.g. they include snapadmin at the site root, or
# under "/api/" which collides with their own API) can relocate the whole surface
# — REST, Swagger and GraphQL — under one namespace without editing their URLconf,
# by setting e.g. SNAPADMIN_URL_PREFIX = "snapadmin/". Empty (default) is a no-op
# and keeps the historical layout. Route *names* are unchanged, so reverse() and
# {% url %} keep working regardless of the prefix.
URL_PREFIX = getattr(settings, "SNAPADMIN_URL_PREFIX", "")

logger = structlog.get_logger(__name__)


#: Packages each feature needs, named explicitly rather than via an extra: they are
#: still ordinary dependencies of every install, so there is no `[api]`/`[graphql]`
#: extra to point at — telling someone to install one would send them to a pip
#: warning and no packages. Revisit when they move behind extras.
REST_PACKAGES = "djangorestframework drf-spectacular django-filter"
GRAPHQL_PACKAGES = "graphene-django"


def _missing_dependency(
    feature: str, setting: str, packages: str, exc: ImportError
) -> ImproperlyConfigured:
    """Build the error raised when a feature is switched on but its packages are absent.

    A bare ``ImportError`` from inside a URLconf reads as a broken installation. It
    is really a two-part configuration answer — install the packages, or turn the
    feature off — so say both.
    """
    return ImproperlyConfigured(
        f"{feature} is enabled ({setting} = True) but its dependencies are not installed: {exc}. "
        f"Install them with `pip install {packages}`, "
        f"or set {setting} = False to switch the feature off."
    )


def _missing_graphql_dependency(exc: ImportError) -> ImproperlyConfigured:
    return _missing_dependency(
        "GraphQL", "SNAPADMIN_GRAPHQL_ENABLED", GRAPHQL_PACKAGES, exc
    )


urlpatterns = []

if REST_API_ENABLED:
    # Imported here, not at module level: with the feature off, a project needs
    # neither DRF nor these modules, and including snapadmin.urls must not demand
    # them. The same applies to Swagger and GraphQL below.
    try:
        from rest_framework.routers import DefaultRouter

        from snapadmin.api.views import (
            APITokenViewSet,
            DynamicModelViewSet,
            ModelSchemaView,
            SSOProviderView,
        )
        from snapadmin.api.exports import ExportJobViewSet
        from snapadmin.api.health import HealthCheckView
        from snapadmin.api.offline import OfflineModelsView, OfflineModelDataView
        from snapadmin.api.reindex import ESReindexView
    except ImportError as exc:
        raise _missing_dependency(
            "The REST API", "SNAPADMIN_REST_API_ENABLED", REST_PACKAGES, exc
        ) from exc

    router = DefaultRouter()
    router.register(r"tokens", APITokenViewSet, basename="api-token")
    router.register(r"exports", ExportJobViewSet, basename="api-export")

    if USER_API_ENABLED:
        from snapadmin.api.users import SnapUserViewSet

        router.register(r"users", SnapUserViewSet, basename="api-user")

    urlpatterns += [
        # Token management
        path("", include(router.urls)),

        # Model introspection
        path("models/schema/", ModelSchemaView.as_view(), name="model-schema"),
    ]
    if USER_API_ENABLED:
        from snapadmin.api.users import PermissionListView

        urlpatterns += [
            # Assignable permissions (frontend permission pickers)
            path("permissions/", PermissionListView.as_view(), name="permission-list"),
        ]
    urlpatterns += [

        # Health check
        path("health/", HealthCheckView.as_view(), name="api-health"),

        # Admin-only bulk ES reindex (opt-in via SNAPADMIN_REINDEX_API_ENABLED).
        # Registered unconditionally; the view returns 404 while disabled so the
        # gate is togglable at runtime without reloading the URLconf.
        path("es/reindex/", ESReindexView.as_view(), name="es-reindex"),

        # Public SSO provider list (headless login for external frontends)
        path("sso-providers/", SSOProviderView.as_view(), name="sso-providers"),

        # Offline-capable model list (consumed by the admin connectivity layer)
        path("offline-models/", OfflineModelsView.as_view(), name="offline-models"),

        # Recent rows of one offline-capable model (prefetched into IndexedDB by offline.js)
        path(
            "offline-data/<str:app_label>/<str:model_name>/",
            OfflineModelDataView.as_view(),
            name="offline-data",
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

        # No-Celery bulk helpers  ─  count + synchronous streaming export.
        # Registered before the <int:pk> detail route; the int converter never
        # matches "count"/"export" so ordering is not load-bearing, but keeping
        # the collection-level routes together reads more clearly.
        path(
            "models/<str:app_label>/<str:model_name>/count/",
            DynamicModelViewSet.as_view({"get": "count"}),
            name="model-count",
        ),
        path(
            "models/<str:app_label>/<str:model_name>/export/",
            DynamicModelViewSet.as_view({"get": "export"}),
            name="model-export",
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
    try:
        from drf_spectacular.views import (
            SpectacularAPIView,
            SpectacularSwaggerView,
            SpectacularRedocView,
        )
    except ImportError as exc:
        raise _missing_dependency(
            "The OpenAPI schema and docs", "SNAPADMIN_SWAGGER_ENABLED", REST_PACKAGES, exc
        ) from exc

    urlpatterns += [
        # OpenAPI schema & interactive docs
        path("schema/",  SpectacularAPIView.as_view(),      name="api-schema"),
        path("docs/",    SpectacularSwaggerView.as_view(url_name="api-schema"), name="swagger-ui"),
        path("redoc/",   SpectacularRedocView.as_view(url_name="api-schema"),   name="redoc"),
    ]

if GRAPHQL_ENABLED:
    try:
        try:
            # NB: importing this module *builds the schema*, so a failure here is
            # as likely to come from a project's own models (two same-named
            # SnapModels colliding on a graphene type name, say) as from a missing
            # package — only the latter is a configuration answer worth raising.
            from snapadmin.api.graphql import SnapGraphQLView, schema
        except ImportError as exc:
            raise _missing_graphql_dependency(exc) from exc

        # GraphiQL playground: enabled only alongside DEBUG unless overridden —
        # keep the interactive explorer out of production by default.
        GRAPHIQL_ENABLED = getattr(
            settings, "SNAPADMIN_GRAPHIQL_ENABLED", getattr(settings, "DEBUG", False)
        )
        urlpatterns += [
            path("graphql/", SnapGraphQLView.as_view(graphiql=GRAPHIQL_ENABLED, schema=schema), name="graphql"),
            path("graphql", SnapGraphQLView.as_view(graphiql=GRAPHIQL_ENABLED, schema=schema)),
        ]
    except ImproperlyConfigured:
        # The missing-dependency answer above must reach the developer.
        raise
    except Exception as e:
        # Everything else — an unbuildable schema, a view that won't construct —
        # stays a warning. Raising from a URLconf takes down every other route in
        # the project, which is a far worse outcome than a missing endpoint.
        logger.warning("graphql_setup_failed", error=str(e))

# Relocate the whole surface under SNAPADMIN_URL_PREFIX when set. Wrapping the
# assembled patterns in a single include() keeps every route name intact (no
# namespace is introduced) so downstream reverse()/{% url %} calls are unaffected.
if URL_PREFIX:
    urlpatterns = [path(f"{URL_PREFIX.strip('/')}/", include(urlpatterns))]
