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

Models you cannot rewrite take the other route: leave them plain
``django.db.models.Model`` classes and opt in from the outside with the
``@snap_model(...)`` decorator, which registers the model and records the same
settings without touching its field layer (metadata only — see
``snapadmin.models.snap_model`` for what it deliberately does not attach).

``snapadmin`` must be in ``INSTALLED_APPS`` and ``snapadmin.urls`` included in
the root URLconf. Three console scripts help before that point: ``snapadmin-new``
generates a project you keep (the three steps above, already wired — ``migrate``
then ``runserver`` work immediately, no Docker, no manual edits), ``snapadmin-demo``
runs a throwaway demo project, and ``snapadmin-init`` inspects an existing project
read-only and prints the snippets to paste.

Module map
----------
Import paths are the public contract — modules are never moved or renamed.

Declaring models
    ``snapadmin.models``
        ``SnapModel`` (the declarative base and ``register_all_admins()``),
        ``snap_model`` (the decorator that opts a **plain** ``models.Model`` in —
        metadata and registration only: no ES manager, no ``purge_expired()``, no
        generated admin), ``snap_property`` (a method decorator for a computed,
        display-only column — the decorator form of ``SnapFunctionField``, works
        on either door), ``EsStorageMode``, ``APIToken``, ``ErrorEvent``, the ES
        manager/queryset.
    ``snapadmin.fields``
        Every ``Snap*Field``. Snap-only kwargs (``searchable``, ``filterable``,
        ``show_in_list``, ``wysiwyg``, …) drive the admin and API and are
        stripped before Django sees them, so they add no migration.
        ``snap_field(field, **kwargs)`` sets the same kwargs directly on a
        plain Django field instance — a third-party field (``django-money``,
        ``phonenumber_field``, …) or a brownfield model that cannot be
        rewritten onto the ``Snap*Field`` classes — so every reader treats it
        exactly like a ``Snap*Field``, including ``wysiwyg=True`` sanitize-on-
        write. ``required=True`` is the one kwarg that can add a migration
        (it mutates ``null``/``blank`` directly, same as a hand-built
        ``Snap*Field(required=True)``); everything else stays migration-free.
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
        ``snap_action`` (in ``api.views``) turns a model method into a
        user-defined REST action — ``POST /api/models/<app>/<Model>/<pk>/
        <name>/`` (or the list-level route with ``detail=False``) — bound by
        the model's own ``api_read_only``/``api_http_method_names`` policy and
        a derived or explicit Django permission; discoverable per model via
        ``GET /api/models/schema/``. ``api_field_permissions`` (registry
        metadata, resolved by ``get_model_meta`` like every other model-level
        setting) gates a field's very presence/writability in REST and
        GraphQL, orthogonal to PII masking — see ``snapadmin.masking``.
    ``snapadmin.api.graphql``
        The generated Graphene schema.
    ``snapadmin.api.authentication`` · ``snapadmin.sso``
        API-token auth and SSO redirect handling.
    ``snapadmin.limits``
        A cache-backed quota primitive — ``reserve(key, windows, concurrency)``
        for per-tenant/per-token limits across several time windows at once
        plus a concurrency cap, and ``cooldown(key, seconds)`` for backing off
        after an upstream 429. No opinion about what ``key`` means, so it
        guards an inbound endpoint and an outbound client call alike.
    ``snapadmin.api.exports`` · ``snapadmin.exporting``
        Async row exports; pluggable sources via ``SNAPADMIN_EXPORT_SOURCES``.
    ``snapadmin.api.users`` · ``snapadmin.api.health`` · ``snapadmin.api.reindex``
    ``snapadmin.api.offline``
        Optional endpoints, each behind its own setting.

