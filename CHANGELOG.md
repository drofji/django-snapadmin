# Changelog

All notable changes to **django-snapadmin** are recorded here. This file is a concise,
version-by-version summary; the full, prose release notes for each version live in
[the docs repository](https://github.com/drofji/django-snapadmin/tree/main/docs/releases/) 
(shipped in the source distribution) and online in the project documentation.

The project follows [PEP 440](https://peps.python.org/pep-0440/) versioning and is in the
**beta** series (`0.1.0bN`) — the public API is stabilising but may still change before `0.1.0` stable.

## Unreleased

### Breaking
- The shipped `admin.js`'s select2 auto-init is now **opt-in**: only a `<select>` carrying a
  `snapadmin-select2` class (or `data-snapadmin-select2` attribute) gets initialised, not every
  `<select>` on the page. The old broad selector reached the changelist's own action dropdown and
  silently broke bulk actions on a theme that binds it through Alpine. Add the class to a field's
  widget to opt it back in.
- `SNAPADMIN_CONNECTIVITY_ENABLED` now defaults to `False` (previously always on): the admin-wide
  health poll, save-blocking guard and sidebar sync badge no longer load unless explicitly enabled
  *and* at least one registered model has `offline_mode = True`. A deployment with
  `SNAPADMIN_REST_API_ENABLED = False` used to poll a 404ing `/api/health/` forever and block every
  Save button — set `SNAPADMIN_CONNECTIVITY_ENABLED = True` to restore the previous behaviour.
- `SNAPADMIN_REST_API_ENABLED` / `SNAPADMIN_GRAPHQL_ENABLED` are deprecated
  to default to `False` at `1.0` — see the deprecation warning and migration note below.
- Retro-note (this heading is new): `SnapModel.get_admin_fields()`'s return arity silently grew
  from four values to five in an earlier pre-1.0 release with no changelog entry — now pinned so
  it cannot shift silently again. `django-admin-rangefilter` stopped being a dependency in
  `0.1.0b6`, already documented there under `Removed` — see that entry rather than duplicating it.
- `snapadmin.backup.run_backup()` / `run_due_backups()`, and the `purge_expired_data` /
  `send_error_digest` / `run_es_reindex` / `send_health_alert` Celery tasks, now **raise** instead
  of returning a dict when every unit of work failed (previously reported as a normal, if
  unhelpful, return value). Code calling these directly and branching on the return value for a
  total-failure case must now catch the exception instead (`BackupError`, `SnapPurgeError`,
  `AlertDeliveryError`, `ReindexError`) — see the task-outcome convention under Fixed, below.

### Added
- `snap_field()` now accepts every `Snap*Field` constructor kwarg — `required` and the file-upload
  trio (`allowed_extensions`/`allowed_encodings`/`max_size_bytes`) are no longer refused.
- `SNAPADMIN_PROFILE = "admin" | "api" | "full"` picks sane defaults for the handful of settings
  that actually differ by use case, instead of deciding all ~90 individually. Unset (or `"full"`)
  changes nothing — every existing install keeps its current behaviour.
- `snapadmin_info`'s `inventory` section now reports, per model, which registration door it came
  through (`SnapModel` subclass vs. `@snap_model` decorator) and which capabilities that door
  leaves inactive (Elasticsearch mirroring, retention purge, generated admin).
- `@snap_property` decorates a method into a computed, display-only admin column — the decorator
  form of `SnapFunctionField` (no database column, no migration). Works identically on a
  `SnapModel` subclass and on a `@snap_model`-decorated plain model.
- `get_model_meta()` gains a third precedence tier: a project-wide `SNAPADMIN_<NAME>` setting,
  consulted between the class attribute and the caller's built-in default. Only reachable on the
  `@snap_model` route — a `SnapModel` subclass always has a class attribute to answer from.
- A runnable integration checklist (Must work / Should be configured / Data safety /
  Optional-scale), documented at `#integration-checklist` and now printed by `snapadmin-init`
  itself — every row is ✅/❌/⚠️, never a false green for anything it can't check without a live
  project.
- Database backups can be encrypted in-stream with AGE (`SNAPADMIN_BACKUP_AGE_RECIPIENTS`) — any
  one of N configured recipients decrypts a bundle independently. Two interchangeable backends
  (`pyrage`, the new optional `[age]` extra, or the `age` CLI). Empty (the default) changes nothing.
- `SNAPADMIN_BACKUP_INCLUDE` bundles media and an encrypted `.env` alongside the database backup
  (default `["db"]`, opt-in). Every run now also writes an unencrypted `manifest.json` sidecar;
  retention (`SNAPADMIN_BACKUP_KEEP`) applies per part.
- `manage.py snapadmin_restore` restores a backup bundle — dry-run by default, `--confirm` to
  perform it. Verifies the manifest checksum before touching anything, supports `--only`/`--skip`
  part selection, fetches straight from a configured destination (`<destination>:<name>`), and
  prints exactly which identity an encrypted bundle needs.
- `manage.py snapadmin_rollback` undoes a restore: `snapadmin_restore --confirm` automatically
  snapshots the current live state before touching anything (aborting the restore if the snapshot
  itself fails), and `snapadmin_rollback [<id>]` restores it back. Its own short retention
  (`SNAPADMIN_RESTORE_SNAPSHOT_KEEP`, default 3) is separate from `SNAPADMIN_BACKUP_KEEP`.
- A fifth backup destination: any S3-compatible object store (`SNAPADMIN_BACKUP_S3_*`, the new
  optional `[s3]` extra, `boto3`) — AWS S3, MinIO, Backblaze B2, Hetzner Object Storage or Wasabi via
  `SNAPADMIN_BACKUP_S3_ENDPOINT_URL`. Supports the ambient AWS credential chain when no explicit key
  is set. A worked SFTP recipe for Hetzner Storage Box (a different, non-S3 product) is now in the
  docs. New check `snapadmin.W011` flags an incompletely configured S3 destination.
- `snapadmin_info` gains a `backups` section (destinations, last run per destination, encryption
  status, recipient fingerprints); the `features` section's backup line now also names the active
  destinations and whether a restore has ever run.
- `SnapModel.get_admin_fields()` returns a pinned `AdminFieldSets` named tuple (`form_fields`,
  `list_display`, `search_fields`, `list_filter`, `autocomplete_fields`) instead of a bare 5-tuple —
  backward-compatible by construction, since positional unpacking, indexing and `len()` all keep
  working; a future sixth member stays a breaking change, just an announced one.
- `SnapModel.get_admin_media()` — the base admin `(js, css)` asset lists as a public, typed
  classmethod, so a project overriding `register_admin()` can extend the real lists instead of
  copying a snapshot that rots at the next release.
- `APIToken.allowed_scopes` (new field, one migration) plus `token_has_scope()` scope a token to a
  project's own endpoints, not just SnapAdmin's generated model routes — SnapAdmin only stores and
  matches the free-form strings, the meaning is the project's. Empty denies every scope check
  (fail-closed), unlike `allowed_models`.
- `POST /api/tokens/<id>/rotate/` (also `APIToken.rotate()`) replaces a token's secret in place —
  same row, id, scopes and history — and returns the new raw key once; the old key stops
  authenticating immediately. Written to the audit trail.
- `POST /api/tokens/<id>/deactivate/` flips `is_active` off without deleting the row — the
  documented revocation path. A regular user manages their own tokens (list, rotate, deactivate)
  without needing to be a superuser.
- `snapadmin_reindex --verify` compares the Elasticsearch document count against the source row
  count once a model's run finishes (discounting documents ES itself rejected, and skipped
  entirely for `ES_ONLY` models, which have no independent source to compare against) and exits
  non-zero on a mismatch — a run that reports success on the strength of its own loop counter no
  longer looks identical to one that quietly came up short. `--progress-interval` (default 5s)
  throttles the per-chunk progress line so a multi-hour run in a detached container doesn't fill
  the log with one line per chunk; the line reporting a model's completion, cancellation or
  failure always prints regardless of the throttle.

### Changed
- The shipped `admin.js`'s select2 initialisation is opt-in now — see Breaking, above, for the
  migration note.
- `SNAPADMIN_CONNECTIVITY_ENABLED` gates the admin-wide connectivity layer and now defaults to
  `False` — see Breaking, above.

### Fixed
- A project's own `admin_overrides["get_readonly_fields"]` / `admin_overrides["safe_html_<field>"]`
  no longer get silently clobbered by the generator: the generated callables are merged onto the
  admin class before `admin_overrides`, never written into it, so a project's own override always
  wins regardless of write order. Previously a project's own `get_readonly_fields` could vanish,
  taking every change form on the site down with `FieldError: Unknown field(s)`, with nothing logged.
- Off `DEBUG`, the shipped media no longer downloads jQuery twice: the base admin JS now picks
  `jquery.js` / `jquery.min.js` the same way Django's own `ModelAdmin.media` does, so the two media
  lists collapse into a single entry on merge.
- The system dashboard's GitHub link pointed at the retired `drofji/snapadmin` (dead) instead of
  `drofji/django-snapadmin`.
- **A scheduled task that did nothing, or half-failed, no longer looks like a success.** All six
  Celery tasks (`run_db_backups`, `purge_expired_data`, `purge_expired_tokens`, `send_error_digest`,
  `run_es_reindex`, `send_health_alert`) now return a `status` key — `"ok"` / `"partial"` /
  `"noop"` / `"disabled"` — and a `failed` list alongside every existing key (purely additive), and
  **raise** instead of returning when every unit of work failed. One monitoring rule now covers all
  six: alert when `status != "ok"`, page when the Celery task state is `FAILURE`. Fixes the reported
  incident where a disabled backup schedule ran "successfully" for weeks with no backup ever taken,
  and a silently-failing offsite destination never surfaced anywhere but a log line.
- `run_db_backups`'s due-time check (`_is_due()`) no longer skips a day when a run completes even
  slightly later than the previous day's ideal slot — a small tolerance (2% of the destination's own
  interval) absorbs realistic scheduler jitter without materially changing when a backup actually
  runs. A new check, `snapadmin.W010`, warns when the Celery Beat entry for `run_db_backups` runs
  less often than the shortest configured `SNAPADMIN_BACKUP_*_EVERY_HOURS` — that combination
  silently drops days regardless of the tolerance above.
