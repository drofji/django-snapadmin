"""
snapadmin/api/filters.py

Dynamic filter generation for SnapAdmin REST API.
Automatically builds a django-filter FilterSet for any model based on field types.
drf-spectacular introspects the filter backend and exposes all parameters in Swagger.
"""

import django_filters
from django.db import models as django_models
from django_filters.rest_framework import DjangoFilterBackend

_filterset_cache: dict = {}


def _build_filters_for_model(model_class) -> dict:
    filters = {}

    for field in model_class._meta.get_fields():
        if not hasattr(field, "column"):
            continue

        name = field.name

        if isinstance(field, (
            django_models.CharField,
            django_models.TextField,
            django_models.EmailField,
            django_models.URLField,
            django_models.SlugField,
        )):
            filters[name] = django_filters.CharFilter(lookup_expr="icontains")

        elif isinstance(field, django_models.UUIDField):
            filters[name] = django_filters.UUIDFilter(lookup_expr="exact")

        elif isinstance(field, django_models.BooleanField):
            filters[name] = django_filters.BooleanFilter(lookup_expr="exact")

        elif isinstance(field, (
            django_models.IntegerField,
            django_models.BigIntegerField,
            django_models.SmallIntegerField,
            django_models.PositiveIntegerField,
            django_models.PositiveSmallIntegerField,
            django_models.FloatField,
            django_models.DecimalField,
        )):
            filters[name] = django_filters.NumberFilter(lookup_expr="exact")
            filters[f"{name}__gte"] = django_filters.NumberFilter(
                field_name=name, lookup_expr="gte"
            )
            filters[f"{name}__lte"] = django_filters.NumberFilter(
                field_name=name, lookup_expr="lte"
            )

        elif isinstance(field, (django_models.DateTimeField, django_models.DateField)):
            filter_cls = (
                django_filters.DateTimeFilter
                if isinstance(field, django_models.DateTimeField)
                else django_filters.DateFilter
            )
            filters[name] = filter_cls(lookup_expr="exact")
            filters[f"{name}__gte"] = filter_cls(field_name=name, lookup_expr="gte")
            filters[f"{name}__lte"] = filter_cls(field_name=name, lookup_expr="lte")

        elif isinstance(field, django_models.ForeignKey):
            filters[f"{name}_id"] = django_filters.NumberFilter(
                field_name=f"{name}_id", lookup_expr="exact"
            )

    return filters


def build_filterset_for_model(model_class):
    """Return a cached FilterSet class auto-generated from model field types."""
    cache_key = f"{model_class._meta.app_label}.{model_class._meta.model_name}"
    if cache_key in _filterset_cache:
        return _filterset_cache[cache_key]

    filter_fields = _build_filters_for_model(model_class)
    meta = type("Meta", (), {"model": model_class, "fields": []})
    filterset_cls = type(
        f"{model_class.__name__}AutoFilterSet",
        (django_filters.FilterSet,),
        {**filter_fields, "Meta": meta},
    )
    _filterset_cache[cache_key] = filterset_cls
    return filterset_cls


class SnapAdminFilterBackend(DjangoFilterBackend):
    """
    DjangoFilterBackend subclass that builds a FilterSet dynamically for any model.
    drf-spectacular introspects this to generate Swagger filter parameters automatically.
    """

    def get_filterset_class(self, view, queryset=None):
        get_model = getattr(view, "_get_model_class", None)
        if get_model is None:
            return None
        model_class = get_model()
        if model_class is None:
            return None
        return build_filterset_for_model(model_class)
