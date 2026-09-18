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
        # es_auto_mapping = True    # a mirror with no mapping indexes ids only
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
    ``snapadmin.es``
        The Elasticsearch integration's model-layer pieces — ``EsStorageMode``,
        ``EsManager``, ``EsQuerySet``, ``SnapEsUnavailable`` — split out of
        ``snapadmin.models`` (#SIMPL1f) for size, and re-exported from there
        unchanged: ``from snapadmin.models import EsManager`` keeps working, and
        this is the module to read when working on the ES integration itself.
    ``snapadmin.jobs``
        The resumable background-job models — ``SnapJobBase``, ``SnapExportJob``,
        ``SnapReindexJob``, ``SnapImportJob`` — split out of ``snapadmin.models``
        (#SIMPL1f) the same way, and re-exported from there unchanged.
    ``snapadmin.admin_gen``
        The generated-admin machinery — ``get_admin_fields()``, ``get_admin_media()``,
        ``register_admin()``, ``register_all_admins()`` — split out of
        ``snapadmin.models`` (#SIMPL1f) into ``AdminGenMixin``, which ``SnapModel``
        inherits from. Every method stays reachable at ``SnapModel.<name>``
        unchanged; this is the module to read or extend the generator itself.
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
    ``snapadmin.pagination``
        ``EstimatedCountPaginator`` — swaps a huge unfiltered changelist's exact
        ``COUNT(*)`` for PostgreSQL's instant planner estimate above
        ``SNAPADMIN_ESTIMATED_COUNT_THRESHOLD``; falls back to an exact count
        everywhere that isn't safe. Wired into every generated admin.

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
    ``snapadmin.api.exceptions``
        Makes a Django ``ValidationError`` raised on a write path answer ``400``
        naming the field instead of escaping as a 500. Always on for SnapAdmin's
        own endpoints; ``snap_exception_handler`` is the drop-in DRF
        ``EXCEPTION_HANDLER`` for a project's own views.
    ``snapadmin.limits``
        A cache-backed quota primitive — ``reserve(key, windows, concurrency)``
        for per-tenant/per-token limits across several time windows at once
        plus a concurrency cap, and ``cooldown(key, seconds)`` for backing off
        after an upstream 429. No opinion about what ``key`` means, so it
        guards an inbound endpoint and an outbound client call alike.
    ``snapadmin.api.exports`` · ``snapadmin.exporting``
        Async row exports; pluggable sources via ``SNAPADMIN_EXPORT_SOURCES``.
    ``snapadmin.importing``
        CSV/NDJSON import — the write-side counterpart, mirroring the export
        job's architecture in reverse (``SnapImportJob``, chunked crash-safe
        resume, an NDJSON report). Backs ``manage.py snapadmin_import``.
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
        Retention (``SNAPADMIN_BACKUP_KEEP``) applies per part. With
        ``SNAPADMIN_SHARDING`` enabled, the ``"db"`` part becomes one
        ``"db.<shard_name>"`` part per shard's primary (never a replica),
        each independently checksummed and retention-pruned — an unsharded
        project's bundle is unaffected.
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
    ``snapadmin.middleware``
        ``SnapErrorMonitorMiddleware`` — add it to ``MIDDLEWARE`` to feed
        unhandled request exceptions into ``snapadmin.monitoring``.
    ``snapadmin.reindexing`` · ``snapadmin.etl`` · ``snapadmin.db``
        Elasticsearch reindexing, ETL helpers, and a single-alias read-replica
        router (``SNAPADMIN_ANALYTICS_DB_ALIAS``) for SnapAdmin's own
        auto-generated read views — a narrower, simpler sibling of
        ``snapadmin.sharding`` below, independent of it.
    ``snapadmin.sharding``
        Optional, declarative multi-shard/read-replica database routing —
        entirely inert unless ``SNAPADMIN_SHARDING = {"ENABLED": True, ...}``.
        ``registration`` parses the setting (an auto-distributed flat
        ``DATABASES`` list, or an explicit ``SHARDS`` mapping) and injects the
        DSNs into ``settings.DATABASES``; ``router.SnapAdminRouter``
        (registered automatically) resolves a query's shard by
        ``modulo``/``hash``/``range``/a ``CUSTOM_ROUTER_FUNC``, fails writes
        over to a live replica when the primary is down, and selects a read
        replica by ``random``/``round_robin``/``first_available``, falling
        back to (or, with ``HA_SETTINGS['FALLBACK_TO_PRIMARY'] = False``,
        refusing in favour of) the primary once every replica is down;
        ``health`` is the cached TCP-reachability probe behind that decision.
        ``state``/``decorators`` give ``snap_master_only()``/
        ``snap_target(shard=..., replica=...)`` — ``contextvars``-based (so
        async-safe), usable as a context manager or a decorator on a sync or
        ``async def`` function alike. A model opts in with ``shard_key`` (a
        ``SnapModel`` attribute or ``snap_model()`` keyword, resolved through
        ``get_model_meta`` exactly like ``tenant_scoped``) — no model,
        including Django's own ``auth``/``sessions``/``admin`` tables, is ever
        routed without asking. ``ids.uuid7()`` is an opt-in, stdlib-only
        RFC 9562 time-ordered UUID helper for a model that wants a
        collision-free primary key across shards. Backs
        ``manage.py snap_migrate`` and the sharded branch of
        ``manage.py snapadmin_db_backup`` — both touch every shard's
        **primary** only, never a replica.
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
    ``snapadmin.tenancy``
        Row-level multi-tenancy: a model opts in with ``tenant_scoped = True``
        plus a tenant column (``tenant_field()``), and every generated
        surface — admin, REST, GraphQL, Elasticsearch routing, exports,
        imports, the offline cache — then requires a bound tenant
        (``use_tenant()``, or ``SnapTenantMiddleware`` per request via
        ``SNAPADMIN_TENANT_RESOLVER``) to see or write any row; with none
        bound, every read returns empty and every write is refused —
        default-deny, never "every row". ``use_all_tenants()`` is the one
        explicit, audited bypass, reserved for background code whose job is
        inherently cross-tenant (the retention purge, the Elasticsearch
        reindex). Isolation is *logical*, not physical — see ``SECURITY.md``.
    ``snapadmin.conf``
        The single accessor for every ``SNAPADMIN_*`` setting:
        ``get_setting(name, default)`` resolves an explicit Django setting,
        then the active ``SNAPADMIN_PROFILE`` preset (``admin`` / ``api`` /
        ``full``), then the built-in default — collapsing "99 settings to
        configure" to one line for a new project without changing behaviour
        for an install that already sets things explicitly. Also the one place
        the API surfaces' default lives (``REST_API_ENABLED_DEFAULT`` /
        ``GRAPHQL_ENABLED_DEFAULT``, both ``False`` since 1.0): every read site
        imports it rather than spelling it out, because the 1.0 flip first
        landed in ``urls.py`` alone and left ten other sites reporting an API
        that was no longer mounted. Each profile likewise states its values
        outright instead of mirroring the defaults — that mirroring is what
        made ``SNAPADMIN_PROFILE = "api"`` invert to "API off" at 1.0 — so
        ``"full"`` and an unset profile are no longer the same thing.
    ``snapadmin.checks``
        Django system checks — warnings ``snapadmin.W001``…``W018`` and errors
        ``snapadmin.E001``…``E019`` catch misconfiguration at startup, so read
        them before debugging behaviour. The masking checks are *errors* because
        a mistyped rule fails open: it masks nothing and says nothing.
        ``E013``–``E016`` cover ``SNAPADMIN_SHARDING``: an unresolvable DSN or
        shard shape, an unrecognised ``STRATEGY``/``REPLICA_SELECTION`` or a
        ``'custom'`` strategy with no importable ``CUSTOM_ROUTER_FUNC``, and a
        ``'range'`` strategy with a shard missing its ``RANGE`` or two
        overlapping ranges.
        ``E007`` is the backup ``.env``-without-encryption refusal: ``env`` in
        ``SNAPADMIN_BACKUP_INCLUDE`` with no ``SNAPADMIN_BACKUP_AGE_RECIPIENTS``
        configured fails closed rather than shipping plaintext secrets. ``W021``
        is its advisory counterpart for the dump itself — a destination that
        leaves the host (``network``/``remote``/``sftp``/``s3``) active with no
        recipients configured; a warning, not an error, because the transport or
        the destination may encrypt where settings cannot see. ``W022`` flags an
        absolute ``SNAPADMIN_BACKUP_SFTP_DIR``, which is read relative to the SSH
        login directory (unless the login directory is that path). ``W023`` is
        backups configured while ``SNAPADMIN_BACKUP_ENABLED`` is off, ``W024``
        an ``env`` part with no file behind ``SNAPADMIN_BACKUP_ENV_FILE``,
        ``W025`` a model with masked fields behind a hand-written admin that
        does not mask, ``W026`` the deprecated ``admin_sections``. ``E027`` is
        ``SnapModel``'s ``EsManager`` and a project's own ``objects`` manager
        replacing each other silently — a mixin's scoping manager hidden, or a
        ``tenant_scoped`` model's ``EsManager`` hidden. ``E008``
        catches a ``@snap_action`` whose declared HTTP methods conflict with its
        own model's ``api_read_only``/``api_http_method_names`` policy — dead
        configuration that would otherwise always answer ``403``. ``E011``/``E012``
        are the GDPR subject-access declaration: a registered model that never
        declares ``subject_path`` (or ``None``) at all, or one that declares a
        malformed path — see ``manage.py snapadmin_subject_request`` below.
        ``W015`` catches a registered model whose generated admin form would
        render with no fields at all (no ``show_in_form=True`` anywhere),
        whatever the cause — see ``SNAPADMIN_SHOW_IN_FORM_DEFAULT`` below.
        ``E009`` catches a **declared but unenforceable** ``tenant_scoped``
        (``snapadmin.tenancy``): the resolved tenant field does not exist on
        the model, or the model was registered via ``@snap_model`` rather
        than subclassing ``SnapModel`` — tenant scoping is enforced through
        ``SnapModel``'s ``EsManager``, which a plain registered model never
        uses as its default manager. It does not demand every registered
        model declare tenant scoping — most legitimately should not.
    ``snapadmin.crypto``
        Streaming AGE encryption for backup artefacts — two backends
        (``pyrage``, the optional ``[age]`` extra; or the ``age`` command-line
        tool) behind one ``encrypt_stream``/``decrypt_stream`` interface. Used
        by ``snapadmin.backup`` when ``SNAPADMIN_BACKUP_AGE_RECIPIENTS`` is set.
        ``generate_keypair()`` mints a fresh identity/recipient pair through
        either backend — ``manage.py snapadmin_age_keygen`` is the CLI in
        front of it, writing the private key straight to a git-ignored
        ``.age/`` directory instead of ever printing it.
    ``snapadmin.encryption``
        Field-level encryption — ciphertext at rest in the database, ordinary
        Python values in application code. ``snapadmin.encryption.keys`` resolves
        the ``SNAPADMIN_ENCRYPTION`` keyset from a ``KEY_PROVIDER`` (KMS/Vault),
        a mounted ``KEY_FILE``, the ``SNAPADMIN_ENCRYPTION_KEYS`` environment
        variable or the settings dict — first hit wins, sources are never
        merged — and guarantees that no key is ever rendered into a log, a
        ``repr`` or an exception (only its id and fingerprint). Keys are
        generated with ``manage.py snapadmin_encryption_key``; the keyset is
        ordered, so the first key encrypts and every key decrypts, which is what
        makes rotation possible without downtime. ``snapadmin.encryption.cipher``
        is the cipher itself: AES-256-GCM over the versioned
        ``snap1.<key id>.<nonce>.<payload>`` envelope, with the
        ``app.model.field`` AAD that stops a ciphertext being moved between
        columns, behind the lazily-imported ``[encryption]`` extra
        (``cryptography``). Nothing here runs, and no dependency is imported,
        until a model declares an encrypted field.
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
    ``snapadmin_import``, ``snapadmin_audit_export``, ``snapadmin_health_alert``,
    ``snapadmin_db_backup``, ``snapadmin_purge_expired_data``, ``snapadmin_send_error_digest``,
    ``snapadmin_restore``, ``snapadmin_rollback``, ``snapadmin_subject_request``,
    ``snapadmin_encryption_key``, ``snapadmin_encrypt_fields``, ``snap_migrate``,
    ``snapadmin_age_keygen``.
    ``snapadmin_restore``/``snapadmin_rollback`` are dry-run by
    default — pass ``--confirm`` to actually restore or roll back.
    ``snap_migrate`` runs ``migrate`` against every ``SNAPADMIN_SHARDING`` shard's
    primary (sequentially, or all at once with ``--parallel``), never a replica.
    ``snapadmin_age_keygen`` generates an AGE keypair for
    ``SNAPADMIN_BACKUP_AGE_RECIPIENTS``, writes the private key to a git-ignored
    ``.age/`` directory and prints only the public recipient.
    ``snapadmin_encryption_key`` generates a field-encryption key and prints it
    once, as the environment line to paste into a secret store — never into a
    settings module. ``--rotate`` prints a key to prepend to the existing keyset
    and the ids already in it, never their material.
    ``snapadmin_encrypt_fields`` converts stored data: ``--adopt`` encrypts rows
    that were already in a column when it was switched to an encrypted field,
    ``--rotate`` moves rows off an older key so it can be dropped from the
    keyset, and ``--reindex`` rebuilds ``<field>_bi`` blind-index columns (the
    repair after a ``bulk_update()`` or ``QuerySet.update()``, neither of which
    refreshes them). It reports only unless given ``--apply``, walks by primary
    key so a killed run resumes with ``--start-pk``, and counts a row it cannot
    convert instead of stopping on it.
    ``snapadmin_subject_request export|delete --model app.Model --identifier VALUE
    --user USERNAME`` is the GDPR subject-access command — export (unmasked,
    reusing the existing ``SnapExportJob`` machinery) or delete (dry-run by
    default, ``--confirm`` to actually delete) everything reachable from one
    data subject via every registered model's ``subject_path``. ``--user`` must
    hold ``snapadmin.view_raw_pii``.

Settings
--------
Everything is namespaced ``SNAPADMIN_*`` and every feature is off-by-default
unless noted. The families: ``SNAPADMIN_REST_API_*`` / ``SNAPADMIN_API_*``
(REST surface, throttling, pagination, guards, and
``SNAPADMIN_API_ACTION_PERMISSIONS`` for a project's own viewset actions), ``SNAPADMIN_GRAPHQL_*``,
``SNAPADMIN_SWAGGER_ENABLED``, ``SNAPADMIN_TOKEN_ADMIN_ENABLED`` (whether the
API-token admin is offered; unset follows REST/GraphQL), ``SNAPADMIN_ES_*`` (Elasticsearch routing and
fallback), ``SNAPADMIN_BACKUP_*``, ``SNAPADMIN_RESTORE_SNAPSHOT_*``
(the pre-restore safety net), ``SNAPADMIN_ERROR_*`` and
``SNAPADMIN_HEALTH_ALERT_*`` / ``SNAPADMIN_ALERT_*`` (monitoring and alert
delivery), ``SNAPADMIN_PURGE_EXTERNAL`` (an external scheduler runs the
retention purge), ``SNAPADMIN_AUDIT_*`` and
``SNAPADMIN_MASKED_FIELDS`` / ``SNAPADMIN_MASKING_RULES`` (audit and PII),
``SNAPADMIN_ENCRYPTION`` (one dict: the field-encryption keyset and where it is
read from — ``KEY_PROVIDER``, ``KEY_FILE``, ``KEYS``, ``STRICT``; the
``SNAPADMIN_ENCRYPTION_KEYS`` and ``SNAPADMIN_ENCRYPTION_KEY_FILE`` environment
variables configure the same thing without touching settings),
``SNAPADMIN_SHARDING`` (one dict: multi-shard/read-replica routing —
``ENABLED``, ``STRATEGY``, ``SHARD_KEY``, ``REPLICA_SELECTION``,
``HA_SETTINGS``, and either an auto-distributed flat ``DATABASES`` list or an
explicit ``SHARDS`` mapping — unset or ``ENABLED: False`` is a complete
no-op),
``SNAPADMIN_EXPORT_*``,
``SNAPADMIN_SSO_*``, plus layout keys (``SNAPADMIN_URL_PREFIX``,
``SNAPADMIN_APP_LABELS``, ``SNAPADMIN_HIDDEN_APPS``, ``SNAPADMIN_NESTED_APPS``,
``SNAPADMIN_THEME_AUTH_ADMIN``, ``SNAPADMIN_SHOW_IN_FORM_DEFAULT`` — raises
every ``Snap*Field``'s ``show_in_form`` default project-wide; an explicit
per-field value still wins).
The full reference with defaults is the "Environment Variables Reference"
section of the documentation.

Optional extras
---------------
The base install carries only permissive licences (MIT/BSD/Apache) and is safe
for commercial use. ``pip install django-snapadmin[<extra>]``:
``api`` (DRF, drf-spectacular, django-filter — needed once
``SNAPADMIN_REST_API_ENABLED`` / ``SNAPADMIN_SWAGGER_ENABLED`` is on; both default
to off since 1.0), ``graphql`` (graphene-django — ``SNAPADMIN_GRAPHQL_ENABLED``,
also off by default, independent of ``api``),
``theme`` (Unfold UI), ``elasticsearch``, ``celery``, ``backup`` (SFTP),
``age`` (pyrage, for encrypted backups — ``SNAPADMIN_BACKUP_AGE_RECIPIENTS``),
``s3`` (boto3, for S3-compatible offsite backups — ``SNAPADMIN_BACKUP_S3_*``),
``extra-settings``, ``wysiwyg`` (CKEditor 5 — GPL/commercial, hence optional),
``autocomplete-filter``, ``xlsx`` (openpyxl, for ``export_format="xlsx"``),
``encryption`` (cryptography, for ``SnapEncrypted*Field`` columns), or
``all`` (reproduces today's full dependency graph — a no-op upgrade for an
existing install). Each is imported lazily and raises a pointed
``ImproperlyConfigured`` only when its feature is actually used.

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
    "SnapEncryptedCharField": "snapadmin.fields",
    "SnapEncryptedTextField": "snapadmin.fields",
    "SnapEncryptedEmailField": "snapadmin.fields",
    "SnapEncryptedJSONField": "snapadmin.fields",
    "SnapEncryptedIntegerField": "snapadmin.fields",
    "SnapEncryptedDecimalField": "snapadmin.fields",
    "SnapEncryptedDateField": "snapadmin.fields",
    "SnapEncryptedDateTimeField": "snapadmin.fields",
    "SnapBlindIndexField": "snapadmin.fields",
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