- `create_db_dump()` (and the AGE-encrypted path) now supports MySQL via `mysqldump`, alongside the
  existing PostgreSQL and SQLite support — the credential handled the same way as the PostgreSQL
  branch (`MYSQL_PWD` environment variable, never a command-line argument).
- The dynamic model API answers an unknown or unregistered model the same way on every action,
  including `retrieve`/`update`/`partial_update` — previously provided by DRF without an explicit
  guard, so they fell through to filtering an empty queryset instead of the consistent 404 body the
  other five actions already built for themselves. The check now runs once in `initial()`.

### Security
- `snap_field(field, wysiwyg=True)` now sanitizes on write, matching `SnapRichTextField` — closing
  a gap where the wrapper route stored raw HTML unsanitized.
- Backing up `env` with no `SNAPADMIN_BACKUP_AGE_RECIPIENTS` configured is refused fail-closed
  (system check `snapadmin.E007` plus a matching runtime guard) — a `.env` file's secrets are never
  written to a backup destination unencrypted.
- An unresolvable model on the dynamic API now denies every HTTP verb instead of falling back to
  full CRUD (`_resolve_http_method_names()`), and the 404 guard runs after authentication and
  permission checks — asserted by a dedicated test — so an anonymous probe cannot use a 404-vs-401
  difference to enumerate registered models without credentials.

