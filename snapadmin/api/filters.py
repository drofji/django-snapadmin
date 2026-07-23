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
from django.conf import settings
from django.db import connections, models as django_models
from django.db.models import Q, QuerySet
from django.utils.module_loading import import_string
from django_filters.constants import EMPTY_VALUES
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import ValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter, SearchFilter

from snapadmin.masking import get_masked_fields, user_can_view_pii

_filterset_cache: dict = {}

# Default row-scan ceiling for JsonKeyPathFilter's Python list-membership fallback
# (backends without native JSON containment, e.g. SQLite). Overridable per project
# via SNAPADMIN_API_JSON_FILTER_SCAN_CAP.
_JSON_FILTER_SCAN_CAP_DEFAULT = 100_000

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


class _NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """NumberFilter accepting a comma-separated list, for numeric/FK ``__in`` lookups."""


class JsonKeyPathFilter(django_filters.CharFilter):
    """
    Filters on a single JSON key-path declared in a model's ``api_json_filters``,
    e.g. ``api_json_filters = {"payload": ["a.b"]}`` exposes the query parameter
    ``?payload__a__b=value``.

    Value semantics are deliberately broader than a plain field filter, because a
    JSON value at a given path can be either a scalar or a list from row to row:

    - **Scalar match** — the JSON value at the path equals ``value`` exactly.
      Implemented with Django's key-transform lookup (``field__key1__key2=value``),
      which every backend — including SQLite — supports natively.
    - **List-membership match** — the JSON value at the path is itself a list and
      ``value`` is one of its elements. On backends that support it, this uses the
      native ``__contains=[value]`` JSON-containment lookup. SQLite's
      ``connection.features.supports_json_field_contains`` is ``False`` (Django's
      ``contains`` lookup raises ``NotSupportedError`` there), so on SQLite (and any
      other backend without native support) this falls back to a per-row Python
      membership check on the extracted JSON value.

    A row matches if either check matches (a single query parameter covers both
    cases without the caller having to know which shape a given row's data uses).

    **Comma-separated OR.** Like the ``__in`` text/numeric filters, a comma in the
    value is an OR: ``?payload__a__b=x,y`` matches rows whose value at the path is
    ``x`` *or* ``y`` (each side still tried as both a scalar and a list-membership
    match). A value that legitimately contains a comma can't be expressed — the
    same trade-off ``__in`` makes.

    **Laziness at scale.** On a backend with native JSON containment
    (PostgreSQL/MySQL) the whole thing is one lazy ``qs.filter(Q(...))`` — it
    composes with ``.iterator()`` (so the streaming export never materialises a
    PK list). The SQLite / no-native fallback must scan rows in Python for the
    list-membership half, so it is capped at ``SNAPADMIN_API_JSON_FILTER_SCAN_CAP``
    rows (default 100_000); past the cap it raises ``ValidationError`` (HTTP 400)
    rather than risk OOM — pointing the caller at a native-JSON backend or ES.

    JSON columns carry no index, so any of these filters is always a full table
    scan — for filtering at scale on large tables, use ``SnapModel.es_search()``
    (Elasticsearch integration) instead.
    """

    def __init__(self, json_field_name: str, key_path: str, **kwargs) -> None:
        self.json_field_name = json_field_name
        self.key_parts = key_path.split(".")
        self.lookup_field = "__".join([json_field_name, *self.key_parts])
        super().__init__(**kwargs)

    def filter(self, qs: QuerySet, value: str | None) -> QuerySet:
        if value in EMPTY_VALUES:
            return qs
        values = [part.strip() for part in value.split(",") if part.strip()]
        if not values:
            return qs

        if connections[qs.db].features.supports_json_field_contains:
            return self._native_filter(qs, values)
        return self._python_fallback_filter(qs, values)

    def _q_for_value(self, value: str) -> Q:
        """Scalar-exact OR native list-membership for one value."""
        return (
            Q(**{self.lookup_field: value})
            | Q(**{f"{self.lookup_field}__contains": [value]})
        )

    def _native_filter(self, qs: QuerySet, values: list[str]) -> QuerySet:
        # Native JSON containment (PostgreSQL/MySQL): return one lazy queryset so
        # `.iterator()` still streams — no PK list is built. Seed the OR from the
        # first value (an empty Q() OR-ed would match every row).
        combined = self._q_for_value(values[0])
        for value in values[1:]:
            combined |= self._q_for_value(value)
        return self.get_method(qs)(combined)

    def _python_fallback_filter(self, qs: QuerySet, values: list[str]) -> QuerySet:
        # No native JSON `contains` (e.g. SQLite): the scalar-exact half is still a
        # DB lookup, but list-membership needs a per-row Python scan, so it is
        # capped to avoid pulling an unbounded table into memory.
        scalar_q = Q(**{self.lookup_field: values[0]})
        for value in values[1:]:
            scalar_q |= Q(**{self.lookup_field: value})
        scalar_pks = set(qs.filter(scalar_q).values_list("pk", flat=True))
        membership_pks = self._python_membership_pks(qs, values)
        return self.get_method(qs)(pk__in=scalar_pks | membership_pks)

    def _scan_cap(self) -> int:
        return (
            getattr(settings, "SNAPADMIN_API_JSON_FILTER_SCAN_CAP", None)
            or _JSON_FILTER_SCAN_CAP_DEFAULT
        )

    def _python_membership_pks(self, qs: QuerySet, values: list[str]) -> set:
        cap = self._scan_cap()
        if qs.count() > cap:
            raise ValidationError(
                f"JSON filter '{self.lookup_field}' would scan more than {cap} rows "
                "on a database without native JSON support; narrow the query, raise "
                "SNAPADMIN_API_JSON_FILTER_SCAN_CAP, or use a native-JSON backend / "
                "Elasticsearch."
            )
        pks = set()
        for pk, data in qs.values_list("pk", self.json_field_name).iterator():
            node = data
            for part in self.key_parts:
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, list) and any(value in node for value in values):
                pks.add(pk)
        return pks


