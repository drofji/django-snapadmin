# Changelog

All notable changes to `django-snapadmin` are documented here.

---

## [0.1.0a9] — 2026-07-05 — Ninth Alpha

Clears the rest of the enterprise backlog: audit trail, async export, large-dataset pagination, full
i18n, WCAG accessibility, config checks, migration guide and ecosystem compatibility. Closes issues
#1, #2, #3, #5, #6, #7, #8 and #9. **Still a pre-release** — APIs may change before `0.1.0` stable.

### Added
- **Ecosystem compatibility** (`#1`) — new `admin_mixins` attribute on `SnapModel` composes third-party
  admin mixins (`ImportExportModelAdmin`, `reversion.VersionAdmin`, `SimpleHistoryAdmin`, guardian's
  `GuardedModelAdmin`, `MPTTModelAdmin`, …) **on top of** SnapAdmin's auto-generated config instead of
  replacing it (placed first in the MRO). Documented the full compatibility story — SnapAdmin only
  auto-registers `SnapModel`s and never clobbers an existing admin — in new `COMPATIBILITY.md` with a
  per-package matrix (mptt / guardian / reversion / debug-toolbar / import-export / simple-history /
  django-filter / taggit). `admin_enabled = False` remains the full hand-off escape hatch.
- **Internationalisation (i18n)** (`#9`) — SnapAdmin's UI strings are wrapped in `gettext` and ship
  compiled catalogs for **10 locales**: English, Russian, German, Swiss German (`de_CH`, ß→ss),
  French, Swiss French (`fr_CH`), Spanish, Italian, Polish and Dutch. A minimal, accessible
  **language switcher** (`snapadmin/language_switcher.html`, backed by Django's `set_language`) lets
  managers change locale on the fly; missing strings **fall back to English** automatically. Sandbox
  wires `LocaleMiddleware`, `LANGUAGES` and `LOCALE_PATHS`.
- **Accessibility — WCAG 2.1 AA / EAA** (`#8`) — SnapAdmin's own UI is now screen-reader and
  keyboard friendly: a "skip to main content" link, ARIA landmarks (`banner` / `main` / `nav` /
  `footer`), accessible names on icon-only controls, decorative icons marked `aria-hidden`, heading
  semantics on section titles, `scope="col"` on table headers, a text alternative for the dashboard
  chart, a visible keyboard focus ring, and `rel="noopener"` on new-tab links. The SSO login partial
  is a labelled `role="group"`. (Admin changelist/form tables inherit Django + Unfold's own AA-level
  accessibility.)
- **Startup configuration checks** (`#2`) — Django system checks (`manage.py check`, `runserver`,
  CI) validate the SnapAdmin settings and surface typos early with an actionable hint instead of a
  silent no-op or a deep runtime error: unknown `SNAPADMIN_ANALYTICS_DB_ALIAS` (`W001`), a
  `SNAPADMIN_MASKED_FIELDS` key/field that doesn't resolve (`E001`/`E002`), a `SNAPADMIN_NESTED_APPS`
  target app that isn't installed (`W002`), and an SSO provider with no usable URL (`W003`). New
  module `snapadmin.checks`.
- **Migration guide** (`#3`) — new repo-root `MIGRATING.md` (linked from the README) with a step-by-step
  upgrade **checklist** from the legacy `drofji-automatically-django-admin` / `drofji-snapadmin` to
  `django-snapadmin`: package rename, `SnapModel` base, `Snap*` field types, `SNAPADMIN_*` settings, and
  the `admin.py` boilerplate that auto-registration replaces. (The docs site already carried a migration
  section; this adds the canonical, shippable checklist.)
- **Async background export** (`#6`) — `POST /api/exports/` enqueues a Celery job
  (`api.tasks.run_export`) that streams a SnapModel's rows to CSV or JSON in resumable chunks. Poll
  `GET /api/exports/<id>/` for status + progress (rows processed/total, percent, ETA seconds); cancel
  with `POST …/cancel/`; download the finished file with `GET …/download/`. **Fault-tolerant** — the
  writer resumes from the last persisted chunk if the worker restarts (`acks_late`); **cancellable**
  between chunks. New modules `snapadmin.exporting` + `snapadmin.api.exports`, model `SnapExportJob`
  (migration `0006`), settings `SNAPADMIN_EXPORT_ENABLED` / `_CHUNK_SIZE` / `_DIR`.
- **Fast approximate-count pagination** (`#5`) — `EstimatedCountPaginator` replaces the changelist's
  expensive `COUNT(*)` with PostgreSQL's `reltuples` planner estimate on **unfiltered** listings of
  tables past `SNAPADMIN_ESTIMATED_COUNT_THRESHOLD` (100 000), so multi-million-row admin pages stop
  timing out. Exact count everywhere it isn't safe (other DBs, filtered queries, small tables). Wired
  into every generated admin; kill-switch `SNAPADMIN_ESTIMATED_COUNT`. (FK columns are already
  auto-`select_related`'d in admin + API, and the API auto-`prefetch_related`s M2M.) New module
  `snapadmin.pagination`.
- **Unalterable audit trail** (`#7` — DORA / ISO 27001) — every admin create/update/delete is
  recorded as an immutable `SnapadminAuditLog`: **who** (actor FK + text snapshot, IP, User-Agent),
  **what** (content type + object repr + before/after field diff) and **when** (tz-aware timestamp).
  Rows are append-only (`save`/`delete` raise once persisted) and the admin is fully read-only.
  New module `snapadmin.audit`; new setting `SNAPADMIN_AUDIT_LOG_ENABLED` (default `True`) +
  `SNAPADMIN_AUDIT_RETENTION_DAYS` (default `365`). SIEM export via
  `manage.py snapadmin_audit_export` (JSON lines / CSV, time/action/app/model filters, `--purge`).
  Migration `0005`.

---

## [0.1.0a8] — 2026-07-05 — Eighth Alpha

Enterprise configuration pass: four settings-driven features that fit SnapAdmin's zero-boilerplate
philosophy — each inert on stock single-database installs until you opt in. Closes issues #15, #13,
#12 and #4/#16. **Still a pre-release** — APIs may change before `0.1.0` stable.

### Added
- **Read-replica routing** (`#15`) — `SNAPADMIN_ANALYTICS_DB_ALIAS` pins auto-generated read-only
  API list/retrieve querysets to a `DATABASES` replica via `.using()`. Writes (POST/PUT/PATCH/DELETE)
  and the object lookups behind them always stay on `default`, so replication lag can never stale or
  drop a mutation. Empty/unknown alias = no routing. New module `snapadmin.db`.
- **PII data masking** (`#12`) — `SNAPADMIN_MASKED_FIELDS` (`{"app.Model": ["email", …]}`) obfuscates
  sensitive fields in the admin changelist, hides them from the admin change form, and masks them in
  REST API responses for anyone lacking the new `snapadmin.view_raw_pii` permission (superusers see
  raw). New module `snapadmin.masking`; new permission via migration `0004`.
- **SSO/OAuth2 login helper** (`#13`) — `SNAPADMIN_SSO_PROVIDERS` renders corporate login buttons on
  the admin login page (context processor `snapadmin.sso.sso_providers` + includable
  `snapadmin/sso_buttons.html`) and exposes them at public `GET /api/sso-providers/` for headless
  frontends. A presentation wrapper only — no new auth dependency. New module `snapadmin.sso`.
- **Admin-index nesting** (`#4` / `#16`) — `SNAPADMIN_NESTED_APPS` folds auto-generated sections under
  existing app groups; `SNAPADMIN_HIDDEN_APPS` hides groups; `SNAPADMIN_APP_LABELS` renames headings.
  Wired by wrapping `admin.site.get_app_list` at startup — no custom `AdminSite`. New module
  `snapadmin.nesting`.

### Notes
- Upgrading runs migration `0004_alter_apitoken_options`, which registers the `view_raw_pii`
  permission for the `snapadmin` app. No data changes.
- Fixed a long-standing stale version string on the dashboard (`0.1.0a2` → the real version).

## [0.1.0a7] — 2026-07-04 — Seventh Alpha

Ops & docs pass: a secure SSH/SFTP offsite backup transport, hands-off PyPI publishing, richer
PyPI project links, and a documentation split that separates the installable package from the
bundled demo plus a new guide on extending SnapAdmin. **Still a pre-release** — APIs may change
before `0.1.0` stable.

### Added
- **SFTP offsite backups** — new `sftp` backup destination ships the encrypted dump over
  SSH/SFTP (`SNAPADMIN_BACKUP_SFTP_*`) with password or SSH-key auth, its own frequency
  (`_SFTP_EVERY_HOURS`) and the same retention pruning as the other destinations. Requires the
  optional `paramiko` dependency: `pip install django-snapadmin[backup]`. Use instead of, or
  alongside, the plain FTP `remote` destination.
- **Automated PyPI publishing** — `.github/workflows/publish.yml` builds and uploads every
  `v*` tag via PyPI Trusted Publishing (OIDC, no stored token); see `CONTRIBUTING.md`.

### Changed
- PyPI project links now include **Documentation** (https://drofji.github.io/django-snapadmin/),
  **Issues** and **Changelog** alongside Homepage/Repository.

### Docs
- README opens with a **package vs. demo** orientation that separates what you install
  (`django-snapadmin`) from the repo-only demo, and labels demo-derived screens.
- New **Extending & Overriding** section (README + docs site): custom `SnapField` types, reusing
  the built-in validators, extending a `SnapModel`, adding/overriding REST endpoints, config-only
  auth/permission/ES-client swaps, and shadowing admin templates.

---

## [0.1.0a6] — 2026-07-04 — Sixth Alpha

Integrator feedback pass: remove the hard-coded integration points that forced a
downstream project to write bridge code. Pluggable auth, custom user model, configurable
ES client, bulk-reindex command, generic ETL helper and an optional user-management API.
**Still a pre-release** — APIs may change before `0.1.0` stable.

### Added
- **Pluggable API authentication** — `SNAPADMIN_API_AUTHENTICATION_CLASSES` (dotted paths,
  like DRF's own setting) drives the model CRUD, schema and token endpoints. Unset =
  SnapAdmin token auth only (unchanged default). Enables JWT / session / custom auth without
  subclassing the views (`snapadmin.api.authentication.SnapAPIAuthMixin`,
  `get_api_authentication_classes()`)
- **Django-permission fallback** — `TokenModelPermission` now allows non-`APIToken` auth
  (session, JWT) through plain Django model permissions instead of hard-rejecting; the token
  path keeps its stricter `allowed_models` scope
- **Custom `AUTH_USER_MODEL` support** — `APIToken.user` targets `settings.AUTH_USER_MODEL`
  (no more hard-coded `auth.User`); usernames read via `get_username()` throughout
- **Configurable Elasticsearch client** — `ELASTICSEARCH_KWARGS` merged into
  `Elasticsearch(...)` (api_key, TLS, retries, `request_timeout` override) and
  `SNAPADMIN_ES_CLIENT_FACTORY` (dotted path to a zero-arg client factory) for full control;
  the previously hard-coded 5s timeout is now the overridable default
- **`manage.py snapadmin_reindex`** — bulk-reindex every ES-enabled `SnapModel` (or one
  `--model app.Model`, with `--chunk-size`) out of the box
- **Generic ETL helper** — `snapadmin.etl.upsert_from_source(model, rows, unique_fields=…)`
  streams an external source into a model via `bulk_create(update_conflicts=True)`:
  idempotent upsert, no per-row saves or ES writes, one bulk reindex at the end
  (demo: `ExchangeRate` model + `manage.py sync_exchange_rates`)
- **Optional user-management API** — `SNAPADMIN_USER_API_ENABLED` mounts admin-only
  `/api/users/` (CRUD + `set-password` + `permissions`) and `/api/permissions/` for building
  frontend admin panels
- **Docs** — JWT integration recipe; many more DUAL / ES_ONLY / DB_ONLY query examples
  (object counts with & without filters, optimal bulk-export / pagination patterns)

### Changed
- **`SnapCharField` follows the `required` flag** like every other Snap field:
  `required=False` (default) now yields `null=True` / `blank=True` (was hard-coded
  `null=False`), for data parity with the other field types and mirrored ES documents.
  `required=True` still yields `null=False`. **Behaviour change** — run `makemigrations`

### Fixed
- **Stable migrations for `required=True` fields** — Snap fields now force the resolved
  `null`/`blank` into `deconstruct()`, so the migration autodetector's deconstruct→reconstruct
  clone no longer drops the `required` flag and silently reverts a mandatory column to nullable

---

## [0.1.0a5] — 2026-07-03 — Fifth Alpha

Optional email error monitoring (spike alerts + daily grouped digest), 3-2-1 database
backups (local / network / offsite FTP, each on its own schedule), a
backward-compatibility contract test suite that pins the public API surface, and a
simpler PyPI description. **Still a pre-release** — APIs may change before `0.1.0` stable.

### Added
- **3-2-1 database backups** (`snapadmin.backup`) — gzip dumps (SQLite file copy /
  `pg_dump` for PostgreSQL) shipped to three configurable destinations, each with its
  **own frequency**: a **local** directory on the same server
  (`SNAPADMIN_BACKUP_LOCAL_DIR`, default every 24 h), a **network** server on the same
  network via a mounted NFS/SMB share (`SNAPADMIN_BACKUP_NETWORK_DIR`, every 24 h) and
  an **offsite remote** server via FTP/FTPS (`SNAPADMIN_BACKUP_FTP_*`, weekly).
  Per-destination retention (`SNAPADMIN_BACKUP_KEEP`, prunes FTP too), persistent
  last-run state, failure isolation (one dead destination never cancels the others, and
  is retried on the next pass). Run via the `api.tasks.run_db_backups` Celery Beat task
  (hourly due-check) or `manage.py db_backup [--force | --destination local|network|remote]`
  from cron. Strictly opt-in (`SNAPADMIN_BACKUP_ENABLED = False` by default)
- **Error monitoring & email alerts** — opt-in
  `snapadmin.middleware.SnapErrorMonitorMiddleware` records every unhandled exception
  and 5xx response as an `ErrorEvent` (new model, migration `0003`), browsable in the
  admin under **Error Events** (read-only, searchable, filterable)
- **Spike alert email** — when `SNAPADMIN_ERROR_ALERT_THRESHOLD` errors (default 20)
  occur within `SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES` (default 15), one email goes to
  `SNAPADMIN_ERROR_ALERT_EMAILS`; an atomic cache cooldown guarantees at most one alert
  per window — no inbox floods
- **Daily grouped error digest** — identical errors merged by exception class +
  endpoint, most frequent first, capped at `SNAPADMIN_ERROR_DIGEST_MAX_GROUPS`
  (default 20) so the email never explodes; send time is configurable
  (`api.tasks.send_error_digest` via Celery Beat, or the new
  `manage.py send_error_digest [--hours N]` command from cron); recipients via
  `SNAPADMIN_ERROR_DIGEST_EMAILS` (falls back to the alert list)
- **Automatic retention** — `ErrorEvent` rows older than
  `SNAPADMIN_ERROR_RETENTION_DAYS` (default 30) are purged by the digest task
- **Public-contract test suite** (`tests/test_public_contract.py`) — pins importable
  names, `SnapModel` attribute defaults, method signatures, settings names and URL
  names so future releases can't break PyPI users silently
- Demo/sandbox: `/demo/error/` showcase view (DEBUG-only), env-driven `EMAIL_*` +
  `SNAPADMIN_ERROR_*` settings, `send-error-digest` Beat schedule
  (`SNAPADMIN_ERROR_DIGEST_HOUR`/`_MINUTE`), Error Events sidebar entry

### Changed
- PyPI short description simplified: “Define your Django models once — instantly get an
  admin panel, REST API, GraphQL and full-text search. Zero boilerplate.”
- Monitoring is fail-safe by design: storage/SMTP failures are logged via structlog
  (`error_monitor_record_failed`, `error_monitor_alert_failed`) and never break the
  request; with empty recipient lists no email is ever attempted

---

## [0.1.0a4] — 2026-07-03 — Fourth Alpha

Security hardening (GraphQL is no longer open to anonymous callers), API field privacy,
and a big Elasticsearch capability pass: automatic mappings, custom analyzers, bulk
re-indexing, mapping-aware searches and visible ES errors. **Still a pre-release** —
APIs may change before `0.1.0` stable.

### Security
- **GraphQL now enforces authentication + permissions** (previously every SnapModel was
  readable anonymously at `/api/graphql/`). Each resolver requires an authenticated
  caller — admin session or the same `Authorization: Token` header REST uses — holding
  the model's Django `view` permission; a token's `allowed_models` scope applies on top.
  Opt out (not recommended) with `SNAPADMIN_GRAPHQL_REQUIRE_AUTH = False`
- **GraphiQL playground follows `DEBUG`** by default (`SNAPADMIN_GRAPHIQL_ENABLED`)
- **`api_exclude_fields`** on `SnapModel` — listed columns never leave the server via
  the REST serializer (reads *and* writes), GraphQL type or `/api/models/schema/`;
  the admin still shows them (demo: `AuditLog.user_email`)
- Health endpoint: anonymous probes get overall status only; the per-service breakdown
  (DB/ES online state) is reserved for authenticated callers
- Sandbox/demo hardening: DRF throttling defaults (`SNAPADMIN_THROTTLE_ANON/_USER`),
  refuse to boot with a placeholder `SECRET_KEY` when `DEBUG=False`, docker-compose
  binds Postgres/Redis/ES/Kibana ports to `127.0.0.1`, `seed_demo` refuses the
  `admin/admin` default outside `DEBUG` (`SNAPADMIN_SEED_ADMIN_PASSWORD`)

### Added
- **`es_auto_mapping`** — derive the ES index mapping from model fields automatically:
  `Char`/`Text` → `text` + `.raw` keyword subfield, `Email`/`Slug`/`URL`/`UUID`/`IP`/
  `File` → `keyword`, integers & FK → `long`, `Float` → `double`, `Decimal` →
  `scaled_float`, dates → `date`, `JSONField` → `object`; `es_mapping` entries override
  per field (demo: `SearchLog`)
- **`es_index_settings`** — index-level ES settings (custom analyzers under `analysis`,
  shards, replicas) applied at index creation (demo: `Product`)
- **GraphQL list arguments**: `search` (ES-routed for DUAL/ES_ONLY via `snap_search`),
  `first`, `offset`
- **Bulk re-indexing**: `es_reindex_all()` streams rows through `helpers.bulk`
  (one round-trip per `chunk_size=500` docs, flat memory) and reports partial errors
- **`SNAPADMIN_QUERY_BACKEND_HEADER`** setting to suppress `X-Snap-Query-Backend` in
  production; the header now also reports the **actual** backend — `database` when ES
  failed mid-request and the internal DB fallback answered
- ES failures that were silently swallowed are now logged as structlog `warning` events:
  `es_ensure_index_failed`, `es_index_document_failed`, `es_delete_document_failed`,
  `es_search_failed` (with the chosen fallback), `es_purge_delete_failed`,
  `es_purge_query_failed`, `es_pk_existence_check_failed`, `es_queryset_delete_failed`,
  `es_get_failed`, `graphql_model_skipped`

### Changed
- Full-text ES queries (`es_search()` and the routed REST `?search=`) now target only the
  **text-capable fields** of the effective mapping (fallback `["*"]`) and run with
  `lenient: true`, so mappings that mix numeric/date/boolean fields can no longer break
  a search
- `snapadmin/` at **100% line coverage** (659 tests)

### Upgrade notes
- GraphQL clients must now authenticate (same tokens as REST). To restore the old open
  behavior — e.g. on a fully private network — set `SNAPADMIN_GRAPHQL_REQUIRE_AUTH = False`

---

## [0.1.0a3] — 2026-07-03 — Third Alpha

Smart Elasticsearch query routing for the REST API, a working `?search=` parameter,
and a documentation pass focused on real request examples. **Still a pre-release** —
APIs may change before `0.1.0` stable.

### Added
- **Smart ES query routing**: full-text `?search=` REST requests on `DUAL`-storage
  models (data mirrored in Elasticsearch) are executed on ES automatically — fuzzy,
  typo-tolerant, relevance-ranked — with no change to the URL or client code. Filters
  and pagination still apply on top of the ES-ranked result. Toggle globally via
  `SNAPADMIN_ES_QUERY_ROUTING` (default `True`) or per model via `es_query_routing`
- **`?search=` now works on the REST API**: search fields are derived from the model's
  `searchable=True` Snap fields (the same set the admin search box uses); `DB_ONLY`
  models search via SQL `icontains`, `ES_ONLY` models pass the term into the ES query
- **`X-Snap-Query-Backend` response header** (`elasticsearch` | `database`) on every
  list response, so API consumers can verify where a query ran
- **`SNAPADMIN_ES_SEARCH_LIMIT`** setting (default `1000`) — max hits fetched from ES
  per routed search / ES_ONLY listing (replaces a hardcoded limit)
- README: "REST API in Practice" section with copy-paste `curl` examples — CRUD,
  auto-generated filters, and ES-routed search with the routing decision matrix
- Sandbox: all feature toggles (`SNAPADMIN_REST_API_ENABLED` / `_SWAGGER_` / `_GRAPHQL_`,
  `SNAPADMIN_ES_QUERY_ROUTING`, `SNAPADMIN_ES_SEARCH_LIMIT`) are now env-driven and
  documented in `dist.env`

### Changed
- **`DUAL` models no longer round-trip through ES for plain listings**: list requests
  without `?search=` are served straight from the database with native pagination —
  previously every DUAL/ES_ONLY listing ran an ES `match_all` capped at 1000 rows,
  breaking pagination beyond that
- PyPI/README project description rewritten around the developer workflow

### Fixed
- `ES_ONLY` model list endpoints no longer crash when combined with the auto-generated
  field filters (`SnapAdminFilterBackend` now passes `EsQuerySet` through instead of
  asserting a real `QuerySet`)

---

## [0.1.0a2] — 2026-06-27 — Second Alpha

Hardening, new field types, infrastructure and a documentation overhaul on top of the
first alpha. **Still a pre-release** — APIs may change before `0.1.0` stable.

### Security
- **API tokens are hashed at rest.** `APIToken` no longer stores the raw key: it keeps a
  non-secret `token_prefix` (first 8 chars) and a unique SHA-256 `token_digest`. The raw
  `token_key` is returned exactly once — in the `POST /api/tokens/` response and a one-time
  admin message — and is `None` on every subsequent read. Authentication looks tokens up by
  digest. (Migration `0002_hash_api_token_key` backfills existing keys, then drops the
  plaintext column.)
- Offline data endpoint (`/api/offline-data/<app>/<model>/`) now enforces staff + per-model
  view permission instead of returning rows to any authenticated user

### Added
- New field types: `SnapSmallIntegerField`, `SnapPositiveSmallIntegerField`,
  `SnapPositiveBigIntegerField`, `SnapRichTextField`, `SnapPhoneField`, `SnapColorField`
  (with `SnapPhoneValidator` / `SnapColorValidator`)
- **GDPR data retention**: `data_retention_days` / `data_retention_field` on `SnapModel`
  and a storage-aware `purge_expired()` — DB_ONLY deletes rows, **DUAL also clears the
  Elasticsearch mirror**, **ES_ONLY purges via a range `delete_by_query`**; plus the
  `purge_expired_data` Celery task and management command (`--dry-run`)
- **Offline mode**: `offline_mode` / `offline_cache_limit` per model — IndexedDB caching,
  real backend health checks, connectivity toasts, saved-objects panel, reconnect sync
- **Large-dataset performance**: auto `list_select_related` (FK N+1 fix), `list_per_page`
  / `list_max_show_all` / `show_full_result_count` knobs; `seed_large` +
  `benchmark_list_view` commands
- Theming split: theme-agnostic `admin.css` + opt-in `admin-unfold.css`
- Optional extras: `[elasticsearch]`, `[celery]`, `[all]`; `django-filter` auto Swagger filters
- Demo: `Showcase` (every field type), `CustomerProfile` (`SnapOneToOneField`),
  `AuditLog` (retention), `SearchLog` (ES_ONLY)

### Fixed
- **Infinite migrations** from `SnapColorField` / `SnapPhoneField` / `SnapFileField`: they
  re-injected their built-in validator on every `deconstruct()`, so `makemigrations` never
  converged. `deconstruct()` now strips the auto-injected validator (user validators kept)
- GDPR purge no longer leaves Elasticsearch copies behind for DUAL/ES_ONLY models
- `ES_ONLY` primary keys now drawn from the full 63-bit space via `secrets` (with an
  ES existence re-roll) instead of a 6-digit random int prone to silent overwrites
- GraphQL schema generation (fields now collected at class-creation time)
- `FieldDoesNotExist` import for Django 6.0; deduped object-history log entries
- Docker `command` quoting; Celery startup; structlog usage in tasks
- Demo ES toggle (`ELASTICSEARCH_ENABLED` / `_URL`) is now actually read by `sandbox/settings.py`
- Docs: corrected `es_index_fields` → `es_mapping`; renamed DSGVO → GDPR throughout

### Changed
- `allowed_models` documented precisely: an **empty list is not "unrestricted"** — it means
  "any model the owning user already has Django perms for" (AND-ed with `user.has_perm`); a
  non-empty list narrows access further
- Docs site redesigned (dark/light theme, sidebar filter, copy buttons, `es_search` examples);
  added an `APIToken` security section and a migration guide from the retired `drofji_autoadmin`
- `snapadmin/` package at **100% line coverage** (603 tests)

---

## [0.1.0a1] — 2026-06-23 — First Alpha Release

Initial public alpha of **SnapAdmin** — a declarative Django admin and API package.

### Features

#### Declarative Admin
- `SnapModel` base class: inherit once, get automatic admin registration with a smart `__str__`
- `SnapField` mixin for every Django field type — control `list_display`, `search_fields`, `list_filter` directly on the field definition
- `show_in_list`, `show_in_form`, `searchable`, `filterable`, `editable`, `updatable` flags on every field
- `row` attribute: group fields into horizontal rows in the change form
- `tab` attribute: place fields into named Unfold tabs
- `wysiwyg` attribute: enable CKEditor 5 for `TextField` / `SnapTextField`
- `SnapFunctionField`: computed display columns with optional safe HTML output
- `SnapStatusBadgeField`: colour-coded pill badges for choice/status fields

#### Unfold Integration (optional)
- Native `django-unfold` support: compressed fields, unsaved-form warning, submit filter button, tabs
- Graceful fallback to Django's built-in `ModelAdmin` when Unfold is not installed

#### Change Logging
- `SnapSaveMixin`: automatic field-level change tracking (`old → new`) via Django's `LogEntry`
- Tracks inline changes in related formsets

#### REST API
- Auto-generated CRUD endpoints for every `SnapModel` subclass — zero extra code
- `GET/POST /api/models/{app}/{Model}/` — list and create
- `GET/PUT/PATCH/DELETE /api/models/{app}/{Model}/{id}/` — detail, update, delete
- `GET /api/models/schema/` — introspect all available endpoints and fields
- Toggle via `SNAPADMIN_REST_API_ENABLED` setting

#### GraphQL API
- Dynamic schema generation via Graphene-Django for all `SnapModel` subclasses
- Single-object and list resolvers auto-created per model
- Accessible at `/api/graphql/` with GraphiQL enabled
- Toggle via `SNAPADMIN_GRAPHQL_ENABLED` setting

#### API Documentation
- Interactive Swagger UI at `/api/docs/` via `drf-spectacular`
- ReDoc at `/api/redoc/`
- Toggle via `SNAPADMIN_SWAGGER_ENABLED` setting

#### Token Authentication
- `APIToken` model: named tokens with expiry, per-model access control, and active/inactive toggle
- `Authorization: Token <key>` header authentication for all API endpoints
- `APIToken.create_for_user()` helper for programmatic token creation
- Built-in Celery task `purge_expired_tokens` to clean up expired tokens

#### Elasticsearch Integration
- Three storage modes per model: `DB_ONLY` (default), `DUAL`, `ES_ONLY`
- `es_search(query_string, limit)` — fuzzy full-text search via ES with DB fallback
- `es_reindex_all()` — bulk re-index all records
- Auto index and mapping management on `post_migrate`
- Toggle globally via `ELASTICSEARCH_ENABLED` setting

#### Structured Logging
- `configure_logging(log_level, json_logs)` — call once in `settings.py`
- Colourised human-readable output for development; JSON lines for production/Docker
- `get_logger(__name__)` for any module; `SnapAdminLogger` for SnapAdmin internals

#### Demo & Sandbox
- Complete `demo/` app: Product, Customer, Order models with complex relationships
- Custom system dashboard with environment health checks and analytics
- `python manage.py seed_demo` to populate the environment instantly
- Docker Compose stack: Django + PostgreSQL + Redis + Elasticsearch
- Celery background tasks: ES re-indexing, daily stats generation

### Available SnapField Types
`SnapCharField`, `SnapTextField`, `SnapEmailField`, `SnapSlugField`, `SnapURLField`,
`SnapUUIDField`, `SnapIntegerField`, `SnapPositiveIntegerField`, `SnapFloatField`,
`SnapDecimalField`, `SnapBigIntegerField`, `SnapDateField`, `SnapDateTimeField`,
`SnapTimeField`, `SnapDurationField`, `SnapFileField`, `SnapImageField`,
`SnapBooleanField`, `SnapJSONField`, `SnapGenericIPAddressField`,
`SnapForeignKey`, `SnapOneToOneField`, `SnapManyToManyField`

### Requirements
- Python >= 3.10
- Django >= 5.2

### Known Issues
- Celery startup in the sandbox requires a `celery.py` app module in the `sandbox` package
- Swagger filter schemas are not yet auto-generated for all SnapField types
- `ES_ONLY` mode uses a pseudo-random integer PK — not suitable for high-concurrency writes

### Installation
```bash
pip install django-snapadmin
```