## 0.1.0b7 — 2026-08-25

Two ways to declare a model instead of one, plus a full-project scaffolder and a batch of
operational features. No breaking changes, no required migration — every addition is opt-in.

### Breaking
- None.

### Added
- **`@snap_model`** opts a plain `django.db.models.Model` into SnapAdmin without subclassing —
  registers it and records the same settings a `SnapModel` subclass declares as class attributes.
  No field, no attribute, no migration. Deliberately does **not** attach `SnapModel`'s runtime
  machinery (Elasticsearch, retention purge, generated admin); the seven gates that used to
  hand-roll an `issubclass(model, SnapModel)` check now ask the registry instead.
- **`snapadmin.registry` is public API** — `is_registered()`, `meta_for()`, `register()`, and the
  new `get_model_meta()` every SnapAdmin surface now reads a model-level setting through.
- **`snap_field()`** sets SnapAdmin's field-level attributes (`searchable`, `filterable`,
  `wysiwyg`, …) directly on any Django field instance — the same interop path as `@snap_model`,
  one field at a time, for a third-party field package or a field that can't be rewritten.
- **`snapadmin-new`** scaffolds a project you keep — `manage.py`, settings, a worked `SnapModel`
  example, SQLite, `migrate` and `runserver` work immediately. `--full` adds Docker/Postgres/
  Redis/Elasticsearch.
