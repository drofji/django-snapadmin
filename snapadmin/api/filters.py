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

**Strict query parameters** (``SNAPADMIN_API_STRICT_QUERY_PARAMS``, default ``True``):
a query parameter that is neither a declared filter nor one the API reads itself is
answered with ``400`` naming it, instead of being dropped — a dropped filter returns
every row with a ``200``, which is the worst possible answer for a client about to
export or delete "the matching rows". A parameter on a field the caller may not see
gets exactly the refusal a non-existent field gets, so it is no oracle either.
"""

import django_filters
from django.core.exceptions import FieldDoesNotExist
from django.db import connections, models as django_models
from django.db.models import Q, QuerySet
from django.utils.module_loading import import_string
from django_filters.constants import EMPTY_VALUES
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import ValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter, SearchFilter
from rest_framework.settings import api_settings

from snapadmin.conf import get_setting
from snapadmin.masking import get_masked_fields, user_can_access_field, user_can_view_pii
from snapadmin.registry import get_model_meta

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

# A URL is asked about by its ending as often as by its start (".pdf", a path suffix),
# so URL fields add the two ending lookups to the default. Like ``icontains`` they
# cannot use an index; a project that minds narrows them like any other default.
_URL_LOOKUPS_DEFAULT: list[str] = [*_TEXT_LOOKUPS_DEFAULT, "endswith", "iendswith"]

#: Query parameters the generated viewset reads itself rather than through the
#: filterset, accepted on every endpoint in strict mode. The search, ordering and
#: format parameters come from DRF's own settings (see ``_allowed_query_params``).
#: ``page``/``page_size`` change no row set, so a client reusing one query string for
#: ``count/`` and the list is not refused for them.
_RESERVED_QUERY_PARAMS: frozenset[str] = frozenset({"page", "page_size"})

#: Parameters exactly one action reads. Accepted anywhere else they would be dropped
#: without effect — the silence strict mode exists to refuse.
_RESERVED_QUERY_PARAMS_BY_ACTION: dict[str, frozenset[str]] = {
    "export": frozenset({"limit"}),
}

#: Actions whose handler owns its own query string: a project's ``@snap_action``
#: reads whatever parameters it defines, which no filterset can know about.
_ACTIONS_OWNING_THEIR_QUERY: frozenset[str] = frozenset({"dispatch_action"})


def _strict_query_params() -> bool:
    """Whether the generated REST API refuses query parameters it does not understand.

    ``SNAPADMIN_API_STRICT_QUERY_PARAMS`` (default ``True``). ``False`` restores the
    behaviour before 0.1.0b11: unknown filter parameters, unknown ordering terms and
    filters on hidden fields are dropped without a word.
    """
    return bool(get_setting("SNAPADMIN_API_STRICT_QUERY_PARAMS", True))


def _allowed_query_params(action: str | None) -> set[str]:
    """Every non-filter parameter strict mode accepts for ``action``."""
    allowed = set(_RESERVED_QUERY_PARAMS)
    allowed |= _RESERVED_QUERY_PARAMS_BY_ACTION.get(action or "", frozenset())
    allowed |= {api_settings.SEARCH_PARAM, api_settings.ORDERING_PARAM}
    if api_settings.URL_FORMAT_OVERRIDE:
        allowed.add(api_settings.URL_FORMAT_OVERRIDE)
    allowed |= {str(name) for name in get_setting("SNAPADMIN_API_EXTRA_QUERY_PARAMS", None) or ()}
    return allowed


def _visible_search_fields(user, model_class, search_fields) -> tuple[str, ...]:
    """The search fields ``user`` may match a term against on ``model_class``.

    A search field may carry a DRF prefix (``^name``, ``=code``, ``@body``,
    ``$re``) and may follow relations (``customer__email``): the prefix is not
    part of the name, and a related model's hidden field is as hidden as one of
    the model's own — comparing the raw entry with the hidden names let both
    through, and a match/no-match on them is an oracle for the hidden value.
    """
    return tuple(name for name in search_fields if not _search_path_hidden(user, model_class, name))


def _search_path_hidden(user, model_class, search_field: str) -> bool:
    current = model_class
    for part in search_field.lstrip("^=@$").split("__"):
        if part in _hidden_filter_fields(user, current):
            return True
        try:
            field = current._meta.get_field(part)
        except FieldDoesNotExist:
            return False  # a lookup name, not a field
        if not field.is_relation or field.related_model is None:
            return False
        current = field.related_model
    return False


def _hidden_filter_fields(user, model_class: type[django_models.Model]) -> set[str]:
    """Names a caller may neither filter, order nor search by on ``model_class``.

    Two independent reasons feed the set: PII masking (unless the caller holds
    ``view_raw_pii``) and an ``api_field_permissions`` read guard the caller fails.
    A hidden foreign key is hidden under its column name too (``customer`` and
    ``customer_id``), because the generated filters expose a foreign key by its
    column — matching on the field name alone left ``?customer_id=`` as an oracle.
    """
    app_label, model_name = model_class._meta.app_label, model_class._meta.model_name or ""
    hidden = set() if user_can_view_pii(user) else set(get_masked_fields(app_label, model_name))
    permissions = get_model_meta(model_class, "api_field_permissions", {}) or {}
    hidden |= {
        field
        for field, rule in permissions.items()
        if isinstance(rule, dict)
        and rule.get("read")
        and not user_can_access_field(user, model_class, field, write=False)
    }
    for name in list(hidden):
        try:
            field = model_class._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        attname = getattr(field, "attname", None)
        if attname and attname != name:
            hidden.add(attname)
    return hidden


def _param_field(param: str) -> str:
    """The field part of a filter parameter: ``"email__icontains"`` -> ``"email"``."""
    return param.split("__", 1)[0]


def _unknown_filters_message(unknown: list[str], visible: list[str]) -> str:
    """The ``400`` text for parameters no visible filter answers to.

    A field the caller may see gets its accepted parameters listed; a hidden field
    and a non-existent one get nothing beyond their own name, so the two answers
    cannot be told apart.
    """
    by_field: dict[str, list[str]] = {}
    for name in visible:
        by_field.setdefault(_param_field(name), []).append(name)
    parts = [
        "Unknown or disallowed filter parameter(s): "
        + ", ".join(repr(name) for name in sorted(unknown))
        + "."
    ]
    for field in sorted({_param_field(name) for name in unknown}):
        if field in by_field:
            parts.append(f"Allowed for {field!r}: {', '.join(sorted(by_field[field]))}.")
    return " ".join(parts)


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
        if value is None or value in EMPTY_VALUES:
            return qs
        values = [part.strip() for part in value.split(",") if part.strip()]
        if not values:
            return qs

        if connections[qs.db].features.supports_json_field_contains:
            return self._native_filter(qs, values)
        return self._python_fallback_filter(qs, values)

    def _q_for_value(self, value: str) -> Q:
        """Scalar-exact OR native list-membership for one value."""
        return Q(**{self.lookup_field: value}) | Q(**{f"{self.lookup_field}__contains": [value]})

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
            get_setting("SNAPADMIN_API_JSON_FILTER_SCAN_CAP", None) or _JSON_FILTER_SCAN_CAP_DEFAULT
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
        return f"{name}__isnull", django_filters.BooleanFilter(
            field_name=name, lookup_expr="isnull"
        )
    return f"{name}__{lookup}", django_filters.CharFilter(field_name=name, lookup_expr=lookup)


def _resolve_text_lookups(
    model_class: type[django_models.Model],
    name: str,
    model_lookups: dict[str, list[str]],
    library_default: list[str] = _TEXT_LOOKUPS_DEFAULT,
) -> list[str]:
    """The lookup set for one text field, first non-``None`` source winning.

    Order: the per-field ``api_filter_lookups`` entry (narrowest, model-authored),
    then the model-wide ``api_default_text_lookups``, then the project-wide
    ``SNAPADMIN_API_TEXT_LOOKUPS`` setting, then ``library_default`` (the text
    default, or the URL default with the ending lookups). This lets a project drop
    the non-indexable ``icontains`` once — per-model or per-project — without
    enumerating every column, while a per-field override still wins.
    """
    field_lookups = model_lookups.get(name)
    if field_lookups is not None:
        return field_lookups
    model_default = get_model_meta(model_class, "api_default_text_lookups", None)
    if model_default is not None:
        return model_default
    project_default = get_setting("SNAPADMIN_API_TEXT_LOOKUPS", None)
    if project_default is not None:
        return project_default
    return library_default


def _build_filters_for_model(
    model_class: type[django_models.Model],
) -> dict[str, django_filters.Filter]:
    filters: dict[str, django_filters.Filter] = {}
    model_lookups: dict[str, list[str]] = (
        get_model_meta(model_class, "api_filter_lookups", None) or {}
    )

    for field in model_class._meta.get_fields():
        if not hasattr(field, "column"):
            continue

        # An encrypted column cannot answer any of the lookups generated below.
        # `icontains` / `gte` / `startswith` over ciphertext are refused by the
        # field itself, so a generated filter is a documented query parameter
        # that turns into a 500 the moment anyone uses it — and `exact` would
        # need the blind index, which the field rewrites internally rather than
        # exposing as a second filter name. Its `<field>_bi` sibling is skipped
        # for the opposite reason: filtering on it would work, and would be an
        # equality oracle over a value the caller is not allowed to read.
        if getattr(field, "is_snap_encrypted", False) or getattr(
            field, "is_snap_blind_index", False
        ):
            continue

        name = field.name

        if isinstance(field, _TEXT_FIELD_TYPES):
            library_default = (
                _URL_LOOKUPS_DEFAULT
                if isinstance(field, django_models.URLField)
                else _TEXT_LOOKUPS_DEFAULT
            )
            lookups = _resolve_text_lookups(model_class, name, model_lookups, library_default)
            for lookup in lookups:
                key, filter_instance = _text_filter_for_lookup(name, lookup)
                filters[key] = filter_instance

        elif isinstance(field, django_models.UUIDField):
            filters[name] = django_filters.UUIDFilter(lookup_expr="exact")

        elif isinstance(field, django_models.BooleanField):
            filters[name] = django_filters.BooleanFilter(lookup_expr="exact")

        elif isinstance(
            field,
            (
                django_models.IntegerField,
                django_models.BigIntegerField,
                django_models.SmallIntegerField,
                django_models.PositiveIntegerField,
                django_models.PositiveSmallIntegerField,
                django_models.FloatField,
                django_models.DecimalField,
            ),
        ):
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
            filters[f"{name}_id__in"] = _NumberInFilter(field_name=f"{name}_id", lookup_expr="in")
            filters[f"{name}_id__isnull"] = django_filters.BooleanFilter(
                field_name=f"{name}_id", lookup_expr="isnull"
            )

        elif isinstance(field, django_models.JSONField):
            # JSON columns get no filter by default — only the key-paths explicitly
            # declared in the model's api_json_filters are exposed as query params,
            # e.g. api_json_filters = {"payload": ["a.b"]} -> ?payload__a__b=value.
            json_filters: dict[str, list[str]] = (
                get_model_meta(model_class, "api_json_filters", None) or {}
            )
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

        model_class = self._model_class(view)
        if (
            model_class is not None
            and _strict_query_params()
            and getattr(view, "action", None) not in _ACTIONS_OWNING_THEIR_QUERY
        ):
            self._refuse_unknown_parameters(request, queryset, view, model_class)

        # ES_ONLY models are served from an EsQuerySet, which django-filter
        # cannot operate on (it asserts a real QuerySet) — pass it through.
        if isinstance(queryset, EsQuerySet):
            return queryset
        return super().filter_queryset(request, queryset, view)

    def _refuse_unknown_parameters(self, request, queryset, view, model_class) -> None:
        """Raise ``400`` for every query parameter nothing will act on (strict mode).

        Runs before any filtering, for every action that filters a queryset
        (``list``, ``count``, ``export``, ``fetch_by`` and the detail routes,
        through ``get_object()``). Filter parameters, then ordering terms; the
        search parameter is the view's own check, since only the view knows
        whether its search went to Elasticsearch.
        """
        from snapadmin.models import EsQuerySet

        action = getattr(view, "action", None)
        sent = [key for key in request.query_params if key not in _allowed_query_params(action)]
        if isinstance(queryset, EsQuerySet):
            if sent:
                raise ValidationError(
                    {
                        "filters": [
                            f"{model_class._meta.label} is stored in Elasticsearch only "
                            "(ES_ONLY) and cannot be filtered by query parameters: "
                            + ", ".join(repr(key) for key in sorted(sent))
                            + f". Use ?{api_settings.SEARCH_PARAM}= instead."
                        ]
                    }
                )
            return

        hidden = _hidden_filter_fields(request.user, model_class)
        visible = [
            name
            for name in build_filterset_for_model(model_class).base_filters
            if _param_field(name) not in hidden
        ]
        unknown = [key for key in sent if key not in visible]
        if unknown:
            raise ValidationError({"filters": [_unknown_filters_message(unknown, visible)]})
        self._refuse_unknown_ordering(request, queryset, view)

    @staticmethod
    def _refuse_unknown_ordering(request, queryset, view) -> None:
        """Raise ``400`` for ordering terms DRF's ``OrderingFilter`` would drop.

        Reuses DRF's own validity rule (``remove_invalid_fields``), so a term is
        refused exactly when it would otherwise have been ignored. A masked field
        is missing from the view's ``ordering_fields`` and is refused with the
        same text as a field that does not exist; the list of valid terms never
        names it.
        """
        raw = request.query_params.get(api_settings.ORDERING_PARAM, "")
        terms = [term.strip() for term in raw.split(",") if term.strip()]
        if not terms:
            return
        ordering = OrderingFilter()
        valid_terms = set(ordering.remove_invalid_fields(queryset, terms, view, request))
        invalid = [term for term in terms if term not in valid_terms]
        if invalid:
            fields = sorted(
                name
                for name, _label in ordering.get_valid_fields(queryset, view, {"request": request})
            )
            raise ValidationError(
                {
                    "ordering": [
                        "Unknown ordering field(s): "
                        + ", ".join(repr(term) for term in invalid)
                        + ". Order by one of: "
                        + ", ".join(fields)
                        + " (prefix with '-' to reverse)."
                    ]
                }
            )

    def get_filterset_kwargs(self, request, queryset, view):
        """Drop any query param targeting a field the caller can't see.

        Only reached once :meth:`get_filterset_class` has already resolved a
        concrete model (``get_filterset`` short-circuits to a no-op otherwise),
        so ``view._get_model_class()`` is guaranteed non-``None`` here.

        A field the caller cannot see at all must not be filterable either:
        even an exact match lets a caller use match/no-match (or the returned
        row count, for ``__icontains``) as an oracle to recover a value the
        response body never reveals. In strict mode such a parameter never gets
        this far — :meth:`_refuse_unknown_parameters` answers it with the same
        ``400`` a non-existent field gets. With strict mode off it is dropped
        here, as if never sent. The hidden set is :func:`_hidden_filter_fields`.
        """
        kwargs = super().get_filterset_kwargs(request, queryset, view)
        hidden = _hidden_filter_fields(request.user, view._get_model_class())
        if not hidden:
            return kwargs

        data = kwargs["data"].copy()
        for key in list(data.keys()):
            if _param_field(key) in hidden:
                del data[key]
        kwargs["data"] = data
        return kwargs

    @staticmethod
    def _model_class(view) -> type[django_models.Model] | None:
        get_model = getattr(view, "_get_model_class", None)
        return get_model() if get_model is not None else None

    def get_filterset_class(self, view, queryset=None):
        model_class = self._model_class(view)
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
    configured = get_setting("SNAPADMIN_API_FILTER_BACKEND", None)
    if configured is None:
        return [SnapAdminFilterBackend, SearchFilter, OrderingFilter]
    if isinstance(configured, (str, type)):
        configured = [configured]
    return [
        import_string(backend) if isinstance(backend, str) else backend for backend in configured
    ]
