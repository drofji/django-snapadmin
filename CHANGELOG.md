# Changelog

All notable changes to **django-snapadmin** are recorded here. This file is a concise,
version-by-version summary; the full, prose release notes for each version live in
[`docs/releases/`](docs/releases/) (shipped in the source distribution) and online in the
project documentation.

The project follows [PEP 440](https://peps.python.org/pep-0440/) versioning and is in the
**alpha** series (`0.1.0aN`) — the public API may still change between alphas.

## Unreleased

- **Security:** the system dashboard is now staff-gated by default (it exposed hostname,
  processor, OS, database name, service health and `ALLOWED_HOSTS` to anonymous callers).
  Opt out with `SNAPADMIN_DASHBOARD_PUBLIC = True`.
- **Added:** `SNAPADMIN_URL_PREFIX` relocates the entire route surface (REST, Swagger, GraphQL)
  under one extra path segment for projects that already own the mount point; route names are
  unchanged.
- **Added:** a Python × Django compatibility matrix in the README and `Framework :: Django :: 6.0`
  / per-minor Python classifiers; the suite runs green on Django 6.0.
- **Fixed:** the dashboard shows the real installed version and loads no external assets
  (Chart.js + Material Icons vendored, Font Awesome dropped for an inline SVG).
- **Fixed:** `SnapPhoneField` accepts spaced international numbers (e.g. `+49 89 1234567`).
- **Fixed:** the demo seeder no longer crashes on a cp1252 Windows console.
- **Docs:** GraphQL field-naming scheme documented; per-model admin extension points
  (`admin_mixins` / `admin_overrides` / `css_admin_files` / `js_admin_files`) documented; migration
  guide install name and `/api/` collision handling corrected.

See [`docs/releases/Unreleased.txt`](docs/releases/Unreleased.txt) for the full notes.

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