- **`snapadmin-demo` stamps the tree it extracts** (`.snapadmin-demo.json`), so a re-run actually
  refreshes it — deleting files the new release dropped, keeping anything you added yourself.
- **Alert channels** — Slack, Discord, Teams, Telegram and JSON webhooks beside email, via
  `SNAPADMIN_ALERT_WEBHOOKS`. No new dependency; webhook URLs are treated as credentials.
- **XLSX exports** behind the optional `[xlsx]` extra — typed cells, formula-injection guarded;
  not resumable, unlike CSV/JSON.
- **The Unfold theme's own chrome is now translated** in all ten shipped locales — `django-unfold`
  ships no catalogs of its own.
- **A readable audit-log diff and a per-object timeline** — field-level before/after table instead
  of raw JSON, plus `/admin/.../timeline/<app>/<model>/<id>/` showing every change to one record.
- **`SNAPADMIN_MASKING_RULES`** — per-field masking pattern/replacement and a permission that
  unlocks one field without the blanket `view_raw_pii`.

### Fixed
- **`SnapStatusBadgeField` accepts its source field and choices positionally**, not just as
  keywords — the missing-argument error used to point at the wrong thing.
- **One failing `snapadmin_info` section no longer takes down the whole report** — isolated per
  collector, with credentials redacted from the error text.
- **`snapadmin.tasks` no longer requires Celery to import** — calling a task now runs it
  synchronously without Celery installed; queueing it raises naming the `[celery]` extra.

### Changed
- **The README is a landing page, not a manual** — problem statement, 60-second quickstart, an
  enterprise Q&A section; reference material moved behind collapsible sections.
- **Rich-text HTML is sanitized on write, not only on render** — covers every ORM write path.
  Lossy by design; `safe_html=True` or `auto_sanitize=False` opt out. `QuerySet.update()` is not
  covered.
- **`snapadmin_info` reports demo-tree drift** against the installed package version.
- **Audit-log diffs preserve JSON-native types** — numbers, booleans and `null` no longer
  collapse to strings.

### Deprecated
- **The removal window for every currently-deprecated alias is now fixed at `1.0`** — no
  behaviour change, just a date where "a future release" used to be.

### Security
- **The audit-log change form no longer renders the unmasked diff** to a viewer without PII
  access — the raw field is now excluded from the form outright.
- **Wysiwyg sanitization now fails closed if `nh3` cannot be imported**, instead of silently
  skipping sanitization. `nh3` is still a required dependency today; this is defense in depth
  ahead of a future release that makes it optional.

## 0.1.0b6 — 2026-08-13

A first-run polish release, from installing 0.1.0b5 into a fresh project and walking the demo.
No migration; no import path, setting or command name removed.

### Breaking
- `django-admin-rangefilter` is no longer a dependency — breaking only for code that imported it
  directly or listed it in `INSTALLED_APPS`; see `Removed` below.

