# 🚀 SnapAdmin — Declarative Django Admin & API

**Define your model fields once — get a feature-rich Django admin, a REST API with Swagger docs, a
GraphQL API, and optional Elasticsearch search.** Every surface can be switched on or off with a
single setting, and expensive `?search=` queries are routed to Elasticsearch automatically when a
model is mirrored there — plain listings stay on the database.

[![PyPI](https://img.shields.io/pypi/v/django-snapadmin?logo=pypi&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Downloads](https://img.shields.io/pypi/dm/django-snapadmin)](https://pypi.org/project/django-snapadmin/)
[![Python](https://img.shields.io/pypi/pyversions/django-snapadmin?logo=python&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Django](https://img.shields.io/badge/Django-5.2%20%7C%206.0-092E20?logo=django&logoColor=white)](https://djangoproject.com)
[![License](https://img.shields.io/github/license/drofji/django-snapadmin)](LICENSE)

📚 **[Full Documentation](https://drofji.github.io/django-snapadmin/)** — configuration guide, API reference, examples
📦 **[Django Packages](https://djangopackages.org/packages/p/django-snapadmin/)** — compare SnapAdmin against other Django admin packages
📝 **[Changelog](CHANGELOG.md)** · 🔒 **[Security policy](SECURITY.md)**

---

## ⚡ The Core Idea — 3 Steps, Full Stack

```python
# 1. Define a model
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name      = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)
    price     = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)
    available = snap.SnapBooleanField(default=True, filterable=True)

    # Optional: mirror to Elasticsearch, auto-delete after a year
    # es_storage_mode = snap_models.EsStorageMode.DUAL
    # data_retention_days = 365
```

```python
# 2. settings.py — every surface is a toggle
SNAPADMIN_REST_API_ENABLED = True
SNAPADMIN_GRAPHQL_ENABLED  = True
SNAPADMIN_SWAGGER_ENABLED  = True
```

```python
# 3. admin.py
from snapadmin.models import SnapModel
SnapModel.register_all_admins()
```

That's it — you get an Unfold-themed admin with filters, badges and change logging, `/api/product/`
CRUD with Swagger docs, an `allDemoProducts` GraphQL field, and typo-tolerant search when
Elasticsearch is on.

→ **[Field types](https://drofji.github.io/django-snapadmin/#snap-fields)** ·
**[SnapModel reference](https://drofji.github.io/django-snapadmin/#snap-model)** ·
**[Admin registration](https://drofji.github.io/django-snapadmin/#admin-registration)**

---

## 👀 What You'll See

> These screens come from the bundled **demo**, not the package — they illustrate what SnapAdmin
> generates for *your* models.

```
┌────────────────────────────────────────────────────────────┐
│  SnapAdmin                           🔍 Search...    admin ▾│
├──────────────┬─────────────────────────────────────────────┤
│  DEMO APP    │  Products                         + Add     │
│  Categories  │ ┌──────────────────────────────────────────┐│
│  Tags        │ │ Name            Price  In Stock  Category ││
│  Products    │ │ Premium Laptop  $249   ● Active   Audio   ││
│  Customers   │ │ Ergonomic Mouse $89    ● Active   Access. ││
│  Orders      │ │ USB-C Hub       $49    ○ Out      Electr. ││
│  Audit Logs  │ └──────────────────────────────────────────┘│
│  SYSTEM      │  Sidebar filters: Price range │ Available   │
│  Dashboard   │                   Category    │             │
└──────────────┴─────────────────────────────────────────────┘
```

Also generated: **Swagger UI** at `/api/docs/`, a **GraphQL playground** at `/api/graphql/`, and a
**system dashboard** at `/admin/snapadmin/dashboard/` showing per-model row counts, storage modes and
scheduled jobs.

---

## 📦 Installation

```bash
pip install django-snapadmin
```

Requires **Python ≥ 3.10** and **Django ≥ 5.2**. The package is **beta** — the public API is
stabilising but may still change before `0.1.0` stable, so pin an exact version in production.

Add the stack to `INSTALLED_APPS` — **order matters**, `unfold` must precede `django.contrib.admin`:

```python
INSTALLED_APPS = [
    "unfold", "unfold.contrib.filters", "unfold.contrib.forms", "unfold.contrib.inlines",
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "drf_spectacular", "django_filters", "graphene_django", "snapadmin",
    # your apps …
]
```

Installing SnapAdmin pulls in `django-unfold`, `djangorestframework`, `drf-spectacular`,
`django-filter` and `graphene-django` automatically — you only list them.

### Optional extras

The base install is self-contained and carries **only permissive licences** (MIT/BSD/Apache), so it
is safe for commercial and proprietary use. Opt into the rest:

| Extra | Pulls in | For |
|-------|----------|-----|
| `elasticsearch` | `elasticsearch` | Full-text search, `DUAL` / `ES_ONLY` models |
| `celery` | `celery`, `django-celery-beat`, `django-celery-results` | Background tasks (export, GDPR purge, digests, backups) |
| `backup` | `paramiko` | SFTP offsite database backups |
| `extra-settings` | `django-extra-settings` | In-admin dynamic key/value `Setting` model |
| `wysiwyg` | `django-ckeditor-5` | Rich-text fields — **bundles CKEditor 5 (GPL-or-commercial)** |
| `autocomplete-filter` | `django-admin-autocomplete-filter` | `AutocompleteFilter` list filters (LGPL) |
| `all` | everything above | — |

→ **[Full installation guide](https://drofji.github.io/django-snapadmin/#installation)** —
compatibility matrix, extras gotchas, and the licensing notes for `[wysiwyg]` and MySQL drivers.

---

## ✨ Features

**Admin**
- Declarative `list_display` / `search_fields` / `list_filter` straight from field kwargs
- Unfold-themed responsive UI, colour-coded [status badges](https://drofji.github.io/django-snapadmin/#status-badges), horizontal rows and tabs
- Date and numeric [range filters](https://drofji.github.io/django-snapadmin/#advanced-layout); field-level change logging (`old → new`) with a history view
- [Offline mode](https://drofji.github.io/django-snapadmin/#offline) — per-model IndexedDB prefetch, real backend health checks, sync on reconnect

**APIs**
- [REST CRUD](https://drofji.github.io/django-snapadmin/#api-rest) for every `SnapModel`, with Swagger + ReDoc, filters derived from field types, and streaming/async export
- [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) schema generated from the same models, auth-enforced on every traversed relation
- [API tokens](https://drofji.github.io/django-snapadmin/#api-tokens) hashed at rest (SHA-256), shown once, scoped per model — or plug in [JWT/session/custom auth](https://drofji.github.io/django-snapadmin/#integrating)
- Privacy controls: `api_exclude_fields`, `api_write_fields` (mass-assignment guard), `api_json_filters`, PII masking

**Elasticsearch**
- Per-model [`DB_ONLY` / `DUAL` / `ES_ONLY`](https://drofji.github.io/django-snapadmin/#elasticsearch) storage modes with auto-derived index mappings
- [Smart routing](https://drofji.github.io/django-snapadmin/#es-routing) — `?search=` on `DUAL` models runs fuzzy on ES; plain listings stay on SQL
- [`es_filter()`](https://drofji.github.io/django-snapadmin/#es-filter) structured term filters, [`es_aggregate()`](https://drofji.github.io/django-snapadmin/#es-aggregate) facets, [`es_scan()`](https://drofji.github.io/django-snapadmin/#es-scan) deep scan past the 10k window — each with a DB fallback when ES is off

**Operations**
- [GDPR retention](https://drofji.github.io/django-snapadmin/#gdpr) (`data_retention_days`) and an immutable audit trail
- [Error monitoring](https://drofji.github.io/django-snapadmin/#error-monitoring) — spike alerts + daily grouped email digests
- [3-2-1 database backups](https://drofji.github.io/django-snapadmin/#backups) — local, network share, and offsite FTPS/SFTP
- [Large-dataset tuning](https://drofji.github.io/django-snapadmin/#performance) — auto `list_select_related` (no admin N+1), estimated counts, per-model paging
- [Generic ETL](https://drofji.github.io/django-snapadmin/#integrating) — `upsert_from_source()` and `stale_sync()` with a `max_fraction` wipe guard
- [Structured logging](https://drofji.github.io/django-snapadmin/#logging) via `structlog`; [i18n](https://drofji.github.io/django-snapadmin/#i18n) in 10 locales
- [One-command diagnostics](https://drofji.github.io/django-snapadmin/#snapadmin-info) — `snapadmin_info` reports the version, connected services (DB / Elasticsearch / Celery), registered models and health as text or `--json`, with a `--health-check` readiness probe

Management commands: `snapadmin_info` (diagnostics & health), `snapadmin_reindex`, `db_backup`, `send_error_digest`, `purge_expired_data`.

> ⏱ **Nothing runs on its own.** SnapAdmin ships no daemon — the retention purge, digests and backups
> need a Celery Beat entry or a cron line. See
> **[Background tasks & scheduling](https://drofji.github.io/django-snapadmin/#celery)**.

---

## ⚙️ Configuration

Every surface is a plain Django setting; disabling one removes its URL routes entirely (404):

```python
SNAPADMIN_REST_API_ENABLED  = True    # REST CRUD endpoints
SNAPADMIN_GRAPHQL_ENABLED   = True    # GraphQL endpoint
SNAPADMIN_SWAGGER_ENABLED   = True    # Swagger UI + ReDoc
SNAPADMIN_ES_QUERY_ROUTING  = True    # route ?search= on DUAL models to Elasticsearch
SNAPADMIN_GRAPHQL_REQUIRE_AUTH = True # auth + per-model perms on every resolver
SNAPADMIN_URL_PREFIX        = ""      # relocate the whole API surface
```

→ **[Full settings reference](https://drofji.github.io/django-snapadmin/#env-vars)** — every
`SNAPADMIN_*` knob with its default, grouped by area.

---

## 🧩 Extending

SnapAdmin is meant to be customised, not forked:

- **Add field types** — subclass `SnapField` with your own admin introspection
- **Extend a `SnapModel`** — override `save()`, add managers, mix in your own behaviour
- **Add or override REST endpoints** — mount your router before SnapAdmin's
- **Swap auth, permissions and the ES client** — configuration, no code
- **Override admin templates and the dashboard** — standard Django template resolution

→ **[Extending & Overriding guide](https://drofji.github.io/django-snapadmin/#extending)**

---

## 🌟 Trying the Demo

The repository ships a runnable demo under [`demo/`](demo/) — example models (Product, Customer,
Order), a seeded database, and a Docker stack with PostgreSQL, Redis and Elasticsearch. It is **not**
published to PyPI; only the top-level `snapadmin/` package is.

```bash
git clone https://github.com/drofji/django-snapadmin.git
cd django-snapadmin
cp demo/dist.env demo/.env
docker compose -f demo/docker-compose.yml up --build
```

Then open `http://localhost:8000/admin/` (`admin` / `admin`).

→ **[Demo guide](https://drofji.github.io/django-snapadmin/#demo-setup)** — Traefik overlays with
HTTPS, the Elasticsearch profile, manual setup without Docker, and the seed command.

---

## 📖 Documentation

| Topic | |
|-------|--|
| Getting started | [Installation](https://drofji.github.io/django-snapadmin/#installation) · [SnapModel](https://drofji.github.io/django-snapadmin/#snap-model) · [Field types](https://drofji.github.io/django-snapadmin/#snap-fields) · [Admin registration](https://drofji.github.io/django-snapadmin/#admin-registration) |
| APIs | [REST](https://drofji.github.io/django-snapadmin/#api-rest) · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) · [Tokens](https://drofji.github.io/django-snapadmin/#api-tokens) · [Integrating auth / JWT / ETL](https://drofji.github.io/django-snapadmin/#integrating) |
| Search | [Elasticsearch modes](https://drofji.github.io/django-snapadmin/#elasticsearch) · [Query routing](https://drofji.github.io/django-snapadmin/#es-routing) · [Filters](https://drofji.github.io/django-snapadmin/#es-filter) · [Facets](https://drofji.github.io/django-snapadmin/#es-aggregate) · [Deep scan](https://drofji.github.io/django-snapadmin/#es-scan) |
| Operations | [Diagnostics (`snapadmin_info`)](https://drofji.github.io/django-snapadmin/#snapadmin-info) · [Celery & scheduling](https://drofji.github.io/django-snapadmin/#celery) · [GDPR](https://drofji.github.io/django-snapadmin/#gdpr) · [Backups](https://drofji.github.io/django-snapadmin/#backups) · [Error monitoring](https://drofji.github.io/django-snapadmin/#error-monitoring) · [Performance](https://drofji.github.io/django-snapadmin/#performance) |
| Reference | [All settings](https://drofji.github.io/django-snapadmin/#env-vars) · [Enterprise config](https://drofji.github.io/django-snapadmin/#enterprise-config) · [Extending](https://drofji.github.io/django-snapadmin/#extending) · [Migration guides](https://drofji.github.io/django-snapadmin/#migration-guides) |

Upgrading from `drofji-automatically-django-admin`? See the
**[migration guide](docs/migrations/drofji-automatically-django-admin_to_django-snapadmin.md)**.

---

## 🔒 Security

API tokens are hashed at rest, rich-text HTML is sanitized before display, GraphQL enforces
permissions on every traversed relation, and PII masking is available on both APIs. Report
vulnerabilities privately — see [SECURITY.md](SECURITY.md) for the policy, the supported-versions
row, and the production-hardening checklist.

Third-party dependency licences are inventoried in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The suite must stay green with 100% coverage on `snapadmin/`:

```bash
pytest
```

## 📜 License

MIT — see [LICENSE](LICENSE).
