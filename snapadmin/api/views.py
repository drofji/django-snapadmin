"""
snapadmin/api/views.py

SnapAdmin REST API views.
"""

import json
from dataclasses import dataclass

from django.apps import apps
from django.conf import settings
from django.http import StreamingHttpResponse
from django.urls import reverse
from django.utils.module_loading import import_string
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.filters import BaseFilterBackend, OrderingFilter
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from snapadmin.api.authentication import SnapAPIAuthMixin, token_has_permission
from snapadmin.conf import get_setting
from snapadmin.db import route_read
from snapadmin.api.filters import get_api_filter_backends
from snapadmin.logging_config import get_logger
from snapadmin.masking import get_masked_fields, user_can_access_field, user_can_view_pii
from snapadmin.models import APIToken, EsStorageMode
from snapadmin.pagination import SnapDynamicPagination
from snapadmin.registry import get_model_meta, is_registered
from snapadmin.api.serializers import (
    APITokenCreateSerializer,
    APITokenSerializer,
    get_serializer_for_model,
)

logger = get_logger(__name__)

# Cache for model field introspection results to avoid repeated _meta.get_fields() calls
_model_field_cache = {}


class IsTokenOwnerOrAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj: APIToken):
        return obj.user == request.user or request.user.is_superuser


class SnapAnonRateThrottle(AnonRateThrottle):
    """Anonymous-caller rate limit, read straight from ``SNAPADMIN_THROTTLE_ANON``.

    Overriding ``get_rate()`` bypasses DRF's ``DEFAULT_THROTTLE_RATES`` settings
    dict entirely, so this applies even in a host project that sets no
    ``REST_FRAMEWORK`` throttle config of its own. A falsy setting value (e.g.
    ``None``) disables throttling for this scope.
    """

    scope = "snapadmin_anon"

    def get_rate(self) -> str | None:
        return get_setting("SNAPADMIN_THROTTLE_ANON", "60/min")


class SnapUserRateThrottle(UserRateThrottle):
    """Authenticated-caller rate limit, read from ``SNAPADMIN_THROTTLE_USER``.

    See :class:`SnapAnonRateThrottle` — same independence from DRF's
    ``DEFAULT_THROTTLE_RATES``.
    """

    scope = "snapadmin_user"

    def get_rate(self) -> str | None:
        return get_setting("SNAPADMIN_THROTTLE_USER", "600/min")


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
        token = getattr(request, "auth", None)

        if view.action == "dispatch_action":
            # A @snap_action's own permission — view/change_<model> derived
            # from its declared methods, or an explicit override — is checked
            # precisely, per action, inside dispatch_action() itself
            # (DynamicModelViewSet._snap_action_permission_granted). Resolving
            # the same verb here too would duplicate that logic and could
            # drift out of sync with what the action actually declares; this
            # outer gate only confirms a token's allowed_models scope covers
            # the model at all, same as every other action.
            return token.can_access_model(app_label, model_name) if isinstance(token, APIToken) else True

        action_str = self._action_map.get(view.action, "view")
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
    """Self-service CRUD for the caller's own API tokens (``/api/tokens/``).

    List, create, delete, rotate and deactivate — a token's other fields
    cannot be edited (no PUT/PATCH). The plaintext key is returned **once**,
    in the create and rotate responses; only its hash is stored. A regular
    user manages their own tokens (``token.user == request.user``) without
    needing to be a superuser — scoped by :meth:`get_queryset` and enforced
    again by :class:`IsTokenOwnerOrAdmin` on every object lookup, not by a
    separate permission class per action. A superuser sees and manages every
    token.
    """

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

    @extend_schema(summary="Rotate a token's secret, keeping the row")
    @action(detail=True, methods=["post"])
    def rotate(self, request, *args, **kwargs):
        """Mint a new secret for this token in place.

        The row, its id, scopes and history all survive; only the key
        material changes. The old key stops authenticating immediately. See
        :meth:`snapadmin.models.APIToken.rotate` — this is the supported
        response to a leaked key (``SECURITY.md``).
        """
        token = self.get_object()
        token.rotate(request=request)
        return Response(APITokenSerializer(token).data)

    @extend_schema(summary="Deactivate a token without deleting it")
    @action(detail=True, methods=["post"])
    def deactivate(self, request, *args, **kwargs):
        """Flip ``is_active`` off — the recommended revocation path.

        Deactivating keeps the row (id, scopes, history) intact and can be
        reversed by a superuser through the admin; ``destroy`` remains for
        administrators who want the row gone outright.
        """
        token = self.get_object()
        token.is_active = False
        token.save(update_fields=["is_active"])
        return Response(APITokenSerializer(token).data)