### Added
- **`llms.txt`** — a machine-readable map of the documentation for AI coding assistants, in the
  [llmstxt.org](https://llmstxt.org/) format. Published at
  <https://drofji.github.io/django-snapadmin/llms.txt> and shipped in the source distribution.
- **A quickstart and module map in the `snapadmin` package docstring** — the three-step example,
  what every module and management command does, the `SNAPADMIN_*` setting families and the optional
  extras, available offline from any install via `help(snapadmin)`.
- **`snapadmin-info` and `snapadmin-license-check` as console scripts** — all four spellings
  (`snapadmin-info`, `snapadmin_info`, `python manage.py snapadmin_info`, likewise for the licence
  check) now work; the shim finds your `manage.py` and forwards arguments and exit code.
- **A copy-pasteable container health check** — `HEALTHCHECK` in the demo image and compose service,
  plus a docs section with the exact values for Docker, Compose, Coolify/Dokploy/Caprover and
  Kubernetes.
- **Remote S3-compatible storage in the demo** from one variable (`SNAPADMIN_STORAGE_BACKEND=s3`) —
  AWS S3, Hetzner Object Storage, MinIO and Backblaze B2, with signed URLs and no-overwrite defaults.
- **A `checks` section in `snapadmin_info`** — per-severity system-check counts, with `--health-check`
  failing on any error.
- **A written API-stability and compatibility policy** in `SECURITY.md` — what counts as public API,
  what does not, and the semver/deprecation rules that take effect at `1.0`.
- **A docstring on every name in the public contract**, with usage examples on the ones you type, and
  a test that fails when a public name is added without one.
- **README positioning against Unfold, Jazzmin and Grappelli** — those restyle the admin you write;
  SnapAdmin generates it (and the API, GraphQL and search) from the same field declarations, using
  Unfold as its optional theme.
- **An honest scale note in the README** — no benchmark numbers are quoted; it points at the demo's
  `seed_large` and `benchmark_list_view` commands so you measure on your own data.

### Changed
- **Every management command is `snapadmin_*`-prefixed.** `db_backup`, `purge_expired_data` and
  `send_error_digest` become `snapadmin_db_backup`, `snapadmin_purge_expired_data` and
  `snapadmin_send_error_digest` — generic names can silently collide with a command of your own.
  The old names keep working as deprecated aliases (rename notice on stderr, stdout untouched).
  Celery task names are unchanged.
- **`snapadmin_info` output is readable at a glance** — system checks no longer print above the
  report, uniform records render as an aligned table (model inventory: 55 lines → 13 for 11 models),
  and runs of booleans collapse to one `✓ on` / `✗ off` pair.
- **`snapadmin.W004`/`W007` emit one grouped warning** naming the affected models, instead of one
  near-identical block per model. Check ids are unchanged. W004 no longer fires for models that
  answer 405 to every write (`api_read_only`, or an `api_http_method_names` with no write verb).
- **Loading a model no longer imports the REST framework** — `SnapDynamicPagination` is built on
  first access, and `snapadmin.urls` imports DRF, drf-spectacular and graphene only inside the
  branches that need them. Groundwork: those packages are still dependencies of every install;
  moving them behind `[api]`/`[graphql]` extras is left to its own release.
- **A feature enabled without its dependencies raises an actionable error** naming both the packages
  to install and the setting to switch off, instead of an `ImportError` from inside a URLconf. A
  missing `graphene-django` with GraphQL enabled now raises instead of silently logging a warning.

### Removed
- **`colorama` is no longer a dependency** — nothing in the package imported it; the console colour
  is plain ANSI escapes.
- **`django-admin-rangefilter` is no longer a dependency** — it was installed for every user and the
  package never imported it (range filters come from `unfold.contrib.filters`, or Django's own list
  filters without the theme). Install it directly if your own admin code uses it.

### Fixed
- **`snapadmin_info --health-check` honours `SILENCED_SYSTEM_CHECKS`** — a silenced check used to
  keep it failing forever on a configuration `manage.py check` calls clean.
- **`SNAPADMIN_SWAGGER_ENABLED` follows `SNAPADMIN_REST_API_ENABLED` by default** — switching the
  REST API off left the OpenAPI views wired with nothing to document. An explicit setting still wins.
- **`--no-color` reaches the deprecated commands' rename notice**, so a piped log no longer collects
  ANSI escapes.
- **`GET /api/health/` reports `unhealthy` (503) when the database is down even if Elasticsearch is
  also unreachable.** The Elasticsearch branch could overwrite the status with the still-serving
  `degraded`, so probes kept routing to an instance that could not answer a query.
- **The dashboard chart renders in French** — a translated label containing an apostrophe broke the
  inline `<script>`, so no chart appeared.
- **The themed `User`/`Group` admin no longer depends on `INSTALLED_APPS` order**, which could
  silently skip the theming entirely.
- **The dashboard no longer crashes when a model sets `admin_enabled = False`** — one opted-out model
  took down the whole page with `NoReverseMatch`.
- **SnapAdmin's stylesheet no longer overrides the Unfold theme's own form layout** — the themed
  layer was scoped to a class current Unfold never emits, so it was dead while stock-admin layout
  rules applied everywhere. Styling now ships as one shared sheet plus exactly one theme layer.
- **The built-in `User` admin has a working password field under the theme again** — Unfold's
  templates were rendering against Django's stock forms, leaving the password row empty with no way
  to change it.

## 0.1.0b5 — 2026-07-24

Scale-hardening and operability. The production-scale Elasticsearch query layer is finished, the
auto-generated REST filters are richer and safer, `etl.stale_sync` scales past an in-memory key set,
async export sources become pluggable, and `snapadmin_info` gains a feature-adoption audit. Everything
is additive and backward-compatible; two additive migrations ship (a demo-only watermark column and
`SnapExportJob.source`).

- **Breaking:** none.
- **Added:** `SnapModel.es_count()` — exact match count of a structured ES query, past the search limit.
- **Added:** ES query methods accept `db_fallback=False` (+ `SNAPADMIN_ES_DB_FALLBACK`) to raise
  `SnapEsUnavailable` instead of silently falling back to the database.
- **Added:** `es_scan(source=False, limit=…)` streams primary keys of N-million matches; `snapadmin_reindex`
  gains `--limit` and a settable `--tune` default and fetches only ES-mapped columns.
- **Added:** REST filters gain `?field__isnull=` / `?field__in=` across text/numeric/date/FK, a swappable
  `SNAPADMIN_API_FILTER_BACKEND`, project/model-wide text-lookup defaults, and JSON comma-OR with a lazy
  native queryset + `SNAPADMIN_API_JSON_FILTER_SCAN_CAP`.
- **Added:** per-model `api_read_only` / `api_http_method_names` (write verbs answer 405; `snapadmin.W007`).
- **Added:** `etl.stale_sync` DB-side `strategy="last_seen"` and non-raising `on_exceed="skip"`.
- **Added:** pluggable async-export row sources (`SNAPADMIN_EXPORT_SOURCES` + `SnapExportJob.source`).
- **Added:** `snapadmin_info --section features` — a ✓/✗ commerce-readiness feature-adoption checklist.
- **Added:** lazy top-level re-exports (`from snapadmin import SnapModel, SnapCharField`) + a module map.
- **Fixed:** `AppConfig.ready()` no longer crashes when an optional package is importable but not in
  `INSTALLED_APPS`; text `?field__isnull=` no longer 500s; `es_reindex_all()` no longer risks OOM on MySQL.

## 0.1.0b4 — 2026-07-21

An operability, onboarding and decoupling release: four new operator/onboarding commands, a subsystem
health-alert email channel, Docker self-healing in the demo, and `django-unfold` made an optional
theme. No model, no migration; every existing import path, setting and signature is unchanged.

- **Breaking:** none.
- **Added:** `snapadmin_info` — one command reporting config, connected services and health
  (`--json`, `--section`, `--brief`/`--verbose`, `--health-check`); secrets never printed.
- **Added:** `snapadmin_license_check` — runtime licence audit with 🟢/🟡/🔴 tiers and a
  commercial-compatibility verdict (`--json`, `--critical-only`, `--compatible-with`, `--verbose`).
- **Added:** `snapadmin-demo` console script — stdlib-only bootstrapper that fetches, seeds and serves
  the demo with no existing project (wizard, save/load config, non-interactive CI flags).
- **Added:** `snapadmin-init` console script — read-only integration doctor that prints the exact
  `INSTALLED_APPS` / urls / settings / install snippets to paste, editing nothing.
- **Added:** subsystem health alerts — `snapadmin_health_alert` command and `snapadmin.send_health_alert`
  task email when a probe (database, Elasticsearch, REST API, GraphQL — each skipped when its feature is
  off) is down, with a cooldown. Recipients fall back to `SNAPADMIN_ERROR_ALERT_EMAILS`.
- **Added:** multi-version CI (`test.yml`, Python 3.10–3.13 × Django 5.2/6.0, 100% coverage gate) and a
  status badge; publish/release now gate on the matrix passing.
- **Added (demo):** a `willfarrell/autoheal` sidecar and a Celery worker healthcheck so containers that
  hang while unhealthy are restarted, not just ones that exit.
- **Changed:** `django-unfold` moved from a core dependency to a `[theme]` extra (kept in `[all]`); the
  admin falls back to Django's built-in theme when Unfold is absent (byte-identical when present). New
  `snapadmin.I001` info check surfaces the fallback.

## 0.1.0b3 — 2026-07-20

A large security and Elasticsearch release: ten security fixes, a structured Elasticsearch query
layer, and safer bulk imports. One breaking change to the auto-generated REST filters.

- **Breaking:** auto-generated REST filters now default text fields to **exact** match
  instead of substring. `?field=value` was `icontains` (a never-indexable leading-wildcard `LIKE`,
  and `?sku=123` also matched `sku=91234`); it is now an exact, index-usable match. Substring
  search moves to the explicit `?field__icontains=value`, alongside new `__startswith` and `__in`
  lookups. Set `api_filter_lookups` per model to restore the old behaviour for a given field.
  SFTP backups now verify the remote host key against `known_hosts` (pre-populate it before
  upgrading); the streaming export's `?limit=0` or negative now rejects with `400` instead of
  streaming everything.
- **Security:** GraphQL now enforces `view` permission and PII masking on **every relation a query
  traverses**, not just top-level fields, matching the REST contract.
- **Security:** new `api_write_fields` mass-assignment guard restricts which fields accept a
  client-supplied value on REST create/update; a system check (`snapadmin.W004`) flags models
  without one.
- **Security:** fixed an SSO provider open redirect, a fail-open in `SmartModelSelectorWidget`,
  `mask_value()` type handling, and loss of upload-validator config on `Snap*Field`.
- **Security:** export filters are restricted to the target model's own fields (a related-field
  path could previously reach columns the caller could not otherwise read); PII masking is now
  closed on export, the audit trail, and API filtering/ordering/search.
