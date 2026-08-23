"""SnapAdmin — declarative Django admin + REST/GraphQL API package.

Define a model's fields once and get a themed Django admin, a REST API with
Swagger docs, a GraphQL endpoint and optional Elasticsearch search. Every
surface is a single settings toggle.

The most common public names are re-exported here for convenience, so
``from snapadmin import SnapModel, SnapCharField`` works alongside the original
deep paths (``from snapadmin.models import SnapModel``), which keep working
unchanged. The re-exports are **lazy** (PEP 562 ``__getattr__``): importing
``snapadmin`` — or a console-script subpackage like ``snapadmin.quickstart`` that
runs before any Django settings exist — does not import the Django-backed
``models``/``fields`` modules until one of these names is actually accessed.

Quickstart
----------
Three steps take a model from nothing to a full admin + API::

    # 1. models.py — declare the fields, with admin/API behaviour inline
    from snapadmin import fields as snap, models as snap_models

    class Product(snap_models.SnapModel):
        name      = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)
        price     = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)
        available = snap.SnapBooleanField(default=True, filterable=True)

        # Optional: mirror rows to Elasticsearch, auto-purge after a year
        # es_storage_mode = snap_models.EsStorageMode.DUAL
        # data_retention_days = 365

    # 2. settings.py — every surface is a toggle
    SNAPADMIN_REST_API_ENABLED = True
    SNAPADMIN_GRAPHQL_ENABLED = True
    SNAPADMIN_SWAGGER_ENABLED = True

    # 3. admin.py — register every SnapModel in the project
    from snapadmin.models import SnapModel
    SnapModel.register_all_admins()

``snapadmin`` must be in ``INSTALLED_APPS`` and ``snapadmin.urls`` included in
the root URLconf. Two console scripts help before that point:
``snapadmin-demo`` runs a throwaway demo project, ``snapadmin-init`` inspects an
existing project read-only and prints the snippets to paste.

Module map
----------
Import paths are the public contract — modules are never moved or renamed.

Declaring models
    ``snapadmin.models``
        ``SnapModel`` (the declarative base and ``register_all_admins()``),
        ``EsStorageMode``, ``APIToken``, ``ErrorEvent``, the ES manager/queryset.
    ``snapadmin.fields``
        Every ``Snap*Field``. Snap-only kwargs (``searchable``, ``filterable``,
        ``show_in_list``, ``masked``, …) drive the admin and API and are
        stripped before Django sees them, so they add no migration.
    ``snapadmin.validators``
        ``deconstructible`` validators: phone, colour, file type/size.

Admin surface
    ``snapadmin.admin``
        Admin base classes and the auto-registration machinery. Uses Unfold's
        themed classes when the ``[theme]`` extra is installed, stock Django
        admin otherwise.
    ``snapadmin.widgets`` · ``snapadmin.nesting`` · ``snapadmin.sanitize``
        Form widgets, nested-app grouping in the sidebar, HTML sanitization for
        wysiwyg values.
    ``snapadmin.auth_admin`` · ``snapadmin.extra_settings_admin``
        Re-register third-party/built-in admins with the Unfold theme so they
        match the rest of the site: Django's ``User``/``Group``
        (``SNAPADMIN_THEME_AUTH_ADMIN``, on by default — without it Unfold's
        password-hash template renders the password row empty) and
        django-extra-settings' ``Setting``. No-ops without the ``[theme]`` extra,
        and neither ever replaces an admin class a project customised itself.
    ``snapadmin.views`` · ``snapadmin.urls``
        The system dashboard and the URLconf to ``include()``.

APIs
    ``snapadmin.api.views`` · ``snapadmin.api.serializers`` · ``snapadmin.api.filters``
        The generated REST surface (per-model CRUD, filtering, pagination).
    ``snapadmin.api.graphql``
        The generated Graphene schema.
    ``snapadmin.api.authentication`` · ``snapadmin.sso``
        API-token auth and SSO redirect handling.
    ``snapadmin.api.exports`` · ``snapadmin.exporting``
        Async row exports; pluggable sources via ``SNAPADMIN_EXPORT_SOURCES``.
    ``snapadmin.api.users`` · ``snapadmin.api.health`` · ``snapadmin.api.reindex``
    ``snapadmin.api.offline``
        Optional endpoints, each behind its own setting.

Operations
    ``snapadmin.audit`` · ``snapadmin.masking``
        Change logging and PII masking.
    ``snapadmin.backup``
        3-2-1 backups (local / network / SFTP / FTP).
    ``snapadmin.monitoring`` · ``snapadmin.health`` · ``snapadmin.logging_config``
        Error capture and digests, health checks, structlog wiring.
    ``snapadmin.reindexing`` · ``snapadmin.etl`` · ``snapadmin.db``
        Elasticsearch reindexing, ETL helpers, database routing.
    ``snapadmin.tasks`` · ``snapadmin.celery_compat``
        Celery tasks and Beat schedules (``[celery]`` extra). The module imports
        without Celery: the compat shim keeps the task names and runs a task
        synchronously when called, and raises on ``.delay()`` rather than
        pretending the work was queued.
    ``snapadmin.registry``
        Which models are SnapAdmin's own. ``SnapModel`` subclasses register
        themselves as they are declared, so every gate is a lookup instead of an
        ``issubclass()`` walk. Internal seam — no public API of its own.
    ``snapadmin.checks``
        Django system checks — ``snapadmin.W001``…``W007`` catch misconfiguration
        at startup, so read them before debugging behaviour.
    ``snapadmin.theme_i18n``
        Catalog entries for the Unfold theme's own interface strings, which
        ``django-unfold`` ships untranslated — without them a themed admin renders
        its shell in English around a translated page.

Tooling
    ``snapadmin.diagnostics``
        Collectors behind ``manage.py snapadmin_info`` — runtime, database, API,
        Elasticsearch and the feature-adoption inventory.
    ``snapadmin.licensing``
        Dependency-licence data behind ``manage.py snapadmin_license_check``.
    ``snapadmin.quickstart`` · ``snapadmin.integrate`` · ``snapadmin.manage_cli``
        The console scripts: ``snapadmin-demo``, ``snapadmin-init``, and shims that
        forward ``snapadmin-info`` / ``snapadmin-license-check`` (either spelling) to
        the ``manage.py`` command of the same name. All stdlib-only, importing no
        Django at module level — they run before a project exists.
        ``snapadmin-demo`` stamps the tree it extracts (``snapadmin.quickstart.stamp``),
        so re-running it refreshes that tree — dropping files the new release removed —
        and ``snapadmin_info`` can report a tree left behind by an older release.

Management commands
    ``snapadmin_info``, ``snapadmin_license_check``, ``snapadmin_reindex``,
    ``snapadmin_audit_export``, ``snapadmin_health_alert``, ``snapadmin_db_backup``,
    ``snapadmin_purge_expired_data``, ``snapadmin_send_error_digest``. The last
    three were once unprefixed (``db_backup``, ``purge_expired_data``,
    ``send_error_digest``); those names still work as deprecated aliases that print
    a rename notice.

Settings
--------
Everything is namespaced ``SNAPADMIN_*`` and every feature is off-by-default
unless noted. The families: ``SNAPADMIN_REST_API_*`` / ``SNAPADMIN_API_*``
(REST surface, throttling, pagination, guards), ``SNAPADMIN_GRAPHQL_*``,
``SNAPADMIN_SWAGGER_ENABLED``, ``SNAPADMIN_ES_*`` (Elasticsearch routing and
fallback), ``SNAPADMIN_BACKUP_*``, ``SNAPADMIN_ERROR_*`` and
``SNAPADMIN_HEALTH_ALERT_*`` (monitoring and alerts), ``SNAPADMIN_AUDIT_*`` and
``SNAPADMIN_MASKED_FIELDS`` (audit and PII), ``SNAPADMIN_EXPORT_*``,
``SNAPADMIN_SSO_*``, plus layout keys (``SNAPADMIN_URL_PREFIX``,
``SNAPADMIN_APP_LABELS``, ``SNAPADMIN_HIDDEN_APPS``, ``SNAPADMIN_NESTED_APPS``,
``SNAPADMIN_THEME_AUTH_ADMIN``).
The full reference with defaults is the "Environment Variables Reference"
section of the documentation.

Optional extras
---------------
The base install carries only permissive licences (MIT/BSD/Apache) and is safe
for commercial use. ``pip install django-snapadmin[<extra>]``:
``theme`` (Unfold UI), ``elasticsearch``, ``celery``, ``backup`` (SFTP),
``extra-settings``, ``wysiwyg`` (CKEditor 5 — GPL/commercial, hence optional),
``autocomplete-filter``, ``xlsx`` (openpyxl, for ``export_format="xlsx"``), or
``all``. Each is imported lazily and raises a pointed ``ImproperlyConfigured``
only when its feature is actually used.

Further reading
---------------
Full docs: https://drofji.github.io/django-snapadmin/ — and
https://drofji.github.io/django-snapadmin/llms.txt for a machine-readable map
of it.
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    #: Resolved from the installed distribution's metadata so it always matches
    #: the packaged version (``pyproject.toml``) without a second source of truth.
    __version__ = _pkg_version("django-snapadmin")
except PackageNotFoundError:  # running from a source checkout, not pip-installed
    __version__ = "0.0.0.dev0"

# name -> defining module. Kept as data so the imports stay lazy (see __getattr__).
_LAZY_EXPORTS: dict[str, str] = {
    # Core model API + enums/exceptions (snapadmin.models)
    "SnapModel": "snapadmin.models",
    "EsStorageMode": "snapadmin.models",
    "APIToken": "snapadmin.models",
    "SnapEsUnavailable": "snapadmin.models",
    "SnapPurgeError": "snapadmin.models",
    # Field types (snapadmin.fields)
    "SnapField": "snapadmin.fields",
    "SnapCharField": "snapadmin.fields",
    "SnapTextField": "snapadmin.fields",
    "SnapEmailField": "snapadmin.fields",
    "SnapSlugField": "snapadmin.fields",
    "SnapURLField": "snapadmin.fields",
    "SnapUUIDField": "snapadmin.fields",
    "SnapIntegerField": "snapadmin.fields",
    "SnapPositiveIntegerField": "snapadmin.fields",
    "SnapPositiveSmallIntegerField": "snapadmin.fields",
    "SnapPositiveBigIntegerField": "snapadmin.fields",
    "SnapSmallIntegerField": "snapadmin.fields",
    "SnapBigIntegerField": "snapadmin.fields",
    "SnapFloatField": "snapadmin.fields",
    "SnapDecimalField": "snapadmin.fields",
    "SnapDateField": "snapadmin.fields",
    "SnapDateTimeField": "snapadmin.fields",
    "SnapTimeField": "snapadmin.fields",
    "SnapDurationField": "snapadmin.fields",
    "SnapFileField": "snapadmin.fields",
    "SnapImageField": "snapadmin.fields",
    "SnapBooleanField": "snapadmin.fields",
    "SnapJSONField": "snapadmin.fields",
    "SnapGenericIPAddressField": "snapadmin.fields",
    "SnapForeignKey": "snapadmin.fields",
    "SnapOneToOneField": "snapadmin.fields",
    "SnapManyToManyField": "snapadmin.fields",
    "SnapRichTextField": "snapadmin.fields",
    "SnapPhoneField": "snapadmin.fields",
    "SnapColorField": "snapadmin.fields",
    "SnapFunctionField": "snapadmin.fields",
    "SnapStatusBadgeField": "snapadmin.fields",
    "SnapStatusBadgeFieldChoice": "snapadmin.fields",
    # Validators (snapadmin.validators)
    "SnapPhoneValidator": "snapadmin.validators",
    "SnapColorValidator": "snapadmin.validators",
    "SnapFileValidator": "snapadmin.validators",
}

__all__ = ["__version__", *sorted(_LAZY_EXPORTS)]


def __getattr__(name: str):
    """Lazily resolve a blessed re-export the first time it is accessed."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
