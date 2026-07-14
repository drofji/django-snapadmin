
import graphene
from graphene_django import DjangoObjectType
from graphene_django.views import GraphQLView
from django.apps import apps
from django.conf import settings
from django.db.models import Model, QuerySet
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from graphql import GraphQLError, GraphQLResolveInfo

from snapadmin.logging_config import get_logger
from snapadmin.masking import get_masked_fields, mask_value, user_can_view_pii
from snapadmin.models import SnapModel, EsStorageMode

logger = get_logger(__name__)


def _check_access(info, model) -> None:
    """Enforce authentication + per-model ``view`` permission on a resolver.

    Mirrors the REST API contract: the caller must be authenticated (admin
    session or API token) and hold Django's ``view`` permission for the model;
    when an APIToken authenticated the request, its ``allowed_models`` scope
    applies on top. Disable with ``SNAPADMIN_GRAPHQL_REQUIRE_AUTH = False``
    (not recommended — it exposes every SnapModel to anonymous callers).
    """
    if not getattr(settings, "SNAPADMIN_GRAPHQL_REQUIRE_AUTH", True):
        return

    request = info.context
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise GraphQLError("Authentication required.")

    from snapadmin.models import APIToken

    token = getattr(request, "auth", None)
    if isinstance(token, APIToken) and not token.can_access_model(
        model._meta.app_label, model.__name__
    ):
        raise GraphQLError("Permission denied.")

    perm = f"{model._meta.app_label}.view_{model._meta.model_name}"
    if not user.has_perm(perm):
        raise GraphQLError("Permission denied.")


def _make_relation_guard(model: type[Model]) -> classmethod:
    """Build a ``get_queryset`` classmethod that permission-checks relations.

    graphene_django calls ``DjangoObjectType.get_queryset(queryset, info)``
    whenever it resolves a type through a relation — directly for to-many
    relations (reverse FK / M2M via ``DjangoListField``) and, once the method
    is overridden, for to-one relations too (FK / O2O are routed through
    ``get_node`` → ``get_queryset``). Attaching this guard to every generated
    type extends the top-level ``_check_access`` contract to *every* traversed
    relation, so a caller cannot read a related model it lacks ``view``
    permission on (or that falls outside its API-token scope). On denial it
    raises the same ``GraphQLError`` the top-level resolvers raise, rather than
    returning the related object.
    """

    @classmethod
    def get_queryset(cls: type[DjangoObjectType], queryset: QuerySet, info: GraphQLResolveInfo) -> QuerySet:
        _check_access(info, model)
        return queryset

    return get_queryset


def _make_masked_resolver(field_name: str):
    """Build a field resolver that masks configured PII in GraphQL output.

    Mirrors the REST serializer (:class:`snapadmin.api.serializers.
    PIIMaskingSerializerMixin`): the raw attribute is returned only when the
    requesting user may view raw PII (see
    :func:`snapadmin.masking.user_can_view_pii`), otherwise the value is passed
    through :func:`snapadmin.masking.mask_value`. Fails closed — an absent or
    anonymous user gets the masked value.
    """

    def resolve_masked(root: Model, info: GraphQLResolveInfo) -> object:
        value = getattr(root, field_name)
        user = getattr(info.context, "user", None)
        if user_can_view_pii(user):
            return value
        return mask_value(value)

    return resolve_masked


class SnapGraphQLView(GraphQLView):
    """GraphQLView that also accepts SnapAdmin API tokens.

    Session-authenticated users (e.g. GraphiQL inside the admin) pass through
    unchanged; when the request is anonymous but carries an ``Authorization:
    Token`` header, it is validated with the same backend the REST API uses.
    CSRF is exempted because token clients carry no cookies — the schema is
    read-only (no mutations) and every resolver enforces auth itself.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from rest_framework.exceptions import AuthenticationFailed
            from snapadmin.api.authentication import APITokenAuthentication

            try:
                result = APITokenAuthentication().authenticate(request)
            except AuthenticationFailed:
                result = None
            if result is not None:
                request.user, request.auth = result
        return super().dispatch(request, *args, **kwargs)


def get_dynamic_graphql_schema():
    # graphene.ObjectType collects its fields/resolvers via a metaclass at
    # class-creation time, so attributes must be passed in the namespace dict
    # rather than added later with setattr() (setattr does NOT register fields).
    query_attrs: dict = {}

    for model in apps.get_models():
        if SnapModel.is_concrete_subclass(model):
            try:
                type_name = f"{model._meta.app_label.capitalize()}{model.__name__}Type"

                # Create the DjangoObjectType dynamically, honouring the model's
                # API field exposure control (same exclusion as REST serializers).
                excluded = list(getattr(model, "api_exclude_fields", []) or [])
                if excluded:
                    meta_attr = type('Meta', (), {'model': model, 'exclude': excluded})
                else:
                    meta_attr = type('Meta', (), {'model': model, 'fields': "__all__"})

                # Namespace for the DjangoObjectType. get_queryset extends the
                # per-model view-permission check to every relation traversed
                # from this type; per-field resolvers mask configured PII so it
                # never leaves GraphQL unmasked (mirroring the REST serializer).
                type_attrs: dict = {'Meta': meta_attr, 'get_queryset': _make_relation_guard(model)}
                for masked_field in get_masked_fields(model._meta.app_label, model._meta.model_name):
                    if masked_field not in excluded:
                        type_attrs[f"resolve_{masked_field}"] = _make_masked_resolver(masked_field)
                object_type = type(type_name, (DjangoObjectType,), type_attrs)

                # Add fields to Query
                field_name = f"{model._meta.app_label}_{model.__name__.lower()}"
                list_field_name = f"all_{model._meta.app_label}_{model.__name__.lower()}s"

                # Use factories for resolvers to correctly bind the model class
                def make_single_resolver(m):
                    def resolve_single(self, info, id):
                        _check_access(info, m)
                        # objects.get is ES-aware for ES_ONLY models (EsManager)
                        return m.objects.get(pk=id)
                    return resolve_single

                def make_list_resolver(m):
                    def resolve_list(self, info, search=None, first=None, offset=None):
                        _check_access(info, m)
                        if search:
                            # snap_search routes to Elasticsearch for DUAL/ES_ONLY
                            # models (same smart routing as the REST API).
                            qs = m.snap_search(search, limit=first)
                        else:
                            qs = m.objects.all()
                        offset = offset or 0
                        if first is not None:
                            return qs[offset:offset + first]
                        if offset:
                            return qs[offset:]
                        return qs
                    return resolve_list

                query_attrs[field_name] = graphene.Field(object_type, id=graphene.ID(required=True))
                query_attrs[f"resolve_{field_name}"] = make_single_resolver(model)

                query_attrs[list_field_name] = graphene.List(
                    object_type,
                    search=graphene.String(),
                    first=graphene.Int(),
                    offset=graphene.Int(),
                )
                query_attrs[f"resolve_{list_field_name}"] = make_list_resolver(model)
            except Exception as exc:
                # Skip models that can't be introspected (e.g. no DB table for non-managed)
                logger.warning(
                    "graphql_model_skipped",
                    model=model.__name__,
                    error=str(exc),
                )
                continue

    Query = type("Query", (graphene.ObjectType,), query_attrs)
    return graphene.Schema(query=Query)

schema = get_dynamic_graphql_schema()