- **Security:** database backup path hardened, plus assorted deployment-topology fixes.
- **Added:** `es_filter()` (structured term filters in ES filter context), `es_aggregate()`
  (terms facets) and `es_scan()` (a `search_after` iterator streaming past the 10k
  `max_result_window`) — each falling back to an equivalent database query when ES is off.
- **Added:** `etl.stale_sync()` prunes rows whose natural key vanished from the latest source sync,
  refusing (via `StaleSyncAbort`) if that would delete more than `max_fraction` of the table — so a
  truncated feed cannot silently wipe it.
- **Added:** resumable, progress-tracking bulk reindex (`snapadmin_reindex` / `SnapReindexJob`), and
  the `SNAPADMIN_EXPORT_MAX_ROWS` / `SNAPADMIN_EXPORT_LIMIT_MAX` ceilings on the streaming export.
- **Added:** JSON key-path filtering for the REST API via `api_json_filters`.
- **Fixed:** translation catalogs refreshed — the admin UI is fully localised again in all 10
  locales; GDPR purge correctness (secondary-store failures, `retention_days=0`, inflated counts);
  API pagination and throttling now actually enforced; async export torn-write duplication,
  single-flight and OFFSET drift.
- **Changed:** the README is now a 252-line overview, with the reference material moved to the
  documentation site, which gains Internationalization and Environment Variables sections.