def _text_filter_for_lookup(name: str, lookup: str) -> tuple[str, django_filters.Filter]:
    """Return the ``(query_param, Filter)`` pair for one text-field lookup.

    "exact" is bound to the bare field name so ``?field=value`` performs an exact,
    index-usable match; every other lookup gets an explicit "<field>__<lookup>" key.
    """
    if lookup == "exact":
        return name, django_filters.CharFilter(field_name=name, lookup_expr="exact")
    if lookup == "in":
        return f"{name}__in", _CharInFilter(field_name=name, lookup_expr="in")
    if lookup == "isnull":
        # A CharFilter would forward the raw string ("true"/"false") to Django's
        # isnull lookup, which only accepts a bool -> ValueError -> HTTP 500. Map it
        # to a BooleanFilter so ?field__isnull=true/false parses to a real bool.
        return f"{name}__isnull", django_filters.BooleanFilter(field_name=name, lookup_expr="isnull")
    return f"{name}__{lookup}", django_filters.CharFilter(field_name=name, lookup_expr=lookup)


def _resolve_text_lookups(
    model_class: type[django_models.Model], name: str, model_lookups: dict[str, list[str]]
) -> list[str]:
    """The lookup set for one text field, first non-``None`` source winning.

    Order: the per-field ``api_filter_lookups`` entry (narrowest, model-authored),
    then the model-wide ``api_default_text_lookups``, then the project-wide
    ``SNAPADMIN_API_TEXT_LOOKUPS`` setting, then the library default. This lets a
    project drop the non-indexable ``icontains`` once — per-model or per-project —
    without enumerating every column, while a per-field override still wins.
    """
    field_lookups = model_lookups.get(name)
    if field_lookups is not None:
        return field_lookups
    model_default = getattr(model_class, "api_default_text_lookups", None)
    if model_default is not None:
        return model_default
    project_default = getattr(settings, "SNAPADMIN_API_TEXT_LOOKUPS", None)
    if project_default is not None:
        return project_default
    return _TEXT_LOOKUPS_DEFAULT


