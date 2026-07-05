
import graphene
from graphene_django import DjangoObjectType
from graphene_django.views import GraphQLView
from django.apps import apps
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from graphql import GraphQLError

from snapadmin.logging_config import get_logger
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
        if issubclass(model, SnapModel) and model is not SnapModel:
            try:
                type_name = f"{model._meta.app_label.capitalize()}{model.__name__}Type"

                # Create the DjangoObjectType dynamically, honouring the model's
                # API field exposure control (same exclusion as REST serializers).
                excluded = list(getattr(model, "api_exclude_fields", []) or [])
                if excluded:
                    meta_attr = type('Meta', (), {'model': model, 'exclude': excluded})
                else:
                    meta_attr = type('Meta', (), {'model': model, 'fields': "__all__"})
                object_type = type(type_name, (DjangoObjectType,), {'Meta': meta_attr})

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