Operations
    ``snapadmin.audit`` · ``snapadmin.masking``
        Change logging and PII masking. ``audit`` writes the append-only
        ``{field: {"old": …, "new": …}}`` diff and renders it; ``masking``
        resolves *which* fields are sensitive (``SNAPADMIN_MASKED_FIELDS``),
        *how* each is obfuscated and *who* may see it raw
        (``SNAPADMIN_MASKING_RULES``) — ``mask_field()`` is the choke point
        every masking surface goes through. ``user_can_access_field()`` is a
        related but orthogonal guard: whether a field is present/writable at
        all, driven by a model's ``api_field_permissions`` rather than a
        masking rule.
    ``snapadmin.backup``
        3-2-1 backups (local / network / SFTP / FTP / S3-compatible via the
        ``s3`` extra — AWS, MinIO, Backblaze B2, Hetzner Object Storage,
        Wasabi). Hetzner Storage Box (SFTP/SCP/WebDAV, a different product
        from Object Storage) uses the ``sftp`` destination.
        ``SNAPADMIN_BACKUP_INCLUDE``
        (default ``["db"]``) optionally bundles ``media`` and an AGE-encrypted
        ``env`` alongside the database — loose per-part files sharing one run's
        timestamp, plus an always-unencrypted ``manifest.json`` sidecar.
        Retention (``SNAPADMIN_BACKUP_KEEP``) applies per part.
    ``snapadmin.restore``
        Restoring a bundle ``snapadmin.backup`` produced — fetch from any
        configured destination, verify the manifest's per-part checksum,
        decrypt, then apply to the live database/media/``.env``. Backs
        ``manage.py snapadmin_restore``, which is dry-run by default.
    ``snapadmin.snapshot``
        The pre-restore safety net: ``snapadmin_restore --confirm`` snapshots
        the current live state of every part it is about to overwrite before
        touching anything (``SNAPADMIN_RESTORE_SNAPSHOT_DIR``,
        ``SNAPADMIN_RESTORE_SNAPSHOT_KEEP`` — its own short-lived retention,
        separate from ``SNAPADMIN_BACKUP_KEEP``). Backs
        ``manage.py snapadmin_rollback``.
    ``snapadmin.monitoring`` · ``snapadmin.health`` · ``snapadmin.alerts`` ·
    ``snapadmin.logging_config``
        Error capture and digests, health checks, structlog wiring. ``alerts``
        owns the delivery side: email plus Slack / Discord / Teams / Telegram /
        JSON webhooks (``SNAPADMIN_ALERT_WEBHOOKS``), posted with the standard
        library, failing soft so one dead channel never blocks the others.
    ``snapadmin.reindexing`` · ``snapadmin.etl`` · ``snapadmin.db``
        Elasticsearch reindexing, ETL helpers, database routing.
    ``snapadmin.tasks`` · ``snapadmin.celery_compat``
        Celery tasks and Beat schedules (``[celery]`` extra). The module imports
        without Celery: the compat shim keeps the task names and runs a task
        synchronously when called, and raises on ``.delay()`` rather than
        pretending the work was queued.
    ``snapadmin.registry``
        Which models are SnapAdmin's own, and how each one is configured.
        ``SnapModel`` subclasses register themselves as they are declared and
        ``@snap_model`` registers a plain model, so every gate is a
        ``is_registered()`` lookup instead of an ``issubclass()`` walk.
        ``get_model_meta(model, name, default)`` is the matching accessor for a
        model-level setting, resolving four tiers in order: the registry entry,
        then the class attribute, then a project-wide ``SNAPADMIN_<NAME>``
        setting, then this ``default`` argument — so both ways of declaring a
        model read identically. ``register()`` / ``meta_for()`` complete the
        surface.
    ``snapadmin.conf``
        The single accessor for every ``SNAPADMIN_*`` setting:
        ``get_setting(name, default)`` resolves an explicit Django setting,
        then the active ``SNAPADMIN_PROFILE`` preset (``admin`` / ``api`` /
        ``full``), then the built-in default — collapsing "99 settings to
        configure" to one line for a new project without changing behaviour
        for an install that already sets things explicitly.
    ``snapadmin.checks``
        Django system checks — warnings ``snapadmin.W001``…``W012`` and errors
        ``snapadmin.E001``…``E008`` catch misconfiguration at startup, so read
        them before debugging behaviour. The masking checks are *errors* because
        a mistyped rule fails open: it masks nothing and says nothing.
        ``E007`` is the backup ``.env``-without-encryption refusal: ``env`` in
        ``SNAPADMIN_BACKUP_INCLUDE`` with no ``SNAPADMIN_BACKUP_AGE_RECIPIENTS``
        configured fails closed rather than shipping plaintext secrets. ``E008``
        catches a ``@snap_action`` whose declared HTTP methods conflict with its
        own model's ``api_read_only``/``api_http_method_names`` policy — dead
        configuration that would otherwise always answer ``403``.
    ``snapadmin.crypto``
        Streaming AGE encryption for backup artefacts — two backends
        (``pyrage``, the optional ``[age]`` extra; or the ``age`` command-line
        tool) behind one ``encrypt_stream``/``decrypt_stream`` interface. Used
        by ``snapadmin.backup`` when ``SNAPADMIN_BACKUP_AGE_RECIPIENTS`` is set.
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
    ``snapadmin.scaffold`` · ``snapadmin.quickstart`` · ``snapadmin.integrate`` ·
    ``snapadmin.manage_cli``
        The console scripts: ``snapadmin-new`` (generates a project you keep — one
        worked ``SnapModel``, SQLite, ``.env``/``dist.env``; ``--full`` adds a
        Dockerfile, docker-compose.yml and the Postgres/Redis/Elasticsearch wiring;
        templates ship under ``snapadmin/scaffold/templates/`` and render with
        stdlib ``string.Template``), ``snapadmin-demo``, ``snapadmin-init``, and
        shims that forward ``snapadmin-info`` / ``snapadmin-license-check`` (either
        spelling) to the ``manage.py`` command of the same name. All stdlib-only,
        importing no Django at module level — they run before a project exists.
        ``snapadmin-demo`` stamps the tree it extracts (``snapadmin.quickstart.stamp``),
        so re-running it refreshes that tree — dropping files the new release removed —
        and ``snapadmin_info`` can report a tree left behind by an older release.

