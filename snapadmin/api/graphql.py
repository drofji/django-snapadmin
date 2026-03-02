"""
snapadmin/api/graphql.py

Dynamic GraphQL schema generation for SnapAdmin models.
"""

import graphene
from graphene_django import DjangoObjectType
from django.apps import apps
from snapadmin.models import APIToken, SnapModel
from snapadmin.api.authentication import token_has_permission

_type_cache = {}


def get_type_for_model(model_class):
    """
    Dynamically create or retrieve a DjangoObjectType for a given model.
    """
    if model_class in _type_cache:
        return _type_cache[model_class]

    meta_class = type(
        "Meta",
        (),
        {
            "model": model_class,
            "fields": "__all__",
        },
    )
    type_class = type(
        f"{model_class.__name__}Type",
        (DjangoObjectType,),
        {"Meta": meta_class},
    )
    _type_cache[model_class] = type_class
    return type_class


def create_schema():
    """
    Construct the dynamic Query class and return the Graphene Schema.
    """
    query_attrs = {}

    for model in apps.get_models():
        if not (issubclass(model, SnapModel) and model is not SnapModel):
            continue

        app_label = model._meta.app_label
        model_name = model.__name__
        model_type = get_type_for_model(model)

        # ── List field (all_demo_products, all_demo_customers, etc.) ─────────
        list_field_name = f"all_{app_label}_{model_name.lower()}"
        query_attrs[list_field_name] = graphene.List(model_type)

        def make_list_resolver(app_label, model_name, model_class):
            def resolve_all(self, info):
                token = getattr(info.context, "auth", None)
                user = info.context.user

                if not isinstance(token, APIToken):
                    return model_class.objects.none()

                if not token_has_permission(token, user, app_label, model_name, "view"):
                    return model_class.objects.none()

                return model_class.objects.all()

            return resolve_all

        query_attrs[f"resolve_{list_field_name}"] = make_list_resolver(
            app_label, model_name, model
        )

        # ── Detail field (demo_product, demo_customer, etc.) ─────────────────
        detail_field_name = f"{app_label}_{model_name.lower()}"
        query_attrs[detail_field_name] = graphene.Field(
            model_type, id=graphene.Int(required=True)
        )

        def make_detail_resolver(app_label, model_name, model_class):
            def resolve_detail(self, info, id):
                token = getattr(info.context, "auth", None)
                user = info.context.user

                if not isinstance(token, APIToken):
                    return None

                if not token_has_permission(token, user, app_label, model_name, "view"):
                    return None

                try:
                    return model_class.objects.get(pk=id)
                except model_class.DoesNotExist:
                    return None

            return resolve_detail

        query_attrs[f"resolve_{detail_field_name}"] = make_detail_resolver(
            app_label, model_name, model
        )

    Query = type("Query", (graphene.ObjectType,), query_attrs)
    return graphene.Schema(query=Query)


# Singleton schema instance
schema = create_schema()