# HTTP-method sets the per-model policy resolves to.
_SAFE_HTTP_METHOD_NAMES = ["get", "head", "options"]
_FULL_HTTP_METHOD_NAMES = ["get", "post", "put", "patch", "delete", "head", "options"]


class _PerModelHttpMethods:
    """Descriptor for ``DynamicModelViewSet.http_method_names``.

    Class access (``DynamicModelViewSet.http_method_names``) returns full CRUD — this
    is what drf-spectacular reads (``callback.cls.http_method_names``) to enumerate a
    path's operations, so the generated schema still advertises every verb. Instance
    access (``self.http_method_names`` inside ``dispatch``/``options``) resolves the
    per-request target model's ``api_read_only`` / ``api_http_method_names`` policy,
    so a disallowed verb is rejected with ``405``.
    """

    def __get__(self, instance, owner=None) -> list[str]:
        if instance is None:
            return list(_FULL_HTTP_METHOD_NAMES)
        return instance._resolve_http_method_names()


class SnapActionError(Exception):
    """Raised by a ``@snap_action`` function to answer with an error response.

    ``dispatch_action`` turns this into ``Response({"detail": message},
    status=status)`` — the same ``{"detail": ...}`` envelope every other error
    path in this module already uses. ``status`` defaults to ``400``.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


#: Methods that imply a read-only default permission (``view_<model>``); any
#: other method set implies a mutating default (``change_<model>``). See
#: :func:`snap_action`.
_READ_ONLY_METHODS = frozenset({"get", "head", "options"})


@dataclass(frozen=True)
class SnapActionSpec:
    """What ``@snap_action`` recorded about one decorated model method."""

    name: str
    detail: bool
    methods: frozenset[str]
    permission: str | None
    func: object  # the undecorated function: func(instance_or_model_class, request)


def snap_action(
    *,
    detail: bool = True,
    methods: "list[str] | tuple[str, ...]" = ("post",),
    permission: str | None = None,
):
    """Turn a model method into a user-defined REST action (#RFC1h).

    ``@snap_property`` exposes a computed, read-only column; this exposes a
    **callable** endpoint — ``POST /api/models/<app_label>/<Model>/<pk>/
    <name>/`` for a ``detail=True`` action (the default), or ``POST
    /api/models/<app_label>/<Model>/<name>/`` for ``detail=False``::

        class Order(SnapModel):
            ...
            @snap_action()
            def recalculate_total(self, request):
                self.total = sum(item.line_total for item in self.items.all())
                self.save(update_fields=["total"])
                return {"total": str(self.total)}

    The function receives the model **instance** (``detail=True``) or the
    model **class** (``detail=False``) as its first argument, then
    ``request`` — exactly like an ordinary method receives ``self``, so it
    remains an ordinary callable outside the API too. Return a plain
    JSON-serialisable ``dict`` (wrapped as ``Response(result, status=200)``)
    or a :class:`~rest_framework.response.Response` built directly (a full
    escape hatch for a custom status/headers). Raise
    :class:`SnapActionError` to answer with an error.

    **Discovered the same way ``@snap_property`` is** — a marker stored on the
    function object itself, read back off the model's *own* ``__dict__``
    (mirrors the ``get_admin_fields()`` enumeration precedent: no inherited-
    action lookup, the same documented scoping limit). Works unmodified on
    both doors (a :class:`~snapadmin.models.SnapModel` subclass or a
    ``@snap_model``-decorated plain model) since it touches no model-class
    machinery at all — just a method carrying one extra attribute.

    **Permission, not a DRF permission class.** Two independent gates stand
    between a request and the function body:

    1. **The model's own CRUD policy — enforced before ``dispatch_action`` is
       even called, for free.** Every action URL is wired to every HTTP verb
       (``.as_view({"get": "dispatch_action", "post": "dispatch_action",
       ...})``), but DRF's own ``dispatch()`` rejects a verb outside
       ``self.http_method_names`` with ``405`` before selecting a handler —
       and ``http_method_names`` on this viewset already resolves the target
       model's ``api_read_only``/``api_http_method_names`` policy (the exact
       set a regular ``PATCH``/``DELETE`` is measured against). A ``POST``
       action therefore never reaches ``dispatch_action`` at all on a model
       configured ``api_read_only=True`` — inherited structurally, not
       re-checked a second time.
    2. **A Django permission**, checked inside ``dispatch_action``:
       ``permission`` if given (``"app_label.codename"``), otherwise derived
       from ``methods`` — ``view_<model>`` when every method is safe
       (GET/HEAD/OPTIONS), ``change_<model>`` otherwise. Checked with the
       caller's ``has_perm()`` (superusers pass for free) plus the
       authenticating ``APIToken``'s ``allowed_models`` scope, when the
       caller used a token.

    :param detail: ``True`` (default) for a per-object action (needs a
        ``pk``); ``False`` for a collection-level action. One decorator call
        expresses one scope — declare a second method for the other, exactly
        like DRF's own ``@action(detail=...)``.
    :param methods: Lowercase HTTP verbs this action accepts. Defaults to
        ``("post",)``, the shape almost every action needs (a mutation
        triggered by the client).
    :param permission: An explicit ``"app_label.codename"`` override for the
        default permission derived from ``methods``.
    """
    resolved_methods = frozenset(m.lower() for m in methods)
    if not resolved_methods:
        raise ValueError("snap_action() needs at least one HTTP method.")

    def decorator(func):
        func.__snapadmin_action__ = SnapActionSpec(
            name=func.__name__,
            detail=detail,
            methods=resolved_methods,
            permission=permission,
            func=func,
        )
        return func

    return decorator


def get_snap_action(model_class: type, name: str) -> SnapActionSpec | None:
    """The :class:`SnapActionSpec` ``model_class`` declared under ``name``, or ``None``.

    Looks only at ``model_class``'s own ``__dict__`` — an action inherited
    from a parent class is not discovered, mirroring ``@snap_property``'s
    same, already-documented scoping limit.
    """
    func = model_class.__dict__.get(name)
    return getattr(func, "__snapadmin_action__", None)


def iter_snap_actions(model_class: type) -> list[SnapActionSpec]:
    """Every ``@snap_action`` declared directly on ``model_class``, in definition order."""
    return [
        spec
        for attr in model_class.__dict__.values()
        if (spec := getattr(attr, "__snapadmin_action__", None)) is not None
    ]


class DynamicModelViewSet(SnapAPIAuthMixin, viewsets.ModelViewSet):
    """The generated REST endpoint every ``SnapModel`` is served through.

    One viewset backs every model, resolving the target from the URL::

        GET    /api/models/<app_label>/<model_name>/
        POST   /api/models/<app_label>/<model_name>/
        GET    /api/models/<app_label>/<model_name>/<pk>/
        PATCH  /api/models/<app_label>/<model_name>/<pk>/
        DELETE /api/models/<app_label>/<model_name>/<pk>/

    Serializer, filters, ordering and search are built from the model's field
    declarations; Django model permissions are enforced per action. The model's
    own ``api_read_only`` / ``api_http_method_names`` narrow the allowed verbs,
    ``api_write_fields`` gates what a client may assign, and ``api_exclude_fields``
    keeps a column out of every response.
    """

    permission_classes = [permissions.IsAuthenticated, TokenModelPermission]
    pagination_class = SnapDynamicPagination
    throttle_classes = [SnapAnonRateThrottle, SnapUserRateThrottle]

    @property
    def filter_backends(self) -> list[type[BaseFilterBackend]]:
        """Resolved from ``SNAPADMIN_API_FILTER_BACKEND`` on each access so a
        project can swap the backend chain via settings (default: the built-in
        SnapAdminFilterBackend + DRF Search/Ordering). A property — not a class
        attribute — so ``override_settings`` and deploy-time config both apply,
        while drf-spectacular still reads ``self.filter_backends`` for the schema.
        """
        return get_api_filter_backends()

    # Per-model HTTP-method allowlist. ``http_method_names`` is a descriptor rather
    # than a plain class attribute so it can resolve the *target model's* policy on
    # each request while still answering full CRUD at the class level — drf-spectacular
    # introspects ``callback.cls.http_method_names`` (class access) to enumerate the
    # schema's operations, and a bare @property would hand it the descriptor object.
    http_method_names = _PerModelHttpMethods()

    def _resolve_http_method_names(self) -> list[str]:
        """The HTTP verbs allowed for this request's target model.

        ``api_http_method_names`` (an explicit lowercase allowlist, HEAD/OPTIONS
        always added — wins when set) or ``api_read_only`` (``True`` -> only
        GET/HEAD/OPTIONS); otherwise full CRUD. A disallowed verb is rejected with
        ``405`` in dispatch before any handler runs (so a read-only model never gets
        a blank-row insert), and is pruned from the ``OPTIONS`` ``Allow`` header.

        An **unresolvable model** — kwargs naming an unknown app, an unknown
        model, or a model that is not ``@snap_model``-registered — denies
        every verb (the empty list), never full CRUD: a permissive fallback
        here is exactly what let a disallowed verb (e.g. ``PATCH``) reach a
        handler for a model that does not exist. In practice a live request
        never reaches this fallback for that case: :meth:`initial` already
        raises ``404`` before the verb check is consulted, so this is
        defence in depth, not the live guard.

        **Missing kwargs entirely** (no ``app_label``/``model_name`` at
        all — e.g. an instance built for introspection with no URL match)
        is a different case and keeps the historical full-CRUD default.
        """
        kwargs = getattr(self, "kwargs", None) or {}
        if "app_label" not in kwargs or "model_name" not in kwargs:
            return list(_FULL_HTTP_METHOD_NAMES)
        model = self._get_model_class()
        if model is None:
            return []
        explicit = get_model_meta(model, "api_http_method_names", None)
        if explicit is not None:
            allowed = sorted({name.lower() for name in explicit} | {"head", "options"})
        elif get_model_meta(model, "api_read_only", False):
            allowed = list(_SAFE_HTTP_METHOD_NAMES)
        else:
            allowed = list(_FULL_HTTP_METHOD_NAMES)

        # fetch_by (#FETCH2a) is a POST only because a large explicit key set
        # doesn't fit in a URL — it never writes anything, so it inherits
        # GET's availability rather than being gated by POST's. Without this,
        # a read-only or GET-only model (the read-heavy reference/lookup data
        # fetch-by exists for) could never use it at all. self.action is set
        # by DRF's ViewSetMixin.initialize_request() before this is consulted.
        if getattr(self, "action", None) == "fetch_by" and "get" in allowed and "post" not in allowed:
            allowed = sorted(set(allowed) | {"post"})
        return allowed

    def _get_model_class(self):
        app_label  = self.kwargs["app_label"]
        model_name = self.kwargs["model_name"]
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return None
        if not is_registered(model):
            return None
        return model

    def _es_routing_enabled(self, model_class) -> bool:
        """Whether full-text API queries for this model may be routed to ES.

        Requires all three switches on: the global ``SNAPADMIN_ES_QUERY_ROUTING``
        setting (default True), the model's ``es_query_routing`` attribute
        (default True), and ``ELASTICSEARCH_ENABLED``.
        """
        return (
            get_setting("SNAPADMIN_ES_QUERY_ROUTING", True)
            and get_model_meta(model_class, "es_query_routing", True)
            and getattr(settings, "ELASTICSEARCH_ENABLED", False)
        )

    def initial(self, request, *args, **kwargs):
        """Resolve the target model once, before any handler runs.

        ``retrieve``, ``update`` and ``partial_update`` are provided by DRF
        without an explicit override, so before this guard existed only the
        five actions that called :meth:`_get_model_class` themselves
        (``create``, ``list``, ``destroy``, ``count``, ``export``) answered
        an unknown or unregistered model with a clean, consistent 404 — the
        other three fell through to ``get_object()`` filtering an empty
        queryset. Resolving here means every current action, and anything
        ``@snap_action`` adds later, inherits the same 404 from one place
        instead of six.

        Runs *after* ``super().initial()`` — authentication and permission
        checks — so an anonymous or unauthorized caller always gets
        401/403 regardless of whether the named model exists. Checking model
        existence first would let the 404-vs-401 difference tell an
        unauthenticated prober which models are registered without needing
        any credentials at all; that ordering is asserted by a dedicated
        test, not just assumed.
        """
        super().initial(request, *args, **kwargs)
        if self._get_model_class() is None:
            app_label = self.kwargs.get("app_label", "")
            model_name = self.kwargs.get("model_name", "")
            raise NotFound(f"Model '{model_name}' not found in app '{app_label}'.")

    def _snap_action_permission_granted(self, model_class, spec: SnapActionSpec) -> bool:
        """Whether the current request may call this ``@snap_action`` (#RFC1h answer a).

        ``spec.permission`` if given, else derived from ``spec.methods``:
        ``view_<model>`` when every declared method is safe
        (GET/HEAD/OPTIONS), ``change_<model>`` otherwise. ``has_perm()``
        already grants a superuser everything, so no separate bypass is
        needed. **No token-scope re-check here**: for ``dispatch_action``,
        :meth:`TokenModelPermission.has_permission` already confirms an
        ``APIToken`` caller's ``allowed_models`` scope covers the model
        before this method is ever reached — duplicating it here would be
        unreachable, same reasoning as ``dispatch_action`` not re-checking
        ``api_read_only``.
        """
        app_label = model_class._meta.app_label
        model_name = model_class._meta.model_name
        if spec.permission:
            codename = spec.permission
        else:
            verb = "view" if spec.methods <= _READ_ONLY_METHODS else "change"
            codename = f"{app_label}.{verb}_{model_name}"
        return bool(self.request.user.has_perm(codename))

    @extend_schema(
        summary="Dispatch a user-defined @snap_action",
        description=(
            "Generic dynamic dispatcher for every @snap_action declared on any "
            "registered model — the concrete action names, scopes and methods "
            "are model-specific and discovered at call time, not statically "
            "enumerable here. See GET /api/models/schema/ for the per-model "
            "action list."
        ),
    )
    def dispatch_action(self, request, *args, **kwargs):
        """Route to a ``@snap_action``-decorated model method.

        Backs both action URL patterns — ``.../<pk>/<action_name>/`` (detail)
        and ``.../<action_name>/`` (list) — for every HTTP verb; the actual
        verb/scope match against the action's own declaration happens here,
        not in the URLconf, since the set of valid action names is dynamic.
        """
        model_class = self._get_model_class()  # never None — initial() already 404s
        action_name = self.kwargs.get("action_name", "")
        spec = get_snap_action(model_class, action_name)
        if spec is None:
            raise NotFound(f"Action '{action_name}' not found on model '{model_class.__name__}'.")

        if spec.detail != ("pk" in self.kwargs):
            raise NotFound(
                f"Action '{action_name}' is {'detail' if spec.detail else 'list'}-only."
            )

        method = request.method.lower()
        if method not in spec.methods:
            return Response(
                {"detail": f"Action '{action_name}' does not accept {request.method}."},
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        # No separate api_read_only/api_http_method_names re-check here: the
        # model's CRUD policy is already enforced before dispatch_action is
        # ever called, for free — DRF's own dispatch() rejects a verb outside
        # self.http_method_names (the _PerModelHttpMethods descriptor, the
        # exact same _resolve_http_method_names() this method would otherwise
        # duplicate) with 405 before selecting a handler. A model configured
        # api_read_only=True never reaches this line for a write verb; adding
        # a second copy of that check here would be unreachable dead code,
        # not defence in depth.
        if not self._snap_action_permission_granted(model_class, spec):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        subject = self.get_object() if spec.detail else model_class
        try:
            result = spec.func(subject, request)
        except SnapActionError as exc:
            return Response({"detail": exc.message}, status=exc.status)
        if isinstance(result, Response):
            return result
        return Response(result, status=status.HTTP_200_OK)

    def _get_search_query(self) -> str:
        return self.request.query_params.get(api_settings.SEARCH_PARAM, "").strip()

    @staticmethod
    def _db_search_fields(model_class) -> tuple[str, ...] | None:
        """Fields DRF's SearchFilter may match `?search=` against on the DB path.

        Derived from the model's Snap fields flagged ``searchable=True`` — the
        same set the admin search box uses. A plain model registered with
        ``@snap_model`` has no Snap fields to derive them from, so it names them
        directly via the decorator's ``search_fields``; that declaration also
        wins on a ``SnapModel`` subclass, as every decorator keyword does.
        ``None`` (no searchable fields) makes SearchFilter a no-op.
        """
        declared = get_model_meta(model_class, "search_fields", None)
        if declared is not None:
            return tuple(declared) or None
        if not hasattr(model_class, "get_admin_fields"):
            return None
        search_fields = model_class.get_admin_fields().search_fields
        return tuple(search_fields) or None

    def _masked_fields_for_request(self, model_class) -> set[str]:
        """Field names the current caller must not filter/order/search by.

        The union of two independent reasons a field must not be usable as a
        query oracle: masking (empty for a PII-privileged caller — display is
        the only thing masking gates for them) and #FUT3b's
        ``api_field_permissions`` read guard (unaffected by PII privilege — a
        separate permission axis, checked per field regardless of
        ``view_raw_pii``). Either way this is the same field set the
        serializer excludes/stars out, so ``?field=``/``?ordering=field``/
        ``?search=`` can't be used to recover a value the response body never
        reveals.
        """
        user = self.request.user
        fields = set() if user_can_view_pii(user) else set(
            get_masked_fields(model_class._meta.app_label, model_class._meta.model_name)
        )
        permissions = get_model_meta(model_class, "api_field_permissions", {}) or {}
        fields |= {
            field
            for field, rule in permissions.items()
            if isinstance(rule, dict) and rule.get("read")
            and not user_can_access_field(user, model_class, field, write=False)
        }
        return fields

    def get_queryset(self):
        model_class = self._get_model_class()
        if model_class is None:
            return []

        search_query = self._get_search_query()
        es_limit = get_setting("SNAPADMIN_ES_SEARCH_LIMIT", 1000)
        storage_mode = get_model_meta(model_class, "es_storage_mode", EsStorageMode.DB_ONLY)

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

        masked_fields = self._masked_fields_for_request(model_class)
        if masked_fields:
            if self.search_fields:
                self.search_fields = tuple(f for f in self.search_fields if f not in masked_fields) or None
            # A masked field must not be a valid `?ordering=` term either, or a
            # caller could infer its raw value from the sort order. Only
            # overridden when there's actually something to exclude — leave
            # DRF's own default (every serializer field) alone otherwise, so
            # ordering by e.g. a many-to-many or method field is unaffected.
            self.ordering_fields = [
                name for name, _label in OrderingFilter().get_default_valid_fields(
                    qs, self, {"request": self.request}
                )
                if name not in masked_fields
            ]

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

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if get_setting("SNAPADMIN_QUERY_BACKEND_HEADER", True):
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

        guard = get_setting("SNAPADMIN_API_DELETE_GUARD", None)
        if guard:
            guard_fn = import_string(guard) if isinstance(guard, str) else guard
            if not guard_fn(request, instance):
                return False

        return True

    def destroy(self, request, *args, **kwargs):
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
        blank, or non-numeric — a garbled cap must never silently truncate an
        export, so it degrades to "stream everything".

        An explicit **non-positive** value (``?limit=0`` or a negative number)
        is a different case: it isn't garbled input, it's a caller who typed a
        real number that just doesn't make sense as a row cap — almost always
        a mistake. Silently degrading that to "stream everything" would be the
        most expensive possible response to what looks like a typo, so it
        raises :class:`ValueError` instead; ``export()`` turns that into a
        ``400 Bad Request``.

        A valid, positive value is clamped down to
        ``SNAPADMIN_EXPORT_LIMIT_MAX`` when that setting is configured
        (> 0), so an explicit ``?limit=`` can never request more rows than
        the configured hard maximum — the caller gets the clamped count
        rather than an error.
        """
        raw = request.query_params.get("limit")
        if raw is None or raw == "":
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            raise ValueError(f"?limit= must be a positive integer, got {raw!r}.")
        limit_max = int(get_setting("SNAPADMIN_EXPORT_LIMIT_MAX", 0) or 0)
        if limit_max > 0:
            value = min(value, limit_max)
        return value

    @extend_schema(summary="Count rows matching the current filters")
    def count(self, request, *args, **kwargs):
        """``GET .../count/?<filters>`` — match count for the filtered queryset.

        Honours the same filter, search and permission backends as ``list`` but
        returns only ``{"count": N}`` — a cheap way for a frontend to size a
        result set (or a paginator) without pulling any rows.
        """
        queryset = self.filter_queryset(self.get_queryset())
        response = Response({"count": queryset.count()})
        if get_setting("SNAPADMIN_QUERY_BACKEND_HEADER", True):
            response["X-Snap-Query-Backend"] = self._query_backend
        return response

    @extend_schema(summary="Stream all matching rows as NDJSON (no pagination)")
    def export(self, request, *args, **kwargs):
        """``GET .../export/?<filters>[&limit=N]`` — stream every matching row.

        The synchronous, no-Celery counterpart to the async export endpoint:
        the full filtered queryset is streamed as newline-delimited JSON
        (``application/x-ndjson``), one serialized object per line, without
        pagination. Pass ``?limit=N`` to cap the number of rows (clamped to
        ``SNAPADMIN_EXPORT_LIMIT_MAX`` when configured; rejected with ``400``
        if non-positive — see ``_parse_export_limit``). Rows are pulled
        lazily (``iterator()`` where available) so arbitrarily large tables
        never materialise in memory.

        When no valid ``?limit=`` is passed and ``SNAPADMIN_EXPORT_MAX_ROWS``
        is configured, the filtered match count is checked against that
        ceiling *before* streaming starts; exceeding it responds ``413``
        instead of opening an unfinishable stream, pointing the caller at the
        async export endpoint (``POST /api/exports/``) instead.
        """
        model_class = self._get_model_class()
        queryset = self.filter_queryset(self.get_queryset())
        try:
            limit = self._parse_export_limit(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if limit is not None:
            queryset = queryset[:limit]
        else:
            max_rows = int(get_setting("SNAPADMIN_EXPORT_MAX_ROWS", 0) or 0)
            if max_rows > 0:
                match_count = queryset.count()
                if match_count > max_rows:
                    logger.warning(
                        "export_row_ceiling_exceeded",
                        app_label=model_class._meta.app_label,
                        model_name=model_class._meta.model_name,
                        match_count=match_count,
                        max_rows=max_rows,
                    )
                    async_export_url = reverse("api-export-list")
                    return Response(
                        {
                            "detail": (
                                f"{match_count} rows match the current filters, exceeding the "
                                f"SNAPADMIN_EXPORT_MAX_ROWS limit of {max_rows}. Narrow the "
                                "filters, pass an explicit ?limit=N, or use the async export "
                                f"endpoint (POST {async_export_url}) for large result sets."
                            ),
                            "count": match_count,
                            "max_rows": max_rows,
                            "async_export_endpoint": async_export_url,
                        },
                        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
        serializer_class = self.get_serializer_class()

        chunk_size = max(1, int(get_setting("SNAPADMIN_EXPORT_CHUNK_SIZE", 1000)))

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
        if get_setting("SNAPADMIN_QUERY_BACKEND_HEADER", True):
            response["X-Snap-Query-Backend"] = self._query_backend
        return response

    @extend_schema(summary="Fetch an explicit set of rows by a unique/indexed field (POST body)")
    def fetch_by(self, request, *args, **kwargs):
        """``POST .../fetch-by/`` — stream every row whose ``field`` is in ``values``.

        Body: ``{"field": "sku", "values": [...]}``. A **POST that reads** is
        justified in this one sentence: the key set does not fit in a URL,
        and there is no other way to express "fetch exactly these records"
        for a large explicit set the way ``export``'s ``?filters=`` expresses
        a *condition* — encoding a big value list as a query string risks the
        server's own URL-length limit before it risks anything else.

        ``field`` must be ``unique=True`` or ``db_index=True`` on the target
        model — anything else is a ``400`` naming the constraint, closing the
        unindexed-scan foot-gun a free-form field name would otherwise open.
        ``values`` is capped at ``SNAPADMIN_FETCH_BY_MAX_VALUES`` (default
        ``10000``); over the cap is a ``400``, **never a silent truncation**
        — the cap exists before this route does, because an unbounded
        ``values`` list is a denial-of-service vector. Same NDJSON streaming,
        permissions, masking and ``api_read_only``/field rules as ``list``/
        ``export`` — a masked field can't be used as the lookup key either,
        the same oracle-prevention rule ``list``'s ``?ordering=``/``?search=``
        already apply. Not supported for ``ES_ONLY`` models, which have no DB
        column to index in the first place.
        """
        model_class = self._get_model_class()

        if get_model_meta(model_class, "es_storage_mode", EsStorageMode.DB_ONLY) == EsStorageMode.ES_ONLY:
            return Response(
                {"detail": f"{model_class._meta.label} is ES_ONLY — fetch-by needs a DB column to index."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        field_name = request.data.get("field")
        field = next((f for f in model_class._meta.fields if f.name == field_name), None)
        if field is None:
            return Response(
                {"detail": f"{model_class._meta.label} has no field {field_name!r}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not (field.unique or field.db_index):
            return Response(
                {"detail": f"{field_name!r} is not unique=True or db_index=True on "
                           f"{model_class._meta.label} — fetch-by refuses an unindexed scan."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if field_name in self._masked_fields_for_request(model_class):
            return Response(
                {"detail": f"{field_name!r} is a masked field and cannot be used as a fetch-by key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        values = request.data.get("values")
        if not isinstance(values, list) or not values:
            return Response(
                {"detail": "\"values\" must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        max_values = int(get_setting("SNAPADMIN_FETCH_BY_MAX_VALUES", 10000) or 0)
        if max_values > 0 and len(values) > max_values:
            return Response(
                {"detail": f"{len(values)} values exceeds SNAPADMIN_FETCH_BY_MAX_VALUES "
                           f"({max_values})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = self.filter_queryset(self.get_queryset()).filter(**{f"{field_name}__in": values})
        serializer_class = self.get_serializer_class()
        chunk_size = max(1, int(get_setting("SNAPADMIN_EXPORT_CHUNK_SIZE", 1000)))

        def stream():
            source = (
                queryset.iterator(chunk_size=chunk_size)
                if hasattr(queryset, "iterator") else iter(queryset)
            )
            for obj in source:
                data = serializer_class(obj, context={"request": request}).data
                yield json.dumps(data, default=str) + "\n"

        response = StreamingHttpResponse(stream(), content_type="application/x-ndjson")
        filename = f"{model_class._meta.app_label}_{model_class._meta.model_name}_fetch.ndjson"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        if get_setting("SNAPADMIN_QUERY_BACKEND_HEADER", True):
            response["X-Snap-Query-Backend"] = self._query_backend
        return response


class ModelSchemaView(SnapAPIAuthMixin, APIView):
    """Introspection endpoint listing every model the API exposes.

    ``GET /api/models/schema/`` returns each ``SnapModel``'s endpoint URL and its
    fields with types and flags — enough for a client to build a form or a table
    without hardcoding the model. Fields named in ``api_exclude_fields`` are
    omitted here too.

    Also the discovery surface for ``@snap_action`` (#RFC1h answer e): each
    model's entry carries an ``"actions"`` list (name, scope, methods, URL) —
    the concrete action set is dynamic per model, which drf-spectacular's
    static schema cannot enumerate for the generic ``dispatch_action``
    operation it documents instead. This is the same place a client already
    discovers a model's fields, so there is exactly one discovery mechanism,
    not two.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(summary="List all available model API endpoints")
    def get(self, request: Request) -> Response:
        token = request.auth
        results = []

        for model in apps.get_models():
            if not is_registered(model):
                continue

            app_label  = model._meta.app_label
            model_name = model.__name__

            if isinstance(token, APIToken) and not token.can_access_model(app_label, model_name):
                continue

            excluded = set(get_model_meta(model, "api_exclude_fields", []) or [])
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
                "actions": [
                    {
                        "name": spec.name,
                        "detail": spec.detail,
                        "methods": sorted(spec.methods),
                        "url": request.build_absolute_uri(
                            f"/api/models/{app_label}/{model_name}/{{pk}}/{spec.name}/"
                            if spec.detail
                            else f"/api/models/{app_label}/{model_name}/{spec.name}/"
                        ),
                    }
                    for spec in iter_snap_actions(model)
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