See [the full release notes](https://github.com/drofji/django-snapadmin/blob/main/docs/releases/0.1.0b3.txt) for more detail.

## 0.1.0b2 — 2026-07-13

- **Breaking:** none.
- **Security:** the generic dynamic model API (`/api/models/<app>/<model>/`) now only resolves
  `SnapModel` subclasses, mirroring the schema endpoint. Previously any registered Django model
  (e.g. `auth.User`) could be listed, retrieved, created, updated or deleted through it.
- **Fixed:** doc links in the installed `CHANGELOG.md` now use absolute GitHub URLs instead of
  relative paths that 404 outside a source checkout.

See [the full release notes](https://github.com/drofji/django-snapadmin/blob/main/docs/releases/0.1.0b2.txt) for more detail.

## 0.1.0b1 — 2026-07-08

First beta. Completes a downstream-integrator feedback pass, hardens the dashboard, and reorganises
optional dependencies so a base install is fully permissively licensed. Carries a few breaking changes.

> Upgrading from 0.1.0a11? A few changes need action (Celery task rename, dashboard gate, deps moved
> to extras) — see [the migration guide](https://github.com/drofji/django-snapadmin/blob/main/docs/migrations/0.1.0a11_to_0.1.0b1.md).

- **Breaking:** Celery tasks moved to `snapadmin/tasks.py` and renamed to the `snapadmin.*`
  namespace (from `api.tasks.*`) so `autodiscover_tasks()` finds them. Update every
  `CELERY_BEAT_SCHEDULE` entry and any imports; no back-compat aliases are kept. The dashboard is
  now staff-gated by default (see Security below); `django-admin-autocomplete-filter`, the wysiwyg
  editor and `django-extra-settings` moved behind optional extras (see Changed below).
- **Security:** the system dashboard is now staff-gated by default (it exposed hostname,
  processor, OS, database name, service health and `ALLOWED_HOSTS` to anonymous callers).
  Opt out with `SNAPADMIN_DASHBOARD_PUBLIC = True`.
- **Security:** wysiwyg field values are sanitized (via `nh3`, a new core dependency) before being
  rendered in the admin changelist; opt back into raw HTML per field with `safe_html=True`.
- **Added:** `SNAPADMIN_URL_PREFIX` relocates the entire route surface (REST, Swagger, GraphQL)
  under one extra path segment for projects that already own the mount point; route names are
  unchanged.
- **Added:** admin-only bulk ES reindex endpoint (`POST /api/es/reindex/`, gated), a deletion-veto
  hook for the dynamic model API, and synchronous `count` / streaming NDJSON `export` actions.
- **Changed:** `django-extra-settings` is now an optional extra (`django-snapadmin[extra-settings]`),
  not a forced core dependency — SnapAdmin's core never used it.
- **Changed:** the wysiwyg editor (`django-ckeditor-5`, which bundles GPL/commercial CKEditor 5) is now
  an optional `[wysiwyg]` extra, imported lazily — the base package stays permissively licensed for
  commercial use.
- **Changed:** `django-admin-autocomplete-filter` (LGPL, unused by the core) is now the optional
  `[autocomplete-filter]` extra — the base install is now **fully permissive** (MIT/BSD/Apache), no
  copyleft/commercial code by default.
- **Added:** a Python × Django compatibility matrix in the README and `Framework :: Django :: 6.0`
  / per-minor Python classifiers; the suite runs green on Django 6.0.
- **Fixed:** aggregations on SnapModels no longer return wrong grouped counts (default `-pk` ordering
  no longer leaks into `GROUP BY`); `upsert_from_source()` works on MySQL/MariaDB.
- **Fixed:** the dashboard shows the real installed version and loads no external assets
  (Chart.js + Material Icons vendored, Font Awesome dropped for an inline SVG).
- **Fixed:** `SnapPhoneField` accepts spaced international numbers (e.g. `+49 89 1234567`).
- **Fixed:** the demo seeder no longer crashes on a cp1252 Windows console.
- **Docs:** GraphQL field-naming scheme documented; per-model admin extension points
  (`admin_mixins` / `admin_overrides` / `css_admin_files` / `js_admin_files`) documented; migration
  guide install name and `/api/` collision handling corrected; a CHANGELOG now ships to pip users.

See [the full release notes](https://github.com/drofji/django-snapadmin/blob/main/docs/releases/0.1.0b1.txt) for more detail.

## 0.1.0a11 — 2026-07-05

Squashed the `snapadmin` and `demo` migrations (`0001`–`0006` each) into a single
`0001_initial.py` per app. No model or API changes.

**Breaking:** migration history reset — installs that already ran `migrate` on a prior alpha must
reset the recorded migration rows and fake-apply the new initial (drop/recreate the database also
works); see the migration guide.

## 0.1.0a10 — 2026-07-05

Housekeeping only: fixed a malformed `templates/admin/index...html` filename (the admin
dashboard override was silently ignored) and replaced a debug `print()` / swallowed exception
around GraphQL URL wiring with structured `structlog` logging.

**Breaking:** none.

## 0.1.0a9 — 2026-07-05

Enterprise backlog: immutable audit trail, asynchronous background export, large-dataset
pagination, full i18n (10 locales), WCAG 2.1 AA accessibility, an ecosystem-compatibility matrix,
configuration health checks and a migration guide.

**Breaking:** none.

## 0.1.0a8 — 2026-07-05

Config-driven enterprise features: read-replica routing, an SSO/OAuth2 login helper, PII masking
and nested-app grouping.

**Breaking:** none.

## 0.1.0a7 — 2026-07-04

SFTP offsite backups and a `[backup]` extra, automated PyPI publishing (tag → OIDC Trusted
Publishing), PyPI project URLs, and a docs split (package vs demo) with an Extending guide.

**Breaking:** none.

## 0.1.0a1 – 0.1.0a6

Initial alpha series: the declarative `SnapModel` + `Snap*` field types, auto-generated Unfold
admin, REST API with Swagger, dynamic GraphQL, Elasticsearch integration and smart `?search=`
routing, email error monitoring and 3-2-1 database backups. See the online release notes for
detail.
