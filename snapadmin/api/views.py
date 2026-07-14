"""
snapadmin/api/views.py

SnapAdmin REST API views.
"""

import json

from django.apps import apps
from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils.module_loading import import_string
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from snapadmin.api.authentication import SnapAPIAuthMixin, token_has_permission
from snapadmin.db import route_read
from snapadmin.api.filters import SnapAdminFilterBackend
from snapadmin.models import APIToken, EsStorageMode, SnapModel
from snapadmin.api.serializers import (
    APITokenCreateSerializer,
    APITokenSerializer,
    get_serializer_for_model,
)

# Cache for model field introspection results to avoid repeated _meta.get_fields() calls
_model_field_cache = {}


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
        app_label  = view.kwargs.get("app_label", "")
        model_name = view.kwargs.get("model_name", "")
        action_str = self._action_map.get(view.action, "view")

        token = getattr(request, "auth", None)
        if isinstance(token, APIToken):
            return token_has_permission(
                token, request.user, app_label, model_name, action_str
            )

        # Non-token authentication (session, JWT via
        # SNAPADMIN_API_AUTHENTICATION_CLASSES): plain Django model permissions —
        # the same check a token delegates to, minus the allowed_models scope.
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        return user.has_perm(f"{app_label}.{action_str}_{model_name.lower()}")


