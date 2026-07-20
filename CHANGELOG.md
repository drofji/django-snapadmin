# Changelog

All notable changes to **django-snapadmin** are recorded here. This file is a concise,
version-by-version summary; the full, prose release notes for each version live in
[the docs repository](https://github.com/drofji/django-snapadmin/tree/main/docs/releases/) 
(shipped in the source distribution) and online in the project documentation.

The project follows [PEP 440](https://peps.python.org/pep-0440/) versioning and is in the
**beta** series (`0.1.0bN`) — the public API is stabilising but may still change before `0.1.0` stable.

## Unreleased

_Nothing yet._

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
