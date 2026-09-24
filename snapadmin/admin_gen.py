"""
snapadmin/admin_gen.py

The generated-admin machinery: reading a ``SnapModel`` subclass's Snap field
flags into Django admin fieldsets/list columns/filters (``get_admin_fields``),
the JS/CSS asset lists the generated ``ModelAdmin.Media`` is built from
(``get_admin_media``), building and registering that ``ModelAdmin``
(``register_admin``), and the app-wide sweep that calls it for every
registered model (``register_all_admins``).

Split out of ``snapadmin.models`` (#SIMPL1f) into a mixin, ``AdminGenMixin``,
that ``SnapModel`` inherits from — every method keeps living at
``SnapModel.<name>`` exactly as before (``from snapadmin.models import
SnapModel; SnapModel.get_admin_fields`` is unaffected), this is purely where
the implementation lives now.

**Imports `snapadmin.models` only inside method bodies, never at module
level.** ``snapadmin.models`` imports :class:`AdminGenMixin` from here to
build ``SnapModel``, so a module-level import back would be circular; by the
time any of these methods actually *runs* (admin autodiscover, at the
earliest), both modules have finished loading, so a function-local import
costs nothing but a dict lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.exceptions import AlreadyRegistered
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django.utils.safestring import mark_safe

from snapadmin.conf import get_setting
from snapadmin.fields import SnapFieldAttributeEnum
from snapadmin.pagination import EstimatedCountPaginator
from snapadmin.registry import get_model_meta, is_registered
from snapadmin.sanitize import sanitize_html
from snapadmin import fields as snapfields

# Unfold imports — mirrors the same detect-or-fall-back block every
# admin-facing SnapAdmin module carries (snapadmin.admin has its own
# independent copy for the same reason: neither module can import the other's
# without a cycle, so each detects Unfold for itself).
try:
    if "unfold" not in settings.INSTALLED_APPS:
        raise ImportError(
            "Unfold not in INSTALLED_APPS"
        )  # pragma: no cover - module-level branch, resolved at import

    from unfold.admin import ModelAdmin
    from unfold.contrib.filters.admin import (
        RangeDateFilter,
        RangeNumericFilter,
        TextFilter,
        RelatedDropdownFilter,
        ChoicesDropdownFilter,
    )
    from unfold.decorators import display as unfold_display

    UNFOLD_INSTALLED = True
except (
    ImportError,
    RuntimeError,
):  # pragma: no cover - the Unfold-absent half; covered by tests/test_unfold_optional.py re-importing this module
    from django.contrib.admin import ModelAdmin

    RangeDateFilter = admin.DateFieldListFilter
    RangeNumericFilter = admin.AllValuesFieldListFilter
    TextFilter = admin.AllValuesFieldListFilter
    RelatedDropdownFilter = admin.RelatedFieldListFilter
    ChoicesDropdownFilter = admin.ChoicesFieldListFilter

    def unfold_display(description=None, header=False, label=False, **kwargs):
        def decorator(func):
            if description:
                func.short_description = description
            return func

        return decorator

    UNFOLD_INSTALLED = False


def _wysiwyg_widget():
    """Return a CKEditor 5 widget for wysiwyg fields, importing it lazily.

    ``django-ckeditor-5`` bundles CKEditor 5 (a GPL / commercial editor), so it is
    an **optional** dependency — only projects that actually use wysiwyg fields
    need it. Importing it here (rather than at module load) lets SnapModels load
    without it installed; the clear error only fires if a wysiwyg field is used.
    """
    try:
        from django_ckeditor_5.widgets import CKEditor5Widget
    except ImportError as exc:
        raise ImproperlyConfigured(
            "A SnapModel field sets wysiwyg=True, which needs the CKEditor 5 "
            "rich-text editor. Install the optional extra "
            "`pip install django-snapadmin[wysiwyg]`, add 'django_ckeditor_5' to "
            "INSTALLED_APPS and define CKEDITOR_5_CONFIGS['extends']."
        ) from exc
    return CKEditor5Widget(config_name="extends")


def _any_offline_capable_model() -> bool:
    """Whether any Snap-registered model has ``offline_mode = True`` (#JS2e).

    ``connectivity.js`` is only worth loading when at least one model actually
    has an offline layer for it to drive — otherwise it is a health poll and a
    save-blocking guard with nothing behind them. Reads ``get_model_meta`` so a
    plain ``@snap_model``-decorated model (which accepts ``offline_mode`` as a
    decorator keyword rather than a class attribute) counts too.
    """
    return any(
        get_model_meta(model, "offline_mode", False)
        for model in apps.get_models()
        if is_registered(model)
    )


def _token_admin_enabled() -> bool:
    """Whether :meth:`AdminGenMixin.register_all_admins` offers the API-token admin.

    ``SNAPADMIN_TOKEN_ADMIN_ENABLED`` decides when set. Left unset (``None``) it
    follows the APIs that accept tokens — the REST API and GraphQL — so an
    install with both off is not handed a screen for minting credentials to a
    surface that does not exist (#EXT2h). A project authenticating its own
    views with :class:`~snapadmin.api.authentication.APITokenAuthentication`
    sets it to ``True``.
    """
    from snapadmin.conf import GRAPHQL_ENABLED_DEFAULT, REST_API_ENABLED_DEFAULT

    explicit = get_setting("SNAPADMIN_TOKEN_ADMIN_ENABLED", None)
    if explicit is not None:
        return bool(explicit)
    return bool(
        get_setting("SNAPADMIN_REST_API_ENABLED", REST_API_ENABLED_DEFAULT)
        or get_setting("SNAPADMIN_GRAPHQL_ENABLED", GRAPHQL_ENABLED_DEFAULT)
    )


def _pk_list_column(model: type[models.Model]) -> tuple[str, bool]:
    """``(name, shown)`` for the changelist's primary-key column (#EXT2g).

    The column is referenced by the key's real name — a model whose key is not
    called ``id`` used to get a column that does not exist. Shown first for an
    integer key, hidden for any other key type (a UUID is noise in a list),
    unless the model's ``admin_list_display_pk`` says otherwise. An abstract
    model has no key until a concrete subclass gets its automatic ``id``, so it
    is treated as exactly that.
    """
    pk = model._meta.pk
    name = pk.name if pk is not None else "id"
    override = getattr(model, "admin_list_display_pk", None)
    if override is not None:
        return name, bool(override)
    return name, pk is None or isinstance(pk, models.IntegerField)


def _list_filter_for(fields: dict) -> list:
    """The ``list_filter`` entries for every ``filterable=True`` field.

    Each type gets the widget that can actually express its range: a date or a
    number gets a range filter, a foreign key a searchable dropdown, a field
    with choices a choice dropdown, everything else Django's default.
    """
    filters: list = []
    for field_name, field in fields.items():
        if not getattr(field, SnapFieldAttributeEnum.FILTERABLE.value, False):
            continue
        if isinstance(field, (models.DateField, models.DateTimeField, models.TimeField)):
            filters.append((field_name, RangeDateFilter))
        elif isinstance(field, (models.IntegerField, models.FloatField, models.DecimalField)):
            filters.append((field_name, RangeNumericFilter))
        elif isinstance(field, models.ForeignKey):
            filters.append((field_name, RelatedDropdownFilter))
        elif isinstance(field, models.CharField) and field.choices:
            filters.append((field_name, ChoicesDropdownFilter))
        else:
            filters.append(field_name)
    return filters


def _wysiwyg_display(model, field_name: str):
    """A changelist column that renders one rich-text field as HTML, safely."""
    field_obj = model._meta.get_field(field_name)

    @unfold_display(description=field_obj.verbose_name)
    def _display(self, obj):
        raw = getattr(obj, field_name, "") or ""
        # Wysiwyg values are attacker-controllable HTML. Sanitize
        # before mark_safe to prevent stored XSS in the changelist,
        # unless the field explicitly trusts its content.
        if getattr(field_obj, "safe_html", False):
            return mark_safe(raw)  # noqa: S308 - safe_html=True: the developer vouches for it
        return mark_safe(sanitize_html(raw))  # noqa: S308 - sanitized on the line itself

    return _display


def _function_field_display(field):
    """A changelist column that renders one :class:`SnapFunctionField`."""

    @unfold_display(
        description=getattr(field, "verbose_name", "") or getattr(field, "name", ""),
        header=True,
    )
    def _display(self, obj):
        val = field.get_display_value(obj)
        if UNFOLD_INSTALLED:
            return [val, None, None]
        return val

    return _display


def _group_fields_by_row(model, field_names: list[str]) -> list:
    """Fold fields that share a ``row=`` name into one Django fieldset row."""
    grouped: list = []
    rows: dict[str, list[str]] = {}
    for name in field_names:
        try:
            row_name = getattr(model._meta.get_field(name), "row", None)
        except FieldDoesNotExist:
            row_name = None
        if not row_name:
            grouped.append(name)
            continue
        if row_name not in rows:
            rows[row_name] = []
            grouped.append(rows[row_name])
        rows[row_name].append(name)
    return [tuple(item) if isinstance(item, list) else item for item in grouped]


def _fieldsets_for(model, form_fields: list[str]) -> list:
    """The form's fieldsets: one per ``tab=`` name, plus the untabbed fields."""
    tabs: dict[str, list[str]] = {}
    untabbed: list[str] = []
    for name in form_fields:
        try:
            tab_name = getattr(model._meta.get_field(name), "tab", None)
        except FieldDoesNotExist:
            tab_name = None
        if tab_name:
            tabs.setdefault(tab_name, []).append(name)
        else:
            untabbed.append(name)

    fieldsets: list = []
    if untabbed:
        fieldsets.append((None, {"fields": _group_fields_by_row(model, untabbed)}))
    for tab_name, names in tabs.items():
        fieldsets.append(
            (tab_name, {"fields": _group_fields_by_row(model, names), "classes": ("tab",)})
        )
    return fieldsets


def _drop_masked_fields(fieldsets, masked: set[str]):
    """The same fieldsets without the fields this viewer may not see raw.

    A masked field is shown starred on the changelist; on the change form there
    is no starred equivalent — an input holds the raw value — so it is removed
    from the form entirely for a viewer without PII access.
    """
    filtered = []
    for name, options in fieldsets:
        fields = []
        for entry in options.get("fields", []):
            if isinstance(entry, tuple):
                kept = tuple(item for item in entry if item not in masked)
                if kept:
                    fields.append(kept if len(kept) > 1 else kept[0])
            elif entry not in masked:
                fields.append(entry)
        filtered.append((name, {**options, "fields": fields}))
    return filtered


def _mark_row_fieldsets(fieldsets):
    """Tag every fieldset holding a multi-field row for Unfold's row layout."""
    for _name, options in fieldsets:
        if any(isinstance(entry, tuple) for entry in options.get("fields", [])):
            classes = list(options.get("classes", []))
            if "snap-field-row" not in classes:
                classes.append("snap-field-row")
            options["classes"] = tuple(classes)
    return fieldsets


class AdminGenMixin:
    """``SnapModel``'s generated-admin methods — see the module docstring."""

    if TYPE_CHECKING:  # pragma: no cover - typing only
        # What a model mixing this in provides. Declared rather than inherited:
        # the mixin is deliberately not a Model (SnapModel supplies the model
        # half), but every attribute below is read by the methods here, so
        # stating the contract is what makes those reads checkable.
        _meta: Any
        __name__: str
        admin_enabled: bool
        admin_mixins: list[type]
        admin_overrides: dict[str, Any]
        admin_tabs: list[dict[str, Any]]
        compressed_fields: bool
        css_admin_files: str | list[str]
        js_admin_files: str | list[str]
        list_filter_submit: bool
        list_max_show_all: int
        list_per_page: int
        offline_mode: bool
        show_full_result_count: bool
        snap_inlines: list[type]
        warn_unsaved_form: bool

    @classmethod
    def get_admin_fields(cls):
        from snapadmin.models import AdminFieldSets

        meta_fields = {
            f.name: f
            for f in cls._meta.get_fields()
            if hasattr(f, "name") and not (f.one_to_many or f.one_to_one or f.many_to_many)
        }
        meta_fields_related = {
            f.name: f
            for f in cls._meta.get_fields()
            if hasattr(f, "name") and (f.many_to_one or f.many_to_many)
        }
        attr_fields = {fn: fo for fn, fo in cls.__dict__.items()}

        form_fields = [
            fn
            for fn, fo in meta_fields.items()
            if getattr(fo, SnapFieldAttributeEnum.SHOW_IN_FORM.value, None)
        ]
        list_display = [
            fn
            for fn, fo in meta_fields.items()
            if getattr(fo, SnapFieldAttributeEnum.SHOW_IN_LIST.value, True)
        ]
        search_fields = [
            fn
            for fn, fo in meta_fields.items()
            if getattr(fo, SnapFieldAttributeEnum.SEARCHABLE.value, False)
        ]
        pk_name, show_pk = _pk_list_column(cls)
        if pk_name not in search_fields:
            search_fields.append(pk_name)

        all_fields_for_readonly = {**meta_fields, **meta_fields_related}
        editable_fields = [
            fn
            for fn, fo in all_fields_for_readonly.items()
            if not getattr(fo, SnapFieldAttributeEnum.EDITABLE.value, False)
        ]
        updatable_fields = [
            fn
            for fn, fo in all_fields_for_readonly.items()
            if not getattr(fo, SnapFieldAttributeEnum.UPDATABLE.value, True)
        ]

        def dynamic_get_readonly_fields(self, request, obj=None):
            return [
                fn
                for fn, fo in all_fields_for_readonly.items()
                if fn in editable_fields or (fn in updatable_fields and obj and obj.pk)
            ]

        # Generated callables (this one, the wysiwyg safe_html_<field> displays
        # below, and the SnapFunctionField displays further down) are stashed
        # here rather than written into cls.admin_overrides. admin_overrides is
        # the project's own dict; register_admin() merges this stash into
        # admin_attrs first and admin_overrides last, so a project override
        # always wins by construction instead of by who wrote into the shared
        # dict first (#ADM2a). Rebuilt from scratch on every call, so a re-run
        # (e.g. after a field's safe_html flag changes) always reflects the
        # current field state.
        generated_overrides = {"get_readonly_fields": dynamic_get_readonly_fields}

        list_filter = _list_filter_for(meta_fields)

        autocomplete_fields = [
            fn
            for fn, fo in meta_fields_related.items()
            if getattr(fo, SnapFieldAttributeEnum.AUTOCOMPLETE.value, True)
        ]

        # A rich-text column renders HTML, so it is shown through a generated,
        # sanitizing display method rather than as the raw field.
        for field_name, field in meta_fields.items():
            if getattr(field, "wysiwyg", False) and field_name in list_display:
                method_name = f"safe_html_{field_name}"
                generated_overrides[method_name] = _wysiwyg_display(cls, field_name)
                list_display[list_display.index(field_name)] = method_name

        for attr_name, attr_value in attr_fields.items():
            if isinstance(attr_value, snapfields.SnapFunctionField):
                method_name = f"SnapFunctionField{attr_name.capitalize()}"
                generated_overrides[method_name] = _function_field_display(attr_value)
                list_display.append(method_name)

        if pk_name in list_display:
            list_display.remove(pk_name)
        if show_pk:
            list_display.insert(0, pk_name)
        cls._admin_generated_overrides = generated_overrides
        return AdminFieldSets(
            form_fields, list_display, search_fields, list_filter, autocomplete_fields
        )

    @classmethod
    def get_admin_media(cls) -> tuple[list[str], list[str]]:
        """The ``(js, css)`` asset lists ``register_admin()`` builds the admin's
        ``Media`` class from — theme-sheet selection, the ``connectivity.js`` /
        ``offline.js`` gating and de-duplication with ``js_admin_files`` /
        ``css_admin_files`` all included (#ADM2c). Public so a project
        overriding ``register_admin()`` can extend the real lists instead of
        copying a snapshot that rots at the next release.
        """
        jquery_extra = "" if settings.DEBUG else ".min"
        js = [
            f"admin/js/vendor/jquery/jquery{jquery_extra}.js",
            "admin/js/jquery.init.js",
            "snapadmin/js/jquery_bridge.js",
            "snapadmin/js/select2.min.js",
            "snapadmin/js/admin.js",
        ]
        # connectivity.js is opt-in and off by default (#JS2e/DECISIONS.md D18):
        # it polls /api/health/ and, on a confirmed-down backend, shows a warning
        # toast and blocks form submits so a user does not lose what they typed.
        # An always-on client for what is an opt-in offline layer was the bug —
        # a project with SNAPADMIN_REST_API_ENABLED=False (a documented, supported
        # setting) got a health poll that 404s forever and a bricked admin. Two
        # conditions must both hold before it loads: the setting is on, and at
        # least one registered model actually has offline_mode=True — otherwise
        # there is no offline layer for it to drive. It also owns
        # window.SnapAdminToast, which offline.js borrows for its own
        # "cached / will sync" toast, so it must load before offline.js when both
        # are present; see tests/test_offline.py::TestConnectivityJsInjection.
        if get_setting("SNAPADMIN_CONNECTIVITY_ENABLED", False) and _any_offline_capable_model():
            js.append("snapadmin/js/connectivity.js")

        css = ["snapadmin/css/select2.min.css", "snapadmin/css/admin.css"]
        # The two theme layers are mutually exclusive, and that is the whole
        # scoping mechanism: neither sheet carries a theme prefix, so exactly
        # one of them must reach the page. `admin-stock.css` gives Django's
        # built-in admin a modern form layout; loading it next to a theme
        # overrides the theme's own layout instead of complementing it.
        # `admin-unfold.css` fills the few gaps Unfold leaves. Both come after
        # `admin.css` so they win the cascade over the shared cosmetics.
        css.append(
            "snapadmin/css/admin-unfold.css"
            if UNFOLD_INSTALLED
            else "snapadmin/css/admin-stock.css"
        )

        extra_js = (
            [cls.js_admin_files]
            if isinstance(cls.js_admin_files, str)
            else list(cls.js_admin_files)
        )
        extra_css = (
            [cls.css_admin_files]
            if isinstance(cls.css_admin_files, str)
            else list(cls.css_admin_files)
        )
        final_js = list(dict.fromkeys(js + extra_js))
        if cls.offline_mode:
            final_js.append("snapadmin/js/offline.js")
        final_css = list(dict.fromkeys(css + extra_css))
        return final_js, final_css

    @classmethod
    def register_admin(cls) -> None:
        """Build and register this model's ``ModelAdmin`` from its Snap field flags.

        ``admin_overrides`` is merged in last, so it always wins over every
        attribute or method the generator itself produces — including the
        callables :meth:`get_admin_fields` stashes internally, such as
        ``get_readonly_fields`` and the wysiwyg ``safe_html_<field>`` display
        methods (#ADM2a).
        """
        from snapadmin.models import (
            DjangoAdminClassAttributeEnum,
            PIIMaskingAdminMixin,
            SnapSaveMixin,
            formatted_id,
        )

        if not cls.admin_enabled:
            return
        admin_fields = cls.get_admin_fields()
        form_fields = admin_fields.form_fields
        list_display = admin_fields.list_display
        search_fields = admin_fields.search_fields
        list_filter = admin_fields.list_filter
        autocomplete_fields = admin_fields.autocomplete_fields

        fieldsets = _fieldsets_for(cls, form_fields)

        final_js, final_css = cls.get_admin_media()

        # Auto-derive list_select_related from the ForeignKey columns actually shown
        # in the list view. Rendering an FK column (or a __str__ that walks it) without
        # this issues one extra query per row — the classic admin N+1. We only join the
        # FKs that appear in list_display, so we never pull relations we won't display.
        fk_field_names = {
            f.name for f in cls._meta.get_fields() if getattr(f, "many_to_one", False)
        }
        list_select_related = [fn for fn in list_display if fn in fk_field_names]

        # An encrypted column is never sortable. Clicking a changelist header
        # issues ORDER BY on the column, which holds base64url ciphertext — the
        # rows come back in an order unrelated to their values, with nothing to
        # show anything went wrong. Ordering is the one operation that never
        # passes through the field's own lookup guard, so it has to be taken
        # away here. snapadmin.E023 covers the Meta.ordering half.
        encrypted_names = {
            f.name for f in cls._meta.get_fields() if getattr(f, "is_snap_encrypted", False)
        }
        sortable_by = [fn for fn in list_display if fn not in encrypted_names]

        A = DjangoAdminClassAttributeEnum
        admin_attrs = {
            A.LIST_DISPLAY.value: list_display,
            A.SEARCH_FIELDS.value: search_fields,
            A.LIST_FILTER.value: list_filter,
            A.AUTOCOMPLETE_FIELDS.value: autocomplete_fields,
            A.INLINES.value: cls.snap_inlines,
            # Newest-first default for the changelist. Applied here (not on the
            # base manager) so it never leaks into GROUP BY on aggregations; a
            # model's explicit Meta.ordering is honoured when set.
            "ordering": list(cls._meta.ordering) or ["-pk"],
            "sortable_by": sortable_by,
            "list_select_related": list_select_related or False,
            "list_per_page": cls.list_per_page,
            "list_max_show_all": cls.list_max_show_all,
            "show_full_result_count": cls.show_full_result_count,
            # Fast, timeout-proof changelist count on huge tables.
            # Safe by construction: only estimates unfiltered, large PostgreSQL
            # tables, exact everywhere else (see snapadmin.pagination).
            "paginator": EstimatedCountPaginator,
            "formatted_id": formatted_id,
            A.MEDIA_CLASS.value: type(
                A.MEDIA_CLASS.value,
                (),
                {A.CSS_MEDIA.value: {A.ALL_MEDIA.value: final_css}, A.JS_MEDIA.value: final_js},
            ),
        }

        if UNFOLD_INSTALLED:
            admin_attrs.update(
                {
                    "compressed_fields": cls.compressed_fields,
                    "warn_unsaved_form": cls.warn_unsaved_form,
                    "list_filter_submit": cls.list_filter_submit,
                    "tabs": cls.admin_tabs,
                }
            )

        if fieldsets:
            admin_attrs[A.FIELDSETS.value] = fieldsets
        else:
            admin_attrs[A.FIELDS.value] = form_fields

        def formfield_for_dbfield(self, db_field, request, **kwargs):
            if isinstance(db_field, (models.TextField, snapfields.SnapTextField)) and getattr(
                db_field, "wysiwyg", False
            ):
                kwargs["widget"] = _wysiwyg_widget()
            return super(ModelAdmin, self).formfield_for_dbfield(db_field, request, **kwargs)

        def get_fieldsets(self, request, obj=None):
            # If we have rows, Unfold needs specific layout classes
            fs = super(ModelAdmin, self).get_fieldsets(request, obj)

            # PII masking: drop masked fields from the change form for
            # users without PII access, so raw values never reach an editable
            # input. The changelist shows them masked (see PIIMaskingAdminMixin).
            from snapadmin.masking import get_masked_fields, user_can_view_pii

            masked = set(get_masked_fields(cls._meta.app_label, cls._meta.model_name))
            if masked and not user_can_view_pii(request.user):
                fs = _drop_masked_fields(fs, masked)
            return _mark_row_fieldsets(fs) if UNFOLD_INSTALLED else fs

        admin_attrs["formfield_for_dbfield"] = formfield_for_dbfield
        admin_attrs["get_fieldsets"] = get_fieldsets
        # Generated callables first, the project's own admin_overrides last —
        # merge order is the precedence rule (#ADM2a).
        admin_attrs.update(getattr(cls, "_admin_generated_overrides", {}))
        admin_attrs.update(getattr(cls, "admin_overrides", {}))

        # Ecosystem admin mixins come first in the MRO so their
        # behaviour (import/export, versioning, history, object perms) wraps
        # SnapAdmin's, which in turn wraps Django/Unfold's ModelAdmin.
        extra_mixins = tuple(getattr(cls, "admin_mixins", []) or [])
        parent_classes = extra_mixins + (PIIMaskingAdminMixin, SnapSaveMixin, ModelAdmin)
        # Set last, after admin_overrides, so a project cannot unset it by accident.
        # snapadmin.W015 reasons about the form *this* class would render, and the
        # registration below quietly loses to a project's own @admin.register — so
        # the check has to be able to ask the live registry which admin actually won
        # (#EXT1a). An attribute, not an isinstance test: a project may subclass the
        # generated class, and the mixins above are public and reused by hand-written
        # admins that SnapAdmin did not build.
        admin_attrs["snapadmin_generated_admin"] = True
        # Without this, type() records whichever module the metaclass machinery
        # was called from (a django.forms internal), so tracebacks and any
        # "is this admin generated?" test name a module the class is not in (#EXT2k).
        admin_attrs["__module__"] = cls.__module__
        admin_class = type(f"{cls.__name__}Admin", parent_classes, admin_attrs)
        try:
            # cls is the SnapModel this mixin is mixed into.
            admin.site.register(cast("type[models.Model]", cls), admin_class)
        except AlreadyRegistered:
            pass

    @staticmethod
    def register_all_admins(app_label: str | None = None) -> None:
        from snapadmin.admin import APITokenAdmin, ErrorEventAdmin, SnapadminAuditLogAdmin
        from snapadmin.models import APIToken, ErrorEvent, SnapadminAuditLog

        if _token_admin_enabled():
            try:
                admin.site.register(APIToken, APITokenAdmin)
            except AlreadyRegistered:
                pass
        try:
            admin.site.register(ErrorEvent, ErrorEventAdmin)
        except AlreadyRegistered:
            pass
        try:
            admin.site.register(SnapadminAuditLog, SnapadminAuditLogAdmin)
        except AlreadyRegistered:
            pass

        for model in apps.get_models():
            if not is_registered(model):
                continue
            # register_admin() is SnapModel's own generator — it reads the Snap
            # field flags to build fieldsets, filters and list columns. A plain
            # model registered with @snap_model has no such method (and no Snap
            # fields to read), so it keeps whatever admin the project wrote for
            # it by hand instead of being handed a generated one.
            if not hasattr(model, "register_admin"):
                continue
            if app_label is None or model._meta.app_label == app_label:
                model.register_admin()
