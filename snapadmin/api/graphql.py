
import graphene
from graphene_django import DjangoObjectType
from django.apps import apps
from snapadmin.models import SnapModel, EsStorageMode

def get_dynamic_graphql_schema():
    class Query(graphene.ObjectType):
        pass

    for model in apps.get_models():
        if issubclass(model, SnapModel) and model is not SnapModel:
            try:
                type_name = f"{model._meta.app_label.capitalize()}{model.__name__}Type"

                # Create the DjangoObjectType dynamically
                # We use a closure for model_class to avoid the late binding issue in loops
                meta_attr = type('Meta', (), {'model': model, 'fields': "__all__"})
                object_type = type(type_name, (DjangoObjectType,), {'Meta': meta_attr})

                # Add fields to Query
                field_name = f"{model._meta.app_label}_{model.__name__.lower()}"
                list_field_name = f"all_{model._meta.app_label}_{model.__name__.lower()}s"

                # Use factories for resolvers to correctly bind the model class
                def make_single_resolver(m):
                    def resolve_single(self, info, id):
                        if getattr(m, 'es_storage_mode', None) == EsStorageMode.ES_ONLY:
                            # For ES_ONLY, try to find in ES
                            return m.objects.get(pk=id)
                        return m.objects.get(pk=id)
                    return resolve_single

                def make_list_resolver(m):
                    def resolve_list(self, info):
                        # If DUAL or ES_ONLY, we could potentially use snap_search here
                        # But objects.all() for SnapModel is already ES-aware if ES_ONLY
                        return m.objects.all()
                    return resolve_list

                setattr(Query, field_name, graphene.Field(object_type, id=graphene.ID(required=True)))
                setattr(Query, f"resolve_{field_name}", make_single_resolver(model))

                setattr(Query, list_field_name, graphene.List(object_type))
                setattr(Query, f"resolve_{list_field_name}", make_list_resolver(model))
            except Exception:
                # Skip models that can't be introspected (e.g. no DB table for non-managed)
                continue

    return graphene.Schema(query=Query)

schema = get_dynamic_graphql_schema()