def _build_filters_for_model(model_class: type[django_models.Model]) -> dict[str, django_filters.Filter]:
    filters: dict[str, django_filters.Filter] = {}
    model_lookups: dict[str, list[str]] = getattr(model_class, "api_filter_lookups", None) or {}

    for field in model_class._meta.get_fields():
        if not hasattr(field, "column"):
            continue

        name = field.name

        if isinstance(field, _TEXT_FIELD_TYPES):
            lookups = _resolve_text_lookups(model_class, name, model_lookups)
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
            filters[f"{name}__in"] = _NumberInFilter(field_name=name, lookup_expr="in")
            filters[f"{name}__isnull"] = django_filters.BooleanFilter(
                field_name=name, lookup_expr="isnull"
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
            # No __in for dates: an exact-datetime membership list is rarely useful,
            # and ranges are already covered by __gte/__lte. isnull stays available.
            filters[f"{name}__isnull"] = django_filters.BooleanFilter(
                field_name=name, lookup_expr="isnull"
            )

        elif isinstance(field, django_models.ForeignKey):
            filters[f"{name}_id"] = django_filters.NumberFilter(
                field_name=f"{name}_id", lookup_expr="exact"
            )
            filters[f"{name}_id__in"] = _NumberInFilter(
                field_name=f"{name}_id", lookup_expr="in"
            )
            filters[f"{name}_id__isnull"] = django_filters.BooleanFilter(
                field_name=f"{name}_id", lookup_expr="isnull"
            )

        elif isinstance(field, django_models.JSONField):
            # JSON columns get no filter by default — only the key-paths explicitly
            # declared in the model's api_json_filters are exposed as query params,
            # e.g. api_json_filters = {"payload": ["a.b"]} -> ?payload__a__b=value.
            json_filters: dict[str, list[str]] = getattr(model_class, "api_json_filters", None) or {}
            for key_path in json_filters.get(name, []):
                param_name = f"{name}__{key_path.replace('.', '__')}"
                filters[param_name] = JsonKeyPathFilter(json_field_name=name, key_path=key_path)

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

    def get_filterset_kwargs(self, request, queryset, view):
        """Drop any query param that targets a masked field the caller can't see.

        Only reached once :meth:`get_filterset_class` has already resolved a
        concrete model (``get_filterset`` short-circuits to a no-op otherwise),
        so ``view._get_model_class()`` is guaranteed non-``None`` here.

        A masked field must not be filterable at all: even an exact match lets
        a caller use match/no-match (or the returned row count, for
        ``__icontains``) as an oracle to recover a value they'd only ever see
        starred in the response body. The masked param is silently dropped
        (as if never sent) rather than rejected with 400 — consistent with an
        unknown query param, and it doesn't confirm the field is masked vs.
        simply unfiltered.
        """
        kwargs = super().get_filterset_kwargs(request, queryset, view)
        model_class = view._get_model_class()
        masked = set(get_masked_fields(model_class._meta.app_label, model_class._meta.model_name))
        if not masked or user_can_view_pii(request.user):
            return kwargs

        data = kwargs["data"].copy()
        for key in list(data.keys()):
            field_name = key.split("__", 1)[0]
            if field_name in masked:
                del data[key]
        kwargs["data"] = data
        return kwargs

    def get_filterset_class(self, view, queryset=None):
        get_model = getattr(view, "_get_model_class", None)
        if get_model is None:
            return None
        model_class = get_model()
        if model_class is None:
            return None
        return build_filterset_for_model(model_class)


def get_api_filter_backends() -> list[type[BaseFilterBackend]]:
    """Resolve the dynamic REST API's filter-backend chain from settings.

    ``SNAPADMIN_API_FILTER_BACKEND`` lets a project swap the auto-generated
    ``SnapAdminFilterBackend`` (or the whole chain) for a custom
    ``FilterSet``/backend without monkeypatching ``DynamicModelViewSet``:

    - **Unset** (the default) → the built-in chain
      ``[SnapAdminFilterBackend, SearchFilter, OrderingFilter]``.
    - **A single dotted path or backend class** → a one-element chain of it.
    - **A list/tuple of dotted paths / classes** → that exact chain, replacing
      the default entirely (mirroring DRF's own ``DEFAULT_FILTER_BACKENDS``).

    String entries are imported with :func:`~django.utils.module_loading.import_string`;
    a class object is used as-is.
    """
    configured = getattr(settings, "SNAPADMIN_API_FILTER_BACKEND", None)
    if configured is None:
        return [SnapAdminFilterBackend, SearchFilter, OrderingFilter]
    if isinstance(configured, (str, type)):
        configured = [configured]
    return [import_string(backend) if isinstance(backend, str) else backend
            for backend in configured]
