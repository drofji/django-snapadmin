# Changelog

All notable changes to **django-snapadmin** are recorded here. This file is a concise,
version-by-version summary; the full, prose release notes for each version live in
[the docs repository](https://github.com/drofji/django-snapadmin/tree/main/docs/releases/) 
(shipped in the source distribution) and online in the project documentation.

The project follows [PEP 440](https://peps.python.org/pep-0440/) versioning and is in the
**beta** series (`0.1.0bN`) — the public API is stabilising but may still change before `0.1.0` stable.

## Unreleased

### Added
- **`@snap_model` opts a plain `django.db.models.Model` into SnapAdmin, without subclassing.**
  `@snap_model(api_write_fields=[...], api_exclude_fields=[...], search_fields=[...])` on an ordinary
  Django model registers it and records the same settings a `SnapModel` subclass declares as class
  attributes. It adds no field and no attribute, so it needs no migration. The model then gets the
  REST CRUD routes, a GraphQL type, the offline endpoints, the system checks and the `snapadmin_info`
  inventory. It deliberately does **not** get `SnapModel`'s runtime machinery — no
  `EsManager`/`es_search()`/`snapadmin_reindex`, no `purge_expired()`, no generated admin, no
  `formatted_id` — and the ES, retention and admin sweeps skip it rather than half-work, which is why
  it accepts no `es_*` / `data_retention_*` keywords. Intended for brownfield schemas, models whose
  base class belongs to a third-party package, and field packages like `django-money` or
  `phonenumber_field`.

- **`snapadmin.registry` is public API.** `is_registered(model)` is the gate every SnapAdmin surface
  asks, `get_model_meta(model, name, default)` reads one model-level setting (registry entry first,
  class attribute second), and `register()` / `meta_for()` complete the surface. All four are pinned
  in the public-API contract tests.

- **`snap_field()` puts SnapAdmin metadata on any Django field, not just a `Snap*Field`.**
  `snap_field(models.CharField(max_length=255), searchable=True, filterable=True)` sets the same
  attributes a `Snap*Field` stores on itself (`searchable`, `filterable`, `show_in_list`,
  `wysiwyg`, `tab`, `row`, …) directly on a plain field instance, so a third-party field package
  (`django-money`, `model-utils`, `phonenumber_field`, …) or a brownfield model that cannot be
  rewritten onto the `Snap*Field` classes gets identical behaviour. Every reader treats the result
  exactly like a `Snap*Field`; adds no migration; an unrecognised kwarg raises `ValueError` naming
  it.

- **`snapadmin-new` scaffolds a project you keep.** `pip install django-snapadmin && snapadmin-new
  myshop` writes `manage.py`, a settings package, one app with a worked `SnapModel` example, SQLite
  and a `.env`/`dist.env` — `migrate` then `runserver` work immediately, no Docker, no manual edits.
  `--full` additionally writes a `Dockerfile`, `docker-compose.yml` and the PostgreSQL / Redis /
  Elasticsearch wiring. Templates ship inside the wheel under `snapadmin/scaffold/templates/` and
  render with the standard library's `string.Template` — no new dependency. It refuses to write into
  a non-empty target directory, and validates the project/app name the way `django-admin
  startproject` does (a valid identifier that doesn't shadow an existing importable module).

- **`snapadmin-demo` refreshes an existing demo tree instead of layering over it.** Every extraction
  now leaves a `.snapadmin-demo.json` stamp (the release it came from and the files it wrote). A
  re-run names both versions before touching anything and deletes the files the new release no
  longer ships — files you added yourself are never in the stamp and are never deleted. `pip install
  -U django-snapadmin` upgrades the package but not an extracted `demo/` directory, which used to
  keep serving old models and templates with nothing to say so.

- **Alert channels — Slack, Discord, Teams, Telegram and JSON webhooks beside email.** The error
  spike alert, the daily digest and the health alert are now delivered by a set of channels
  configured in `SNAPADMIN_ALERT_WEBHOOKS`; email is one channel among them and can be switched off
  with `SNAPADMIN_ALERT_EMAIL_ENABLED = False`. Webhooks are posted with the standard library — no
  new dependency. Thresholds, grouping and the cooldowns are shared by every channel, so a webhook
  changes where an alert goes, never how often it fires; a per-entry `events` list filters which
  alerts a channel receives. Delivery is fail-soft (an unreachable webhook never breaks the request,
  the digest task or `snapadmin_health_alert`, and never stops the other channels), and a send where
  every channel failed releases the cooldown instead of consuming it. Webhook URLs are treated as
  secrets: never logged, never in an alert body, never in `snapadmin_info`.

- **XLSX as a third async-export format.** `POST /api/exports/` accepts
  `export_format="xlsx"` alongside `csv` and `json`, writing a real workbook whose cells keep their
  types — a `DecimalField` arrives as a number the spreadsheet can sum, a `DateTimeField` as a date
  in the project's timezone. It needs the new `[xlsx]` extra (`pip install django-snapadmin[xlsx]`,
  which pulls in MIT-licensed openpyxl); requesting the format without it is rejected with a `400`
  naming the extra rather than accepted as a job that could only fail later. Because a workbook is
  written whole rather than appended to, an `xlsx` job **does not resume** from its checkpoint — a
  retry re-exports from the first row — and a cancelled or failed one leaves no partial file to
  download. Text beginning with `=` is stored as text, so exported rows are never evaluated as
  formulas when the file is opened. `csv` and `json` are unchanged.

- **Translations for the Unfold theme's own interface.** `django-unfold` ships no catalogs, so a
  themed admin rendered its shell ("All applications", "Apply Filters", "No results found", the
  command palette) in English around a translated page. SnapAdmin now supplies those strings in all
  ten locales; nothing to configure, and your project's own catalogs still take precedence.

- **A readable per-object diff timeline in the audit log.** The audit trail already stored a
  structured `{field: {"old": …, "new": …}}` diff; the admin only ever printed it as raw JSON. Every
  entry now renders as a field-level before/after table, and the *Object* column links to a
  **timeline** — every recorded change to that one object on a single page, newest first, at
  `/admin/snapadmin/snapadminauditlog/timeline/<app>/<model>/<id>/`. Both views mask through the
  same rules as the rest of the admin, and both are gated on the audit log's own view permission,
  so the timeline can never show more than the list it is reached from. Long histories are capped at
  100 entries per page (`timeline_max_entries`); `manage.py snapadmin_audit_export` still has the
  full history.

- **`SNAPADMIN_MASKING_RULES` — per-field masking rules and per-field PII permissions.** How a field
  is obfuscated used to be fixed by its type. A rule now sets it per field: a regex rewrite
  (`{"pattern": r"\d(?=\d{4})", "replacement": "*"}` → `************1111`), a flat redaction
  (`{"replacement": "[redacted]"}`), and/or a `permission` that unlocks *that one field* for whoever
  holds it, without granting the blanket `snapadmin.view_raw_pii`. Declaring a rule also declares the
  field sensitive, so `SNAPADMIN_MASKED_FIELDS` stays optional and **completely unchanged** where it
  is already in use. Rules apply everywhere masking does — admin changelist, REST, GraphQL,
  background exports and the audit diff — and `user_can_view_pii(user, field=…)` grew the field
  argument additively. Patterns are compiled once; one that could backtrack catastrophically
  (`(a+)+`), one that does not compile, and any value over 4096 characters all fall back to the
  built-in masker rather than to raw data. Because a rule that names a model or field that does not
  exist would fail *open* — masking nothing and saying nothing — three new system checks
  (`snapadmin.E003`–`E005`) reject one at startup.

### Changed
- **The README is now a landing page, not a manual.** It opens with the problem SnapAdmin solves in
  plain language — four internal-tool surfaces (admin, two APIs, search) that are normally four
  separate descriptions of the same data — before any code, then the 60-second quickstart, then the
  commands. A new *For teams and enterprise* section answers the questions asked before a dependency
  is approved (licensing and how to prove it, audit trail, GDPR, PII, test coverage, upgrade policy,
  scale, SSO, monitoring, backups, lock-in) as questions rather than as a feature list. Reference
  detail — both `INSTALLED_APPS` listings, the extras table, the theme comparison, the Docker demo —
  moved behind collapsible sections, cutting what a first-time reader has to scroll past by roughly
  half. No content was dropped and no link changed target.
- **Rich-text HTML is sanitized on write, not only on render.** `wysiwyg=True` /
  `SnapRichTextField` values are cleaned in `pre_save()`, so every ORM write path (admin form, REST
  and GraphQL serializers, `save()`, `bulk_create()`) stores sanitized HTML — previously only the
  admin changelist cleaned it on the way out, leaving every other consumer with the raw payload.
  **This changes what is stored and is lossy** for markup outside the allowlist; existing rows are
  not rewritten. Opt out with `safe_html=True` or the new `auto_sanitize=False`, or widen the
  allowlist via `SNAPADMIN_HTML_SANITIZER`. `QuerySet.update()` is not covered.
- **`snapadmin_info` reports a demo tree that has drifted from the installed release** in its
  *Version & Status* section. Projects without an extracted demo tree see no change.
- **Audit diffs keep JSON-native types.** `audit.format_value()` stringified every value, so a diff
  could not tell `42` from `"42"`, or `False` from `"False"`. Numbers, booleans, strings and `null`
  are now stored as themselves; everything else (`Decimal`, dates, UUIDs, related objects) is still
  `str()`-ed, as are non-finite floats, which have no JSON literal. Rows written before this release
  are untouched and still hold the string form — a consumer of `SnapadminAuditLog.changes` should
  accept both. The `old`/`new` key names are unchanged and will not be renamed.

### Fixed
- **`SnapStatusBadgeField("status", [...])` — the source field and choices may be positional.** They
  were keyword-only, so the obvious call failed with a message that read as "you forgot it". A wrong
  or missing argument now raises a `ValueError` naming the field and the call to write, at import
  time rather than at render time.
- **The audit-log change form no longer prints the unmasked diff.** For a viewer without PII access
  the raw `changes` JSON was swapped for a masked copy in `readonly_fields`, which dropped the real
  field out of that list and back into the form — where Django rendered it read-only straight from
  the model, unmasked, right beside the masked one. The raw field is now excluded from the form
  outright and only the rendered diff is shown.
- **`snapadmin_info` survives a failing section.** A collector that raises now renders as
  `Title: unavailable — ExceptionType: message` (`collector_error` in `--json`) instead of aborting
  the report; credentials in the message are redacted, and a crashed health probe still fails
  `--health-check`.
- **`snapadmin.tasks` imports without Celery installed.** Celery is an optional extra, but the
  module required it at import time. Task names are unchanged; calling a task runs it in-process,
  and `.delay()` / `.apply_async()` raise `ImproperlyConfigured` pointing at
  `pip install django-snapadmin[celery]` instead of silently doing nothing.

### Deprecated
- **The removal window for the deprecated aliases is now fixed at `1.0`**, stated everywhere the
  notice appears (stderr, `help` text, module docstrings, `SECURITY.md`, the docs): the unprefixed
  management commands `db_backup`, `purge_expired_data`, `send_error_digest` (use
  `snapadmin_db_backup`, `snapadmin_purge_expired_data`, `snapadmin_send_error_digest`) and the
  underscored console scripts `snapadmin_info` / `snapadmin_license_check` (use the dashed
  `snapadmin-info` / `snapadmin-license-check`). All of them still work unchanged in this release —
  this is advance notice, not a behaviour change.

## 0.1.0b6 — 2026-08-13

A first-run polish release, from installing 0.1.0b5 into a fresh project and walking the demo.
No migration; no import path, setting or command name removed.

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

- **Changed (BREAKING):** auto-generated REST filters now default text fields to **exact** match
  instead of substring. `?field=value` was `icontains` (a never-indexable leading-wildcard `LIKE`,
  and `?sku=123` also matched `sku=91234`); it is now an exact, index-usable match. Substring
  search moves to the explicit `?field__icontains=value`, alongside new `__startswith` and `__in`
  lookups. Set `api_filter_lookups` per model to restore the old behaviour for a given field.
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

- **Changed (BREAKING):** Celery tasks moved to `snapadmin/tasks.py` and renamed to the `snapadmin.*`
  namespace (from `api.tasks.*`) so `autodiscover_tasks()` finds them. Update every
  `CELERY_BEAT_SCHEDULE` entry and any imports; no back-compat aliases are kept.
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
`0001_initial.py` per app. Breaking for installs that already ran `migrate` on a prior alpha
(drop/recreate the database); no model or API changes.

## 0.1.0a10 — 2026-07-05

Housekeeping only: fixed a malformed `templates/admin/index...html` filename (the admin
dashboard override was silently ignored) and replaced a debug `print()` / swallowed exception
around GraphQL URL wiring with structured `structlog` logging.

## 0.1.0a9 — 2026-07-05

Enterprise backlog: immutable audit trail, asynchronous background export, large-dataset
pagination, full i18n (10 locales), WCAG 2.1 AA accessibility, an ecosystem-compatibility matrix,
configuration health checks and a migration guide.

## 0.1.0a8 — 2026-07-05

Config-driven enterprise features: read-replica routing, an SSO/OAuth2 login helper, PII masking
and nested-app grouping.

## 0.1.0a7 — 2026-07-04

SFTP offsite backups and a `[backup]` extra, automated PyPI publishing (tag → OIDC Trusted
Publishing), PyPI project URLs, and a docs split (package vs demo) with an Extending guide.

## 0.1.0a1 – 0.1.0a6

Initial alpha series: the declarative `SnapModel` + `Snap*` field types, auto-generated Unfold
admin, REST API with Swagger, dynamic GraphQL, Elasticsearch integration and smart `?search=`
routing, email error monitoring and 3-2-1 database backups. See the online release notes for
detail.