Management commands
    ``snapadmin_info``, ``snapadmin_license_check``, ``snapadmin_reindex``,
    ``snapadmin_audit_export``, ``snapadmin_health_alert``, ``snapadmin_db_backup``,
    ``snapadmin_purge_expired_data``, ``snapadmin_send_error_digest``,
    ``snapadmin_restore``, ``snapadmin_rollback``. The three GDPR/error-digest ones
    were once unprefixed (``db_backup``, ``purge_expired_data``,
    ``send_error_digest``); those names still work as deprecated aliases that print
    a rename notice. ``snapadmin_restore``/``snapadmin_rollback`` are dry-run by
    default — pass ``--confirm`` to actually restore or roll back.

Settings
--------
Everything is namespaced ``SNAPADMIN_*`` and every feature is off-by-default
unless noted. The families: ``SNAPADMIN_REST_API_*`` / ``SNAPADMIN_API_*``
(REST surface, throttling, pagination, guards), ``SNAPADMIN_GRAPHQL_*``,
``SNAPADMIN_SWAGGER_ENABLED``, ``SNAPADMIN_ES_*`` (Elasticsearch routing and
fallback), ``SNAPADMIN_BACKUP_*``, ``SNAPADMIN_RESTORE_SNAPSHOT_*``
(the pre-restore safety net), ``SNAPADMIN_ERROR_*`` and
``SNAPADMIN_HEALTH_ALERT_*`` / ``SNAPADMIN_ALERT_*`` (monitoring and alert
delivery), ``SNAPADMIN_AUDIT_*`` and
``SNAPADMIN_MASKED_FIELDS`` / ``SNAPADMIN_MASKING_RULES`` (audit and PII),
``SNAPADMIN_EXPORT_*``,
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
``age`` (pyrage, for encrypted backups — ``SNAPADMIN_BACKUP_AGE_RECIPIENTS``),
``s3`` (boto3, for S3-compatible offsite backups — ``SNAPADMIN_BACKUP_S3_*``),
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
    "snap_model": "snapadmin.models",
    "snap_property": "snapadmin.models",
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
    "snap_field": "snapadmin.fields",
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
