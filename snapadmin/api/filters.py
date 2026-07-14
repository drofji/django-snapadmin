"""
snapadmin/api/filters.py

Dynamic filter generation for SnapAdmin REST API.
Automatically builds a django-filter FilterSet for any model based on field types.
drf-spectacular introspects the filter backend and exposes all parameters in Swagger.

Text-type fields (CharField, TextField, EmailField, URLField, SlugField) default the
*bare* ``?field=value`` query parameter to an exact match — index-usable, unlike a
leading-wildcard substring search. Substring matching stays available through the
explicit ``?field__icontains=value`` suffix (mirroring the ``__gte``/``__lte`` suffix
pattern already used below for numeric and date fields). A model can widen or narrow
the lookup set for one of its own fields via ``SnapModel.api_filter_lookups``.
"""

import django_filters
from django.db import models as django_models
from django_filters.rest_framework import DjangoFilterBackend

_filterset_cache: dict = {}

# Text-type model fields that get the exact/icontains/startswith/in lookup set below.
_TEXT_FIELD_TYPES = (
    django_models.CharField,
    django_models.TextField,
    django_models.EmailField,
    django_models.URLField,
    django_models.SlugField,
)

# Library default lookup set for a text field when the model hasn't overridden it via
# api_filter_lookups. "exact" is exposed on the bare field name (no suffix); every
# other lookup is exposed as an explicit "<field>__<lookup>" suffix.
_TEXT_LOOKUPS_DEFAULT: list[str] = ["exact", "icontains", "startswith", "in"]


class _CharInFilter(django_filters.BaseInFilter, django_filters.CharFilter):
    """CharFilter accepting a comma-separated list, for ``__in`` lookups."""


def _text_filter_for_lookup(name: str, lookup: str) -> tuple[str, django_filters.Filter]:
    """Return the ``(query_param, Filter)`` pair for one text-field lookup.

    "exact" is bound to the bare field name so ``?field=value`` performs an exact,
    index-usable match; every other lookup gets an explicit "<field>__<lookup>" key.
    """
    if lookup == "exact":
        return name, django_filters.CharFilter(field_name=name, lookup_expr="exact")
    if lookup == "in":
        return f"{name}__in", _CharInFilter(field_name=name, lookup_expr="in")
    return f"{name}__{lookup}", django_filters.CharFilter(field_name=name, lookup_expr=lookup)


def _build_filters_for_model(model_class: type[django_models.Model]) -> dict[str, django_filters.Filter]:
    filters: dict[str, django_filters.Filter] = {}
    model_lookups: dict[str, list[str]] = getattr(model_class, "api_filter_lookups", None) or {}

    for field in model_class._meta.get_fields():
        if not hasattr(field, "column"):
            continue

        name = field.name

        if isinstance(field, _TEXT_FIELD_TYPES):
            lookups = model_lookups.get(name, _TEXT_LOOKUPS_DEFAULT)
            for lookup in lookups:
                key, filter_instance = _text_filter_for_lookup(name, lookup)
                filters[key] = filter_instance

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

    def filter_queryset(self, request, queryset, view):
        from snapadmin.models import EsQuerySet

        # ES_ONLY models are served from an EsQuerySet, which django-filter
        # cannot operate on (it asserts a real QuerySet) — pass it through.
        if isinstance(queryset, EsQuerySet):
            return queryset
        return super().filter_queryset(request, queryset, view)

    def get_filterset_class(self, view, queryset=None):
        get_model = getattr(view, "_get_model_class", None)
        if get_model is None:
            return None
        model_class = get_model()
        if model_class is None:
            return None
        return build_filterset_for_model(model_class)