class APITokenViewSet(
    SnapAPIAuthMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
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


class DynamicModelViewSet(SnapAPIAuthMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, TokenModelPermission]
    filter_backends = [SnapAdminFilterBackend, SearchFilter, OrderingFilter]

    def _get_model_class(self):
        app_label  = self.kwargs["app_label"]
        model_name = self.kwargs["model_name"]
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return None
        if not SnapModel.is_concrete_subclass(model):
            return None
        return model

    def _es_routing_enabled(self, model_class) -> bool:
        """Whether full-text API queries for this model may be routed to ES.

        Requires all three switches on: the global ``SNAPADMIN_ES_QUERY_ROUTING``
        setting (default True), the model's ``es_query_routing`` attribute
        (default True), and ``ELASTICSEARCH_ENABLED``.
        """
        return (
            getattr(settings, "SNAPADMIN_ES_QUERY_ROUTING", True)
            and getattr(model_class, "es_query_routing", True)
            and getattr(settings, "ELASTICSEARCH_ENABLED", False)
        )

    def _get_search_query(self) -> str:
        return self.request.query_params.get(api_settings.SEARCH_PARAM, "").strip()

    @staticmethod
    def _db_search_fields(model_class) -> tuple[str, ...] | None:
        """Fields DRF's SearchFilter may match `?search=` against on the DB path.

        Derived from the model's Snap fields flagged ``searchable=True`` — the
        same set the admin search box uses. ``None`` (no searchable fields)
        makes SearchFilter a no-op.
        """
        if not hasattr(model_class, "get_admin_fields"):
            return None
        _, _, search_fields, _, _ = model_class.get_admin_fields()
        return tuple(search_fields) or None

    def get_queryset(self):
        model_class = self._get_model_class()
        if model_class is None:
            return []

        search_query = self._get_search_query()
        es_limit = getattr(settings, "SNAPADMIN_ES_SEARCH_LIMIT", 1000)
        storage_mode = getattr(model_class, "es_storage_mode", EsStorageMode.DB_ONLY)

        # Where did the query go? Exposed as the X-Snap-Query-Backend response
        # header so API consumers can verify the routing decision.
        self._query_backend = "database"
        # When es_search already applied the search, DRF's SearchFilter must be
        # skipped — a second DB icontains pass would wrongly narrow the fuzzy,
        # relevance-ranked ES result.
        self.search_fields = None

        if storage_mode == EsStorageMode.ES_ONLY:
            # No DB table exists — ES is the only source. The search term (if
            # any) goes straight into the ES query instead of being ignored.
            qs = model_class.es_search(search_query or None, limit=es_limit)
            self._query_backend = getattr(qs, "_snap_search_backend", "elasticsearch")
        elif (
            storage_mode == EsStorageMode.DUAL
            and search_query
            and self._es_routing_enabled(model_class)
        ):
            # The data is mirrored in ES: run the expensive full-text search
            # there. es_search returns a real DB queryset ordered by ES
            # relevance, so filters and pagination still apply on top. The
            # marker set by es_search reports the backend that actually
            # answered — "database" when ES failed and the DB fallback ran.
            qs = model_class.es_search(search_query, limit=es_limit)
            self._query_backend = getattr(qs, "_snap_search_backend", "elasticsearch")
        else:
            # Plain listings (and DUAL models with routing off) stay on the
            # database: native pagination, no ES round-trip, no row cap.
            qs = model_class.objects.all()
            self.search_fields = self._db_search_fields(model_class)

        # The base manager no longer injects a default order (it would leak into
        # GROUP BY on aggregations), so guarantee a deterministic newest-first
        # order for pagination on any DB-backed queryset — plain listings and the
        # DB fallback returned when an ES search errors out. ES relevance order
        # and EsQuerySet already report as ordered, so this is a no-op there. An
        # explicit Meta.ordering or a client ``?ordering=`` still wins.
        if hasattr(qs, "ordered") and not qs.ordered:
            qs = qs.order_by("-pk")

        # Introspection of related fields is expensive in a tight loop.
        # We cache the field lists per model.
        cache_key = f"{model_class._meta.app_label}.{model_class._meta.model_name}"
        if cache_key not in _model_field_cache:
            fields = model_class._meta.get_fields()
            fk_fields = [
                f.name
                for f in fields
                if hasattr(f, "many_to_one") and f.many_to_one
            ]
            m2m_fields = [
                f.name
                for f in fields
                if hasattr(f, "many_to_many") and f.many_to_many and not f.auto_created
            ]
            _model_field_cache[cache_key] = (fk_fields, m2m_fields)
        else:
            fk_fields, m2m_fields = _model_field_cache[cache_key]

        if fk_fields:
            # Use select_related for ForeignKeys to avoid N+1 queries if the model's
            # __str__ or other properties access related objects during serialization.
            qs = qs.select_related(*fk_fields)
        if m2m_fields:
            # Use prefetch_related for Many-to-Many to avoid N+1 queries
            # when serializing lists of related IDs.
            qs = qs.prefetch_related(*m2m_fields)

        # Route read-only evaluation to the analytics replica when configured
        # (SNAPADMIN_ANALYTICS_DB_ALIAS). Only list/retrieve are routed: the
        # get_object() lookups behind update/partial_update/destroy must stay on
        # the primary so replication lag can never stale or drop a write.
        if getattr(self, "action", None) in ("list", "retrieve", "count", "export") and hasattr(qs, "using"):
            qs = route_read(qs)

        return qs

    def get_serializer_class(self):
        app_label  = self.kwargs.get("app_label", "")
        model_name = self.kwargs.get("model_name", "")
        try:
            return get_serializer_for_model(app_label, model_name)
        except LookupError:
            return None

    def create(self, request, *args, **kwargs):
        model_class = self._get_model_class()
        if model_class is None:
            return Response(
                {"detail": f"Model '{kwargs.get('model_name')}' not found in app '{kwargs.get('app_label')}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return super().create(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        model_class = self._get_model_class()
        if model_class is None:
            return Response(
                {"detail": f"Model '{kwargs.get('model_name')}' not found in app '{kwargs.get('app_label')}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        response = super().list(request, *args, **kwargs)
        if getattr(settings, "SNAPADMIN_QUERY_BACKEND_HEADER", True):
            response["X-Snap-Query-Backend"] = self._query_backend
        return response

    @staticmethod
    def _deletion_allowed(request, instance) -> bool:
        """Consult the deletion-veto extension points before a DELETE.

        Two independent guards, both of which must allow the delete:

        * the object's own ``api_can_delete(request)`` hook (SnapModels default
          to allowing), and
        * a project-wide ``SNAPADMIN_API_DELETE_GUARD`` callable (dotted path or
          callable) receiving ``(request, instance)``.
        """
        hook = getattr(instance, "api_can_delete", None)
        if callable(hook) and not hook(request):
            return False

        guard = getattr(settings, "SNAPADMIN_API_DELETE_GUARD", None)
        if guard:
            guard_fn = import_string(guard) if isinstance(guard, str) else guard
            if not guard_fn(request, instance):
                return False

        return True

    def destroy(self, request, *args, **kwargs):
        model_class = self._get_model_class()
        if model_class is None:
            return Response(
                {"detail": f"Model '{kwargs.get('model_name')}' not found in app '{kwargs.get('app_label')}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        instance = self.get_object()
        if not self._deletion_allowed(request, instance):
            return Response(
                {"detail": "Deletion of this object is not allowed."},
                status=status.HTTP_403_FORBIDDEN,
            )
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _parse_export_limit(request: Request) -> int | None:
        """Optional ``?limit=`` cap for the streaming export.

        Returns a positive int, or ``None`` (no cap) when the param is absent,
        blank, non-numeric, or non-positive — a bad cap must never silently
        truncate an export, so it degrades to "stream everything".
        """
        raw = request.query_params.get("limit")
        if raw is None or raw == "":
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @extend_schema(summary="Count rows matching the current filters")
    def count(self, request, *args, **kwargs):
        """``GET .../count/?<filters>`` — match count for the filtered queryset.

        Honours the same filter, search and permission backends as ``list`` but
        returns only ``{"count": N}`` — a cheap way for a frontend to size a
        result set (or a paginator) without pulling any rows.
        """
        model_class = self._get_model_class()
        if model_class is None:
            return Response(
                {"detail": f"Model '{kwargs.get('model_name')}' not found in app '{kwargs.get('app_label')}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        queryset = self.filter_queryset(self.get_queryset())
        response = Response({"count": queryset.count()})
        if getattr(settings, "SNAPADMIN_QUERY_BACKEND_HEADER", True):
            response["X-Snap-Query-Backend"] = self._query_backend
        return response

    @extend_schema(summary="Stream all matching rows as NDJSON (no pagination)")
    def export(self, request, *args, **kwargs):
        """``GET .../export/?<filters>[&limit=N]`` — stream every matching row.

        The synchronous, no-Celery counterpart to the async export endpoint:
        the full filtered queryset is streamed as newline-delimited JSON
        (``application/x-ndjson``), one serialized object per line, without
        pagination. Pass ``?limit=N`` to cap the number of rows. Rows are
        pulled lazily (``iterator()`` where available) so arbitrarily large
        tables never materialise in memory.
        """
        model_class = self._get_model_class()
        if model_class is None:
            return Response(
                {"detail": f"Model '{kwargs.get('model_name')}' not found in app '{kwargs.get('app_label')}'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        queryset = self.filter_queryset(self.get_queryset())
        limit = self._parse_export_limit(request)
        if limit is not None:
            queryset = queryset[:limit]
        serializer_class = self.get_serializer_class()

        chunk_size = max(1, int(getattr(settings, "SNAPADMIN_EXPORT_CHUNK_SIZE", 1000)))

        def stream():
            # A capped queryset is already sliced (can't call iterator() on it);
            # an uncapped one streams in chunks to keep memory flat. chunk_size
            # is mandatory once the queryset carries prefetch_related (m2m/reverse
            # relations), so it is always passed on the DB path.
            if limit is None and hasattr(queryset, "iterator"):
                source = queryset.iterator(chunk_size=chunk_size)
            else:
                source = iter(queryset)
            for obj in source:
                data = serializer_class(obj, context={"request": request}).data
                yield json.dumps(data, default=str) + "\n"

        response = StreamingHttpResponse(stream(), content_type="application/x-ndjson")
        filename = f"{model_class._meta.app_label}_{model_class._meta.model_name}.ndjson"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        if getattr(settings, "SNAPADMIN_QUERY_BACKEND_HEADER", True):
            response["X-Snap-Query-Backend"] = self._query_backend
        return response


class ModelSchemaView(SnapAPIAuthMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(summary="List all available model API endpoints")
    def get(self, request: Request) -> Response:
        token = request.auth
        results = []

        for model in apps.get_models():
            if not SnapModel.is_concrete_subclass(model):
                continue

            app_label  = model._meta.app_label
            model_name = model.__name__

            if isinstance(token, APIToken) and not token.can_access_model(app_label, model_name):
                continue

            excluded = set(getattr(model, "api_exclude_fields", []) or [])
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
                    if hasattr(f, "name") and f.name not in excluded
                ],
            })

        return Response({"models": results, "count": len(results)})


class SSOProviderView(APIView):
    """Public list of configured SSO providers for headless frontends.

    Exposes the same ``SNAPADMIN_SSO_PROVIDERS`` the admin login page renders so
    a custom frontend can show identical corporate login buttons. Read-only and
    unauthenticated by design — the payload is just labels + public login URLs
    (no secrets), and the caller is not logged in yet.
    """

    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="List configured SSO login providers")
    def get(self, request: Request) -> Response:
        from snapadmin.sso import get_sso_providers

        providers = get_sso_providers()
        for p in providers:
            # Absolutise relative login URLs so a cross-origin frontend can use
            # them directly; leave absolute provider URLs untouched. A leading
            # "//" is protocol-relative, not site-relative — build_absolute_uri
            # would resolve it to an external origin, so it's excluded here too
            # (defense in depth: get_sso_providers() already filters these out).
            if p["url"].startswith("/") and not p["url"].startswith("//"):
                p["url"] = request.build_absolute_uri(p["url"])
        return Response({"providers": providers, "count": len(providers)})
