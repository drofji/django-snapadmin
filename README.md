# 🚀 SnapAdmin — Declarative Django Admin & API Package

**SnapAdmin** is a declarative Django package that eliminates admin and API boilerplate. Define your model fields once — get a feature-rich Django admin (powered by Unfold), a full REST API with Swagger docs, a dynamic GraphQL API, and optional Elasticsearch full-text search. Every surface (REST, GraphQL, Swagger, ES) can be switched on or off with a single setting, and expensive `?search=` API queries are **automatically routed to Elasticsearch** when a model's data is mirrored there — plain listings stay on the database.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2+-green?logo=django)](https://djangoproject.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

📚 **[Full Documentation](https://drofji.github.io/django-snapadmin/)** — Configuration guide, API reference, examples

---

## 📦 The Package vs. 🌟 The Demo

This repository contains **two separate things** — read the one you care about:

**📦 SnapAdmin — the package** (what you `pip install django-snapadmin` and ship in *your* project).
This is the only code published to PyPI (the top-level `snapadmin/` folder). It gives you:

- Declarative **admin** (Unfold UI, badges, tabs/rows, range filters, field-level change logging)
- Auto **REST API** (CRUD + Swagger) and a dynamic **GraphQL** API, both permission-guarded
- Optional **Elasticsearch** search (`DB_ONLY` / `DUAL` / `ES_ONLY`) with smart `?search=` routing
- **API tokens** (hashed at rest) + pluggable auth (JWT/session/custom), custom `AUTH_USER_MODEL`
- **GDPR** data retention, **error monitoring** + email alerts, **3-2-1 database backups** (incl. SFTP)
- **Offline mode**, large-dataset tuning, generic **ETL upsert**, optional **user-management API**
- Management commands: `snapadmin_reindex`, `db_backup`, `send_error_digest`, `purge_expired_data`

→ Full list in **[SnapAdmin Package Features](#-snapadmin-package-features)** · customise it in
**[Extending & Overriding](#-extending--overriding)**.

**🌟 The Demo — the rest of this repo** (`demo/`, `sandbox/`, Docker, Traefik). *Not* published to
PyPI. It exists purely to let you **evaluate and develop** SnapAdmin: example domain models
(Product/Customer/Order), a seeded database, a Docker stack with PostgreSQL/Redis/Elasticsearch,
and a system dashboard.

→ See **[Demo Application Features](#-demo-application-features)** and
**[Running the Demo (Docker)](#-running-the-demo-docker)**. Every screenshot, model and `docker
compose` command below the package reference belongs to the demo, not the installable package.

---

## ⚡ The Core Idea — 3 Steps, Full Stack

Define a model. Configure settings. Everything works.

```python
# 1. Define a model
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name    = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)
    price   = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)
    available = snap.SnapBooleanField(default=True, filterable=True)

    # 2. (Optional) Enable Elasticsearch + GDPR cleanup
    # es_storage_mode = snap_models.EsStorageMode.DUAL  # DB + ES in sync
    # data_retention_days = 365  # Auto-delete records older than 1 year
```

That's it. You instantly get:

| What | How |
|------|-----|
| Django Admin | Auto-registered, filtered, searchable — no `admin.py` needed |
| REST API | Full CRUD at `/api/product/` with Swagger docs |
| GraphQL | Dynamic schema at `/api/graphql/` |
| ES Search | Optional — enable with `es_storage_mode = EsStorageMode.DUAL` |
| GDPR Cleanup | Optional — enable with `data_retention_days` on any model |

### Elasticsearch Storage Modes

| Mode | Where data lives | When to use |
|------|-----------------|-------------|
| `DB_ONLY` (default) | PostgreSQL only | Any model where search speed isn't critical |
| `DUAL` | PostgreSQL + Elasticsearch | Full-text search on large product/article catalogs |
| `ES_ONLY` | Elasticsearch only | High-frequency write logs, analytics events |

Searchable fields are declared in `es_mapping` (a `{field: ES mapping}` dict). Search
through `es_search()` — a fuzzy `multi_match` when ES is on, with an automatic ORM
fallback when it's off, so the same call works everywhere (`snap_search()` is an alias):

```python
# Fuzzy, typo-tolerant full-text search (default limit=20)
results = Product.es_search("wireles headphones")   # typo still matches
top5    = Product.es_search("laptop", limit=5)
browse  = Product.es_search(limit=100)              # no query → match-all, newest first

# ES_ONLY models are read *only* through es_search() (no DB table)
logs = SearchLog.es_search("error 404")

# DB_ONLY models answer too — falls back to an ORM icontains query
Article.es_search("django")   # works even with ELASTICSEARCH_ENABLED=False
```

For `DB_ONLY`/`DUAL` it returns a Django `QuerySet`; for `ES_ONLY` a lightweight
`EsQuerySet` of instances built from the index.

Full-text queries target only the **text-capable fields** of `es_mapping` (with
`lenient: true`), so mixed mappings with numeric/date fields never break a search.
Index-level settings — custom analyzers, shards — go into `es_index_settings`
(applied when the index is first created):

```python
class Product(snap_models.SnapModel):
    es_storage_mode = snap_models.EsStorageMode.DUAL
    es_mapping = {
        "name":  {"type": "text", "analyzer": "de_analyzer"},
        "price": {"type": "float"},
    }
    es_index_settings = {
        "analysis": {"analyzer": {"de_analyzer": {"type": "german"}}},
        "number_of_shards": 1,
    }
```

Don't want to write mappings at all? Set **`es_auto_mapping = True`** and the mapping
is derived from your model fields — `CharField`/`TextField` become `text` with a
`.raw` keyword subfield (exact match + aggregations), `Email`/`Slug`/`URL`/`UUID`/`IP`
→ `keyword`, integers/FK → `long`, `Decimal` → `scaled_float`, dates → `date`,
`JSONField` → `object`. Anything you declare in `es_mapping` overrides the derived
entry for that field:

```python
class SearchLog(snap_models.SnapModel):
    query         = snap.SnapCharField(max_length=255, searchable=True)
    results_count = snap.SnapIntegerField()

    es_storage_mode = snap_models.EsStorageMode.ES_ONLY
    es_auto_mapping = True          # mapping derived from the fields above
    # es_mapping = {"query": {"type": "search_as_you_type"}}   # optional override
```

> To change settings/mappings of an **existing** index: delete it, then run
> `Product.es_reindex_all()` — it recreates the index with the current definition
> and streams all rows through the **bulk API** (one round-trip per 500 docs, flat
> memory). Every swallowed ES failure (index creation, indexing, search fallback)
> is logged as a structlog `warning`, so outages are visible instead of silent.

---

## 👀 What You'll See

> 🌟 **Demo screens.** The models below (Product, Customer, Order, AuditLog) ship in the bundled
> **demo**, not the package — they illustrate what SnapAdmin generates for *your* models.

After running `docker compose up --build` and visiting `http://localhost:8000/admin/`:

**Django Admin (powered by Unfold)**
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
│  Showcase    │  Sidebar filters: Price range │ Available   │
│  SYSTEM      │                   Category    │             │
│  Dashboard   │                                             │
└──────────────┴─────────────────────────────────────────────┘
```

**REST API Docs (Swagger UI)** — `http://localhost:8000/api/docs/`
```
GET  /api/product/         List all products (filterable, paginated)
POST /api/product/         Create a product
GET  /api/product/{id}/    Retrieve a product
PUT  /api/product/{id}/    Update a product
DEL  /api/product/{id}/    Delete a product
GET  /api/customer/        …same for every SnapModel
```

**GraphQL Playground** — `http://localhost:8000/api/graphql/`
```graphql
query {
  # Field name is all<Applabel><Model>s — here app "demo" + model "Product".
  # Plain list (not a Relay connection), so select fields directly — no edges/node.
  allDemoProducts(first: 10, search: "laptop") {
    id name price available
  }
}
```
See [GraphQL field naming](#graphql-field-naming) for the full scheme.

**System Dashboard** — `http://localhost:8000/admin/snapadmin/dashboard/`
```
┌─────────────────────────────────────────────────────────┐
│  System Dashboard                          v0.1.0b2     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ Product  │ │ Customer │ │  Order   │ │ AuditLog │  │
│  │  50 rows │ │  50 rows │ │  50 rows │ │  50 rows │  │
│  │  [dual]  │ │ [db_only]│ │ [db_only]│ │ 90d ret. │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│  Cron Jobs                                              │
│  reindex_products_to_es   daily at 02:00               │
│  purge_expired_data        daily at 03:00               │
└─────────────────────────────────────────────────────────┘
```

---

## 📦 SnapAdmin Package Features

The core `snapadmin` package provides everything you need to bootstrap your project's admin and API:

| Feature | Description |
|---------|-------------|
| **Declarative Admin** | Configure `list_display`, `search_fields`, `list_filter` directly in your models using `SnapField`. |
| **Beautiful UI** | Native integration with `django-unfold` for a modern, responsive admin experience. |
| **Status Badges** | Easily add color-coded HTML badges for choices and status fields. |
| **Advanced Layout** | Support for horizontal field rows and tabbed interfaces within the admin form. |
| **Range Filters** | Built-in date and numeric range filters for efficient data exploration. |
| **Change Logging** | Automatic tracking of field-level changes (`old → new`) with a dedicated history view. |
| **Automatic REST API** | Instantly generated CRUD endpoints for every `SnapModel` with zero extra code. |
| **Dynamic GraphQL API** | Automatically generated GraphQL schema with support for complex data fetching. |
| **Token Auth** | Expirable API tokens with granular model-level access control. Keys are **hashed at rest** (SHA-256) and shown only once, at creation. |
| **Pluggable API Auth** | `SNAPADMIN_API_AUTHENTICATION_CLASSES` swaps in any DRF authenticators (JWT, session, custom); non-token auth falls back to plain Django model permissions. Works with a **custom `AUTH_USER_MODEL`**. |
| **Generic ETL Upsert** | `snapadmin.etl.upsert_from_source` streams an external source into a model via `bulk_create(update_conflicts=True)` — idempotent, no per-row ES writes, one bulk reindex at the end. |
| **User Management API** | Optional admin-only endpoints (`SNAPADMIN_USER_API_ENABLED`) to CRUD users, set passwords and assign permissions — for building frontend admin panels. |
| **Configurable** | Easily enable/disable REST API, GraphQL, Swagger docs, and search modes via settings. |
| **Elasticsearch Ready** | Multi-mode storage (`DB_ONLY`, `DUAL`, `ES_ONLY`) for blazing fast search. |
| **Smart ES Query Routing** | `?search=` REST queries on `DUAL` models run on Elasticsearch automatically (fuzzy, relevance-ranked); plain listings stay on the DB. Toggle globally (`SNAPADMIN_ES_QUERY_ROUTING`) or per model (`es_query_routing`). |
| **Auto ES Mapping** | `es_auto_mapping = True` derives the index mapping from your model fields (text + `.raw` keyword subfields, dates, numerics); `es_mapping` entries override per field, `es_index_settings` adds analyzers/shards. |
| **Secured GraphQL** | Every resolver enforces authentication (session or API token) + per-model Django permissions — the same contract as REST. `search`/`first`/`offset` arguments included. |
| **API Field Privacy** | `api_exclude_fields` hides sensitive columns from REST, GraphQL and schema introspection while the admin keeps showing them. |
| **API Write Allowlist** | `api_write_fields` restricts which fields accept a client-supplied value on REST create/update — a mass-assignment guard for status flags, ownership FKs and other fields that must only change server-side. A system check (`snapadmin.W004`) flags any model that hasn't set it. |
| **GDPR Data Retention** | Per-model `data_retention_days` parameter with automatic Celery cleanup task. |
| **Error Monitoring & Email Alerts** | Optional middleware records every unhandled exception / 5xx as a browsable `ErrorEvent`; emails a **spike alert** when N errors hit within 15 minutes and a **daily grouped digest** (Celery Beat or cron) — thresholds, window, recipients and send time all configurable. |
| **3-2-1 Database Backups** | Scheduled DB dumps (SQLite copy / `pg_dump`, gzip) shipped to configurable destinations — a **local** directory, a **network** server (mounted share), and an **offsite** copy over **FTP/FTPS** or **SSH/SFTP** — each with its **own frequency** and retention. |
| **Offline Mode** | Per-model `offline_mode` toggle: prefetches the last `offline_cache_limit` rows into IndexedDB, polls `/api/health/` for real backend availability, shows dynamic toasts + a saved-objects panel, and syncs on reconnect. |
| **Large-Dataset Tuning** | Auto-derived `list_select_related` (no admin N+1), plus per-model `list_per_page` / `show_full_result_count` knobs for million-row tables. |
| **Structured Logging** | Integrated `structlog` for readable local logs and JSON logs in production. |

---

## 🏗 Package Architecture

```
snapadmin/
├── api/             # REST & GraphQL API core: views, serializers, auth
├── management/      # Custom management commands
├── migrations/      # Core package migrations (e.g., APIToken)
├── static/          # UI assets (CSS, JS, SVG logos)
├── templates/       # Custom admin templates & dashboard
├── fields.py        # SnapField definitions with admin introspection
├── models.py        # SnapModel base, EsManager, and core logic
└── urls.py          # Auto-configurable API and documentation routes
```

---

## 🚀 Quickstart: Installation

### From PyPI (Recommended)
```bash
pip install django-snapadmin
```

### From GitHub (Latest/Development)
```bash
pip install git+https://github.com/drofji/django-snapadmin.git
```

### Compatibility

SnapAdmin requires **Python ≥ 3.10** and **Django ≥ 5.2** (no upper bound pinned). The package is
**beta** (`Development Status :: 4 - Beta`) — the public API is stabilising but may still change before
`0.1.0` stable; pin an exact version in production.

| | Versions | Status |
|---|----------|--------|
| **Python** | 3.10 · 3.11 · 3.12 · 3.13 | Supported (declared floor 3.10). Test suite is currently exercised on **3.12**. |
| **Django** | 5.2 (LTS) · 6.0 | Supported. The suite currently runs green on **Django 6.0**; **5.2** is the declared floor. |

> **No multi-version CI yet.** There is no automated Python×Django test matrix in CI — the numbers
> above reflect the declared support range and the versions the suite is actively run against during
> development, not a CI-enforced grid. Treat combinations outside the "currently exercised" cells as
> supported-but-untested, and report any incompatibility you hit.

### Optional extras

The base install is self-contained. Opt into extra integrations with pip extras:

| Extra | `pip install` | Pulls in | For |
|-------|---------------|----------|-----|
| `elasticsearch` | `django-snapadmin[elasticsearch]` | `elasticsearch` | Full-text search / `ES_ONLY` / `DUAL` models |
| `celery` | `django-snapadmin[celery]` | `celery`, `django-celery-beat`, `django-celery-results` | Background tasks (async export, GDPR purge, digests, backups) |
| `backup` | `django-snapadmin[backup]` | `paramiko` | SFTP offsite database backups |
| `extra-settings` | `django-snapadmin[extra-settings]` | `django-extra-settings` | An in-admin dynamic key/value `Setting` model (as the demo shows) |
| `wysiwyg` | `django-snapadmin[wysiwyg]` | `django-ckeditor-5` | Rich-text fields (`SnapRichTextField` / `wysiwyg=True`) |
| `autocomplete-filter` | `django-snapadmin[autocomplete-filter]` | `django-admin-autocomplete-filter` | `AutocompleteFilter` list filters in your own admin (LGPL) |
| `all` | `django-snapadmin[all]` | everything above | — |

> **`extra-settings` is optional and not used by SnapAdmin's core** (it was a required dependency
> before — now it isn't). Install the extra only if you want the dynamic `Setting` model. Two gotchas:
> - **`EXTRA_SETTINGS_ADMIN_APP` must match an `INSTALLED_APPS` entry.** If you register apps by their
>   `AppConfig` dotted path (`"shop.apps.ShopConfig"`), pass that dotted string — a bare label
>   (`"shop"`) won't be found. If you list bare labels, use the bare label.
> - **The `Setting` admin is Unfold-styled automatically.** `django-extra-settings` registers its own
>   plain `ModelAdmin`, which would render unstyled next to the rest of the themed site. SnapAdmin fixes
>   this for you: from `SnapAdminConfig.ready()` it re-registers the `Setting` admin (or its proxy, when
>   `EXTRA_SETTINGS_ADMIN_APP` re-homes it) with a class that inherits `unfold.admin.ModelAdmin` on top of
>   extra_settings' own configuration — `list_display`, `search_fields`, fieldsets and media are all
>   preserved, and the page picks up the Unfold theme. No manual subclassing needed. This works
>   regardless of whether `extra_settings` is listed before or after `snapadmin` in `INSTALLED_APPS`; the
>   only requirement is that **`django.contrib.admin` precede `snapadmin`** there (Django's project
>   template already does this). If you install the extra without Unfold, the styling step is simply a
>   no-op.

> **`wysiwyg` and commercial use.** The rich-text editor (`django-ckeditor-5`) bundles **CKEditor 5**,
> which is dual-licensed **GPL-2.0+ or commercial**. It is kept out of the base install so the core
> package carries no GPL/commercial code — the base is permissive (MIT/BSD/Apache) and safe for
> commercial and proprietary use. Opt into `[wysiwyg]` only if you want rich-text fields, and for a
> commercial product obtain a CKEditor licence (they offer a free tier) or supply your own widget.
> Without the extra, using a `wysiwyg=True` field raises a clear `ImproperlyConfigured` telling you to
> install it. (This is not legal advice — review dependency licences with counsel for commercial use.)

> **MySQL driver and commercial use.** SnapAdmin itself carries no MySQL driver dependency. However, if
> you configure Django to use MySQL (via `DATABASES[...]['ENGINE'] = 'django.db.backends.mysql'`), you
> must separately install `mysqlclient` (GPL-2.0-or-later). This is fine for internal or
> non-redistributed applications, but check your license posture before shipping a closed-source product.
> Django also supports the pure-Python driver `PyMySQL` — install it and call `pymysql.install_as_MySQLdb()`
> in your project's `__init__.py` or early in `settings.py` before Django loads. PyMySQL carries a
> permissive MIT license, trading off performance: it is pure-Python and slower than the C-extension
> `mysqlclient`, so production deployments typically prefer the latter. (This is not legal advice —
> review dependency licences with counsel for commercial use.)

---

## 🛠 Usage & Configuration

### 1. Configure Settings
Add the required apps to `INSTALLED_APPS` in `settings.py`. **Order matters** — `unfold`
and its contrib apps must come *before* `django.contrib.admin`. Add `django_ckeditor_5`
**only if you use wysiwyg rich-text fields** (the `[wysiwyg]` extra — see below):
```python
INSTALLED_APPS = [
    # Theme — must precede django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",

    # WYSIWYG — only with the [wysiwyg] extra (SnapRichTextField / wysiwyg=True)
    # "django_ckeditor_5",

    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # SnapAdmin stack (all required)
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "graphene_django",
    "snapadmin",

    # Optional — only with the [celery] extra
    # "django_celery_beat",
    # "django_celery_results",

    # Your apps …
]
```
> Installing `django-snapadmin` pulls in `django-unfold`, `djangorestframework`,
> `drf-spectacular`, `django-filter` and `graphene-django` automatically — you only need to
> list them in `INSTALLED_APPS`. `django-ckeditor-5` is **not** installed by default (it bundles
> CKEditor 5, a GPL/commercial editor) — add it via the `[wysiwyg]` extra only if you use rich-text
> fields.

### 2. Define your Model
```python
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)
    # Group fields into a single horizontal row
    price = snap.SnapDecimalField(max_digits=10, decimal_places=2, row="pricing")
    available = snap.SnapBooleanField(default=True, row="pricing")
```

### 3. Register Admin
```python
# admin.py
from snapadmin.models import SnapModel
SnapModel.register_all_admins()
```

### Theming & Styles

SnapAdmin ships its admin styling as two layers so it never forces theme
assumptions on installs that don't use Unfold:

| Stylesheet | Scope | When it loads |
|------------|-------|---------------|
| `snapadmin/css/admin.css` | Theme-agnostic core (field sizing, Select2, action bar, `:root` design tokens) | Always, on every SnapModel admin page |
| `snapadmin/css/admin-unfold.css` | Unfold-specific overrides (`.unfold`-scoped rules, dark-mode borders, Add-button fix) | **Only when `django-unfold` is installed** |

The Unfold layer is opt-in: it is appended automatically (after the core sheet,
so its rules win the cascade) only when Unfold is detected. A plain Django admin
install — or one on another theme — gets the core sheet alone and is never
styled with Unfold assumptions. The shared design tokens (`--primary-color`,
`--radius`, …) live in the core sheet so both layers reference the same values.

### Available Field Types

| Field | Django Equivalent | SnapAdmin Extras |
|-------|-------------------|-----------------|
| `SnapCharField` | `CharField` | searchable, filterable |
| `SnapTextField` | `TextField` | - |
| `SnapRichTextField` | `TextField` | `wysiwyg=True` preset - no extra arg needed |
| `SnapEmailField` | `EmailField` | - |
| `SnapPhoneField` | `CharField` | phone validation, max_length=20 preset |
| `SnapColorField` | `CharField` | hex color validation (#RRGGBB), max_length=7 preset |
| `SnapSlugField` | `SlugField` | max_length=50 preset |
| `SnapURLField` | `URLField` | - |
| `SnapUUIDField` | `UUIDField` | - |
| `SnapIntegerField` | `IntegerField` | filterable |
| `SnapSmallIntegerField` | `SmallIntegerField` | filterable |
| `SnapPositiveIntegerField` | `PositiveIntegerField` | filterable |
| `SnapPositiveSmallIntegerField` | `PositiveSmallIntegerField` | filterable |
| `SnapPositiveBigIntegerField` | `PositiveBigIntegerField` | filterable |
| `SnapBigIntegerField` | `BigIntegerField` | filterable |
| `SnapFloatField` | `FloatField` | filterable |
| `SnapDecimalField` | `DecimalField` | filterable |
| `SnapDateField` | `DateField` | date range filter |
| `SnapDateTimeField` | `DateTimeField` | date range filter |
| `SnapTimeField` | `TimeField` | - |
| `SnapDurationField` | `DurationField` | - |
| `SnapBooleanField` | `BooleanField` | filterable |
| `SnapJSONField` | `JSONField` | - |
| `SnapGenericIPAddressField` | `GenericIPAddressField` | - |
| `SnapFileField` | `FileField` | extension/size/encoding validation |
| `SnapImageField` | `ImageField` | - |
| `SnapForeignKey` | `ForeignKey` | autocomplete |
| `SnapOneToOneField` | `OneToOneField` | autocomplete |
| `SnapManyToManyField` | `ManyToManyField` | - |
| `SnapFunctionField` | - | computed display column |
| `SnapStatusBadgeField` | - | colored HTML badge column |

> **Wysiwyg HTML is sanitized in the changelist.** Rich-text fields (`wysiwyg=True` /
> `SnapRichTextField`) store raw HTML and show it on the changelist, so their value is
> passed through an HTML sanitizer ([`nh3`](https://pypi.org/project/nh3/)) before display —
> `<script>`, inline event handlers and unsafe URLs are stripped, preventing stored XSS from
> untrusted writers. Pass `safe_html=True` on the field to render fully-trusted HTML verbatim,
> or set `SNAPADMIN_HTML_SANITIZER` to a dotted path (`"myapp.security.clean_html"`,
> a `Callable[[str], str]`) to plug in your own policy.

---

## ⚙️ Feature Toggles & Advanced Settings

Every SnapAdmin surface can be enabled or disabled independently via Django settings.
Disabling a toggle removes the corresponding URL routes entirely (requests return 404):

```python
# Feature toggles
SNAPADMIN_REST_API_ENABLED = True   # REST CRUD endpoints (/api/models/…, /api/tokens/…)
SNAPADMIN_GRAPHQL_ENABLED = True    # GraphQL endpoint (/api/graphql/)
SNAPADMIN_SWAGGER_ENABLED = True    # Swagger UI + ReDoc (/api/docs/, /api/redoc/)
SNAPADMIN_URL_PREFIX = ""           # Extra segment prepended to every snapadmin route (see below)
SNAPADMIN_DASHBOARD_PUBLIC = False  # Dashboard is staff-gated by default; True serves it to anyone
ELASTICSEARCH_ENABLED = False       # Elasticsearch integration as a whole

# Smart ES query routing (see "REST API in Practice" below)
SNAPADMIN_ES_QUERY_ROUTING = True   # Route ?search= on DUAL models to Elasticsearch
SNAPADMIN_ES_SEARCH_LIMIT = 1000    # Max hits fetched from ES per routed search

# Observability & GraphQL security
SNAPADMIN_QUERY_BACKEND_HEADER = True  # X-Snap-Query-Backend header on list responses
SNAPADMIN_GRAPHQL_REQUIRE_AUTH = True  # Auth + per-model perms on every resolver
SNAPADMIN_GRAPHIQL_ENABLED = DEBUG     # GraphiQL playground — keep out of production

# Integration points (v0.1.0a6)
SNAPADMIN_USER_API_ENABLED = False     # Admin-only user-management API (/api/users/, /api/permissions/)
# API authentication — dotted paths, like DRF's own setting. Unset = SnapAdmin
# token auth only. Add SessionAuthentication and/or JWT here:
SNAPADMIN_API_AUTHENTICATION_CLASSES = [
    "snapadmin.api.authentication.APITokenAuthentication",
    "rest_framework.authentication.SessionAuthentication",
]

# Enterprise config (v0.1.0a8)
SNAPADMIN_ANALYTICS_DB_ALIAS = ""      # Route read-only list/retrieve to a replica; writes stay on 'default'
SNAPADMIN_MASKED_FIELDS = {}           # {"app.Model": ["email", ...]} → mask PII in admin + API
SNAPADMIN_SSO_PROVIDERS = {}           # {"azure": {"label": "...", "url": "/accounts/azure/login/"}}
SNAPADMIN_SSO_ALLOWED_HOSTS = []       # ["login.microsoftonline.com"] → restrict absolute SSO provider urls to these hosts
SNAPADMIN_NESTED_APPS = {}             # {"snapadmin": "auth"} → fold sections under existing app groups
SNAPADMIN_HIDDEN_APPS = []             # ["silk"] → hide app groups from the admin index
SNAPADMIN_APP_LABELS = {}              # {"auth": "Administration"} → rename an app group heading

# Compliance & audit
SNAPADMIN_AUDIT_LOG_ENABLED = True     # Record every admin create/update/delete as an immutable audit row
SNAPADMIN_AUDIT_RETENTION_DAYS = 365   # Retention for `snapadmin_audit_export --purge`

# Large-dataset performance
SNAPADMIN_ESTIMATED_COUNT = True              # Fast PG reltuples estimate for huge, unfiltered changelists
SNAPADMIN_ESTIMATED_COUNT_THRESHOLD = 100000  # Only estimate above this row count; exact below

# Async background export (needs Celery + a broker)
SNAPADMIN_EXPORT_ENABLED = True        # POST /api/exports/ → background CSV/JSON export jobs
SNAPADMIN_EXPORT_CHUNK_SIZE = 1000     # Rows per chunk (progress + resume granularity)
SNAPADMIN_EXPORT_DIR = BASE_DIR / "exports"   # Where export files are written
```

> **Configuration health checks.** SnapAdmin registers Django system checks, so `python manage.py check`
> (and `runserver` / CI) flags a mis-set `SNAPADMIN_*` value — an unknown replica alias, a masked-field
> typo, a nested-app target that isn't installed, an SSO provider with no URL — with an actionable hint
> instead of failing silently. Migrating from the legacy `drofji-*` packages? See the
> **[Migration Guides](https://drofji.github.io/django-snapadmin/#migration-guides)**.

> **Ecosystem compatibility.** SnapAdmin only auto-registers `SnapModel`s and never clobbers an existing
> admin, so third-party packages coexist cleanly. To layer a package's admin behaviour on top of the
> auto-generated one, list its mixin in `admin_mixins` (e.g. `admin_mixins = [ImportExportModelAdmin]`);
> to hand a model's admin over entirely, set `admin_enabled = False`. Full matrix (mptt, guardian,
> reversion, debug-toolbar, import-export, simple-history, django-filter, taggit) in the
> **[Ecosystem Compatibility guide](https://drofji.github.io/django-snapadmin/#compatibility)**.

### 📤 Async background export

Large synchronous exports time out; SnapAdmin offloads them to Celery and tracks progress:

```
POST /api/exports/            {"app_label": "demo", "model": "Product", "export_format": "csv"}
                              → 201 {"id": "...", "status": "pending"|"completed", ...}
GET  /api/exports/<id>/       → status, processed_rows/total_rows, progress_percent, eta_seconds
POST /api/exports/<id>/cancel/  → stop between chunks
GET  /api/exports/<id>/download/ → the finished CSV/JSON file
```

Exports run in **resumable chunks** — if the worker restarts, the writer continues from the last
persisted chunk (`acks_late`) instead of starting over — and are **cancellable** mid-run. Jobs are
private to their requester (superusers see all); the caller must hold the target model's `view`
permission. Only `SnapModel`-backed models are exportable.

#### No-Celery path — count + synchronous streaming export

When you don't run Celery, every dynamic model endpoint also exposes two synchronous helpers that
reuse the same filter, search and permission backends as the list view:

```
GET /api/models/<app>/<Model>/count/?<filters>          → {"count": N}   (match count, no rows)
GET /api/models/<app>/<Model>/export/?<filters>[&limit=N]  → NDJSON stream of ALL matching rows
```

`count/` returns just the size of the filtered queryset — handy for sizing a paginator without
pulling data. `export/` streams the **entire** filtered queryset as newline-delimited JSON
(`application/x-ndjson`), one serialized object per line, with **no pagination**; pass `?limit=N`
to cap the row count. Rows are pulled lazily in chunks (tunable via `SNAPADMIN_EXPORT_CHUNK_SIZE`,
default 1000 — shared with the async export) so arbitrarily large tables never materialise in
memory. Both require the model's `view` permission, just like `list`.

### 🌍 Internationalization (i18n)

SnapAdmin's UI strings are wrapped in `gettext` and ship compiled translation catalogs for **10
locales** — English, Russian, German, **Swiss German** (`de_CH`, ß→ss), French, **Swiss French**
(`fr_CH`), Spanish, Italian, Polish, Dutch. A missing string falls back to English automatically.

```python
# settings.py  (the sandbox already wires this up)
MIDDLEWARE = [..., "django.middleware.locale.LocaleMiddleware", ...]  # after SessionMiddleware
LANGUAGES = [("en", "English"), ("ru", "Russian"), ("de", "German"), ("de-ch", "Swiss German"), ...]
```

```python
# urls.py — backs the language switcher
path("i18n/", include("django.conf.urls.i18n")),
```

Drop the accessible language selector into any page (e.g. an admin login/base override) with
`{% include "snapadmin/language_switcher.html" %}` — it posts to Django's `set_language` and renders
nothing when only one language is configured.

### 🏢 Enterprise config (v0.1.0a8)

Four settings-driven features, each safe on stock single-database installs (inert until configured):

- **Read-replica routing** — set `SNAPADMIN_ANALYTICS_DB_ALIAS` to a `DATABASES` alias and every
  auto-generated **read-only** API list/retrieve is pinned to that replica via `.using()`. Writes
  (POST/PUT/PATCH/DELETE) and the object lookups behind them always stay on `default`, so
  replication lag can never stale or drop a mutation. An empty or unknown alias is a no-op.
- **PII masking** — declare sensitive fields once in `SNAPADMIN_MASKED_FIELDS`
  (`{"users.UserModel": ["email", "phone_number"]}`). They are obfuscated in the admin changelist,
  removed from the admin change form, and masked in the REST API responses for anyone who is not a
  superuser or a holder of the `snapadmin.view_raw_pii` permission. Emails become `a***@domain.com`,
  other values `+3********78`.
- **SSO/OAuth2 login buttons** — SnapAdmin only *renders* the providers you already wired into
  `AUTHENTICATION_BACKENDS`/URLconf (django-allauth, social-auth, mozilla-django-oidc); it adds no
  auth dependency. `SNAPADMIN_SSO_PROVIDERS` drives login-page buttons (add the
  `snapadmin.sso.sso_providers` context processor and `{% include "snapadmin/sso_buttons.html" %}`
  to a login override) and a public `GET /api/sso-providers/` endpoint for headless frontends.
  Protocol-relative provider URLs (`//host/path`) are always dropped — they have no legitimate use
  here and would otherwise resolve to an external origin. If `SNAPADMIN_SSO_PROVIDERS` is ever built
  from something other than a hardcoded literal (an env var, an admin-editable setting, a generated
  value), opt into `SNAPADMIN_SSO_ALLOWED_HOSTS` (a list of hostnames) to also restrict *absolute*
  provider URLs to a known allowlist of identity providers.
- **Admin-index nesting** — `SNAPADMIN_NESTED_APPS` folds auto-generated sections under existing app
  groups so the index stays uncluttered; `SNAPADMIN_HIDDEN_APPS` hides groups and
  `SNAPADMIN_APP_LABELS` renames headings — no custom `AdminSite` required.

### 🛡️ Compliance & audit

- **Unalterable audit trail** (DORA / ISO 27001) — every admin create/update/delete is recorded as an
  immutable `SnapadminAuditLog`: **who** (actor + IP + User-Agent), **what** (target object + before/
  after field diff) and **when** (tz-aware timestamp). Rows are append-only (`save`/`delete` raise once
  persisted) and the admin is read-only. On by default (`SNAPADMIN_AUDIT_LOG_ENABLED`), retained for
  `SNAPADMIN_AUDIT_RETENTION_DAYS` (365). Export to a SIEM with `manage.py snapadmin_audit_export`
  (`--format json|csv`, `--since/--until`, `--action/--app/--model`, `--purge`).

### ⚡ Large-dataset performance

- **Automatic eager loading** — every SnapAdmin admin auto-derives `list_select_related` from the FK
  columns it shows, and the REST API auto-`select_related`s FKs + `prefetch_related`s M2M — no N+1,
  zero config.
- **Timeout-proof counts** — on multi-million-row tables the changelist's `SELECT COUNT(*)` is the
  costliest query. `EstimatedCountPaginator` swaps it for PostgreSQL's instant `reltuples` estimate on
  **unfiltered** listings past `SNAPADMIN_ESTIMATED_COUNT_THRESHOLD`, and stays exact for small or
  filtered views and non-PostgreSQL databases. On by default (`SNAPADMIN_ESTIMATED_COUNT`); pair with
  `show_full_result_count = False` per model to also drop the second unfiltered count.

Per-model opt-out from query routing (e.g. when the ES mirror of one model lags):

```python
class Product(snap_models.SnapModel):
    es_storage_mode = snap_models.EsStorageMode.DUAL
    es_query_routing = False   # this model's API searches always run on the DB
```

Hide sensitive columns from the whole API surface (REST, GraphQL, schema
introspection) while keeping them in the admin:

```python
class AuditLog(snap_models.SnapModel):
    action     = snap.SnapCharField(max_length=100, searchable=True)
    user_email = snap.SnapEmailField()          # PII

    api_exclude_fields = ["user_email"]         # never leaves the server via API
```

Restrict which fields a client can actually write through the API — everything
else stays read-only (still returned in responses unless also excluded above),
so subclassing `SnapModel` doesn't silently make every column mass-assignable:

```python
class Account(snap_models.SnapModel):
    owner     = snap.SnapForeignKey(User, on_delete=django_models.CASCADE)
    is_locked = snap.SnapBooleanField(default=False)   # server-only status flag
    balance   = snap.SnapDecimalField(max_digits=12, decimal_places=2)  # computed

    api_write_fields = ["owner"]   # is_locked / balance never accept a client value
```

Leaving `api_write_fields` unset (the default) keeps every non-excluded field
writable, matching prior behaviour — a `snapadmin.W004` system check warns on
every model that hasn't made the choice explicitly.

### Vetoing deletes via the API

Beyond model `delete` permissions, you can forbid deleting **specific objects** through the
dynamic model API without re-mounting any routes. Two extension points are consulted before a
`DELETE`, and **both** must allow it (either returning `False` → **403 Forbidden**):

```python
# 1) Per-model hook — override on the SnapModel:
class Account(snap_models.SnapModel):
    is_system = snap.SnapBooleanField(default=False)

    def api_can_delete(self, request) -> bool:
        return not self.is_system            # system rows are undeletable
```

```python
# 2) Project-wide guard — a dotted path (or callable) taking (request, obj):
# settings.py
SNAPADMIN_API_DELETE_GUARD = "myproject.guards.protect_superusers"

# myproject/guards.py
def protect_superusers(request, obj) -> bool:
    return not getattr(obj, "is_superuser", False)
```

Both default to allowing; unset the setting to rely solely on model permissions and the hook.

## 🧩 Integrating with Your Project

SnapAdmin is built to drop into a real project without forcing you to write bridge code.

### Pluggable API authentication (JWT, session, custom)

By default the API accepts SnapAdmin's own token auth. Point
`SNAPADMIN_API_AUTHENTICATION_CLASSES` at any DRF authenticators — the model CRUD,
schema and token endpoints all honour it. With non-token auth (session/JWT), model
permissions fall back to plain Django model permissions (a token additionally applies
its `allowed_models` scope):

```python
SNAPADMIN_API_AUTHENTICATION_CLASSES = [
    "rest_framework_simplejwt.authentication.JWTAuthentication",  # pip install djangorestframework-simplejwt
    "rest_framework.authentication.SessionAuthentication",
    "snapadmin.api.authentication.APITokenAuthentication",        # keep tokens working too
]
```

**JWT in three steps** (using `djangorestframework-simplejwt`):

```python
# 1. settings.py — add the authenticator (above) and the app
INSTALLED_APPS += ["rest_framework_simplejwt"]

# 2. urls.py — expose obtain/refresh endpoints
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
urlpatterns += [
    path("api/token/",        TokenObtainPairView.as_view()),
    path("api/token/refresh/", TokenRefreshView.as_view()),
]
```

```bash
# 3. authenticate, then call any SnapAdmin endpoint with the Bearer token
ACCESS=$(curl -s -X POST -d 'username=admin&password=…' localhost:8000/api/token/ | python -c "import sys,json;print(json.load(sys.stdin)['access'])")
curl -H "Authorization: Bearer $ACCESS" localhost:8000/api/models/demo/Product/
```

> The setting applies to the REST API. GraphQL keeps its own auth contract
> (`SNAPADMIN_GRAPHQL_REQUIRE_AUTH`) and already accepts SnapAdmin tokens + sessions.

### Custom user model

`APIToken` (and everything built on it) targets `settings.AUTH_USER_MODEL`, so a project
with a custom user model works out of the box — no monkey-patching. Usernames are read
via `get_username()`, so a model with `USERNAME_FIELD = "email"` is fine.

### Configurable Elasticsearch client

Beyond `ELASTICSEARCH_URL`, connection options are configurable — pass API keys, TLS or
retries, or supply a fully custom client for cloud setups:

```python
ELASTICSEARCH_KWARGS = {                       # merged into Elasticsearch(...)
    "api_key": "…",
    "ca_certs": "/etc/ssl/es.pem",
    "request_timeout": 30,                     # overrides the 5s default
    "max_retries": 3, "retry_on_timeout": True,
}
# …or take full control (cloud_id, sniffing, custom transport):
SNAPADMIN_ES_CLIENT_FACTORY = "myproject.es.make_client"   # a zero-arg callable → client
```

### Bulk reindex command

Rebuild the Elasticsearch index for every ES-enabled `SnapModel`, or one model, without
writing a script:

```bash
python manage.py snapadmin_reindex                       # all DUAL / ES_ONLY / es_index_enabled models
python manage.py snapadmin_reindex --model demo.Product  # one model
python manage.py snapadmin_reindex --chunk-size 1000     # tune the bulk batch size
```

#### Reindex over HTTP (admin only)

For ops without shell access, the same bulk reindex is available as an **opt-in**, staff-only
endpoint — off by default:

```python
SNAPADMIN_REINDEX_API_ENABLED = True    # the endpoint 404s until you set this
SNAPADMIN_REINDEX_API_ASYNC = False     # True → offload to the snapadmin.run_es_reindex Celery task
```

```
POST /api/es/reindex/            {"chunk_size": 1000}   # optional; requires an IsAdminUser session/token
  → 200 {"async": false, "models": N, "indexed_models": …, "errored_models": …, "results": {…}}
  → 202 {"async": true,  "task_id": "…"}                # when SNAPADMIN_REINDEX_API_ASYNC is on
```

It reindexes every ES-enabled `SnapModel` (`es_reindex_all`). With async on but Celery not installed
it returns **503** with an actionable message. Non-staff callers get **403**; while disabled, staff
get **404**.

### Generic ETL — external source → SnapModel upsert

Import from any external system (remote DB, CSV, API) with a streamed bulk upsert — no
per-row saves, no per-row ES writes; a single bulk reindex runs at the end:

```python
from snapadmin.etl import upsert_from_source

def rows_from_remote():                        # any iterable of dicts (e.g. a streamed cursor)
    for r in remote_cursor:
        yield {"code": r.code, "base": "EUR", "rate": r.rate}

summary = upsert_from_source(
    ExchangeRate, rows_from_remote(),
    unique_fields=["code"],                     # conflict target (needs a unique constraint)
    batch_size=1000,                            # rows per bulk_create round-trip
)
# {"processed": 5231, "batches": 6, "reindex": {"indexed": 5231}}
```

It uses `bulk_create(update_conflicts=True)`, so rematched rows are **updated**, not
duplicated — re-running an import is idempotent. Because it bypasses `Model.save()`,
model-level `full_clean()` validation is not run (validate upstream for speed). Demo:
`python manage.py sync_exchange_rates`.

Runs on **PostgreSQL, SQLite and MySQL/MariaDB**. `unique_fields` is always the conflict
target; on MySQL/MariaDB (which upsert via `ON DUPLICATE KEY UPDATE`) it is inferred from the
matching unique index instead of being passed explicitly, so the same call works on every
backend without a `NotSupportedError`.

### Optional user-management API (admin-only)

Enable `SNAPADMIN_USER_API_ENABLED = True` to expose admin-only endpoints for managing
users and their permissions — handy for building a frontend admin panel. Every endpoint
requires a **staff** user:

```bash
GET/POST                 /api/users/                    # list / create users
GET/PATCH/DELETE         /api/users/<pk>/               # manage one user
POST                     /api/users/<pk>/set-password/  # {"password": "…"}
POST                     /api/users/<pk>/permissions/   # {"permissions": ["demo.view_product", …]}
GET                      /api/permissions/              # all assignable permissions (for pickers)
```

---

## 🧬 Extending & Overriding

SnapAdmin is designed to be extended without forking. When a generated surface isn't enough,
reach for the closest hook below — each example is self-contained and drops into *your* project.

### 1. Add your own field type

Any Django field becomes a SnapAdmin field by mixing in `SnapField` and running the incoming
kwargs through `_initializeSnapLogic()` (which peels off the Snap-only options like `searchable`,
`filterable`, `show_in_list`) and `handleDjangoKwargs()` (which returns the Django-safe kwargs):

```python
# yourapp/fields.py
from django.db import models
from snapadmin.fields import SnapField

class SnapMoneyField(models.DecimalField, SnapField):
    """DecimalField pre-set for currency, filterable in the admin by default."""

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("filterable", True)          # Snap option
        kwargs = self._initializeSnapLogic(**kwargs)   # consume Snap options
        cleaned = self.handleDjangoKwargs(**kwargs)    # Django-only kwargs
        super().__init__(**cleaned)
```

```python
class Invoice(snap_models.SnapModel):
    total = SnapMoneyField(show_in_list=True)
```

### 2. Reuse the built-in validators

The validators behind `SnapPhoneField` / `SnapColorField` / `SnapFileField` are public and work on
any plain Django field:

```python
from django.db import models
from snapadmin.validators import SnapPhoneValidator, SnapFileValidator

class Contact(models.Model):
    phone    = models.CharField(max_length=20, validators=[SnapPhoneValidator()])
    contract = models.FileField(
        validators=[SnapFileValidator(allowed_extensions=["pdf"], max_size_bytes=10 * 1024 * 1024)],
    )
```

### 3. Extend a `SnapModel`

`SnapModel` is a normal abstract model — add methods, a custom manager, override `save()`, or tune
the Elasticsearch hooks. Everything the admin/API generate is driven off the fields you declare, so
extra methods never break the generated surfaces:

```python
class Product(snap_models.SnapModel):
    name  = snap.SnapCharField(max_length=200, searchable=True)
    price = SnapMoneyField()

    es_storage_mode   = snap_models.EsStorageMode.DUAL
    es_index_settings = {"number_of_shards": 2}        # override index creation

    def save(self, *args, **kwargs):                   # custom write behaviour
        self.name = self.name.strip()
        super().save(*args, **kwargs)                  # keeps ES mirroring intact

    @classmethod
    def on_sale(cls):                                  # your own query helper
        return cls.objects.filter(price__lt=100)
```

**Customising the generated `ModelAdmin` (no `admin.py` needed).** `register_admin()` builds one
`ModelAdmin` per model from class attributes you declare on the `SnapModel` itself — so you extend the
admin by setting attributes, not by writing an `admin.py`:

| Attribute | Type | Effect on the generated `ModelAdmin` |
|-----------|------|--------------------------------------|
| `admin_mixins` | `list[type]` | Extra `ModelAdmin` base classes **prepended** to the MRO (e.g. `[ImportExportModelAdmin]`, `[GuardedModelAdmin]`) — the integration hook for other admin packages |
| `admin_overrides` | `dict[str, Any]` | Arbitrary attributes/methods merged onto the class **last**, so they win over the generated defaults (e.g. `{"list_per_page": 25, "get_queryset": my_fn}`) |
| `css_admin_files` | `str \| list[str]` | Extra stylesheets appended to the admin `Media.css` (static paths) |
| `js_admin_files` | `str \| list[str]` | Extra scripts appended to the admin `Media.js` (static paths) |

```python
class Product(snap_models.SnapModel):
    name = snap.SnapCharField(max_length=200, searchable=True)

    admin_mixins    = [ImportExportModelAdmin]          # extra ModelAdmin base(s)
    css_admin_files = "yourapp/product_admin.css"       # → generated Media.css
    js_admin_files  = ["yourapp/product_admin.js"]      # → generated Media.js
    admin_overrides = {"list_per_page": 25}             # wins over generated defaults
```

These are the supported replacement for hand-writing `class Media` / a custom `admin.site.register`:
SnapAdmin owns the registration (`register_admin()` per model, `register_all_admins()` for a whole app
label), and merges your `css_admin_files` / `js_admin_files` into the `Media` it generates. The
`formatted_id` helper (a zero-padded id display used by the generated admin) is also public on
`snapadmin.models` if you need the same formatting in an override. All three of
`css_admin_files` / `js_admin_files` / `formatted_id` are current, supported API.

> **Static namespace.** SnapAdmin serves its own admin assets under the **`snapadmin/`** static
> namespace (`snapadmin/js/admin.js`, `snapadmin/css/admin.css`, …) — *not* the predecessor package's
> path. If you are migrating from `drofji-automatically-django-admin` and hardcoded a static URL or a
> template `{% static %}` path pointing at its old `drofji_autoadmin/…` (or bare `admin.js`) assets,
> update it to the `snapadmin/…` namespace; a mechanical class rename won't fix a hardcoded asset path.

### 4. Add custom REST endpoints (or override CRUD for one model)

SnapAdmin's REST layer is a single generic `DynamicModelViewSet` routed by `app_label`/`model_name`.
To add behaviour for a specific model, register **your own** DRF viewset in *your* `urls.py` — put it
**before** SnapAdmin's routes so it wins for that path:

```python
# yourapp/api.py
from rest_framework import viewsets, decorators, response
from snapadmin.api.serializers import get_serializer_for_model
from .models import Product

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = get_serializer_for_model("yourapp", "product")  # reuse Snap's serializer

    @decorators.action(detail=False)
    def on_sale(self, request):
        page = self.paginate_queryset(Product.on_sale())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)
```

```python
# urls.py — your router first, then SnapAdmin's
from rest_framework.routers import DefaultRouter
from yourapp.api import ProductViewSet

router = DefaultRouter()
router.register(r"api/product", ProductViewSet, basename="product")

urlpatterns = [
    *router.urls,
    path("", include("snapadmin.urls")),   # generic CRUD for every other model
]
```

> **Already own `/api/`?** SnapAdmin's routes (`/models/…`, `/docs/`, `/graphql/`, `/tokens/`, …) are
> mounted wherever you `include("snapadmin.urls")` — so the simplest fix is to mount them under a path
> you don't use, e.g. `path("snapadmin/", include("snapadmin.urls"))`. If you can't change the mount
> point (you include SnapAdmin at the site root, or an intermediate URLconf pins it under `/api/`), set
> **`SNAPADMIN_URL_PREFIX = "snapadmin/"`** to relocate the *entire* surface — REST, Swagger and GraphQL
> — under that extra segment (e.g. `/api/snapadmin/models/…`). Route **names are unchanged**, so
> `reverse("model-list", …)` and `{% url %}` keep working; leave it empty (the default) for the
> historical layout. To confirm nothing collides after wiring it up, run
> `python manage.py check` and `python manage.py show_urls` (django-extensions) and look for duplicate
> paths.

Prefer to keep the generic viewset but change *how* it behaves globally (extra filters, custom
pagination)? Subclass `DynamicModelViewSet` and point your own route at it.

### 5. Swap authentication, permissions & the ES client (config, no code)

Several extension points need no subclassing — just settings:

| To change… | Set | See |
|------------|-----|-----|
| Who can call the API (JWT / session / custom) | `SNAPADMIN_API_AUTHENTICATION_CLASSES` | [Pluggable API auth](#pluggable-api-authentication-jwt-session-custom) |
| Which model a token may touch | `APIToken.allowed_models` (+ Django perms) | [API Token Security](#-api-token-security) |
| How the Elasticsearch client is built | `ELASTICSEARCH_KWARGS` / `SNAPADMIN_ES_CLIENT_FACTORY` | [Configurable ES client](#configurable-elasticsearch-client) |
| Whether GraphQL requires auth / exposes GraphiQL | `SNAPADMIN_GRAPHQL_REQUIRE_AUTH` / `SNAPADMIN_GRAPHIQL_ENABLED` | [Feature Toggles](#-feature-toggles--advanced-settings) |
| Hiding fields from every API surface | `api_exclude_fields` on the model | table above |
| Restricting which fields REST create/update can write | `api_write_fields` on the model | table above |

> **GraphQL** is generated dynamically from your `SnapModel`s and enforces the *same* per-model
> permissions and `api_exclude_fields` as REST — extend it by adding/removing SnapModels and
> tuning those settings, not by editing the schema.

### 6. Override admin templates & the dashboard

SnapAdmin's templates live under the `snapadmin/` template namespace, so Django's normal template
resolution lets you shadow any of them from your project — no fork required. Ensure your project's
`templates/` dir is ahead of the app in `TEMPLATES['DIRS']`, then drop in a same-named file:

```
yourproject/templates/snapadmin/dashboard.html            # replace the system dashboard
yourproject/templates/snapadmin/email/error_alert.html    # rebrand the spike-alert email
yourproject/templates/snapadmin/email/error_digest.html   # rebrand the daily digest
```

---

## 🔎 REST API in Practice — Query Examples

All examples assume an API token (create one in the admin under **API Tokens**, or via
`POST /api/tokens/` — the raw key is shown **once**):

```bash
TOKEN="your-40-char-token-key"
BASE="http://localhost:8000/api"

# Discover every available endpoint and its fields
curl -H "Authorization: Token $TOKEN" "$BASE/models/schema/"

# Plain listing — paginated, served by the database
curl -H "Authorization: Token $TOKEN" "$BASE/models/demo/Product/?page=2"

# Field filters (auto-generated from field types, visible in Swagger)
curl -H "Authorization: Token $TOKEN" "$BASE/models/demo/Product/?available=true&price__gte=100"

# Full CRUD
curl -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     -d '{"name": "Laptop Pro", "price": "1499.00", "available": true}' \
     "$BASE/models/demo/Product/"
curl -X PATCH -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     -d '{"available": false}' "$BASE/models/demo/Product/42/"
curl -X DELETE -H "Authorization: Token $TOKEN" "$BASE/models/demo/Product/42/"
```

### Full-text search — automatically routed to Elasticsearch

`?search=` runs against the model's `searchable=True` fields. For a **`DUAL`-storage
model** (data mirrored in ES) the very same request is executed on Elasticsearch —
fuzzy, typo-tolerant, relevance-ranked — with **no change to the URL or your client
code**. Filters and pagination still apply on top of the ES-ranked result:

```bash
# Product is DUAL → this search runs on Elasticsearch (typo still matches)
curl -i -H "Authorization: Token $TOKEN" "$BASE/models/demo/Product/?search=laptp"
# HTTP/1.1 200 OK
# X-Snap-Query-Backend: elasticsearch     ← the search ran on ES
# {"count": 3, "results": [{"id": 42, "name": "Laptop Pro", …}, …]}

# Combine ES search with DB filters and pagination — still one request
curl -H "Authorization: Token $TOKEN" \
     "$BASE/models/demo/Product/?search=laptop&available=true&page=1"

# The same URL on a DB_ONLY model transparently uses SQL icontains instead
curl -i -H "Authorization: Token $TOKEN" "$BASE/models/demo/Customer/?search=7"
# X-Snap-Query-Backend: database          ← no ES mirror, DB handled it
```

Routing decision per request, in order:

| Model mode | `?search=` present | ES routing on | Executed on |
|------------|-------------------|---------------|-------------|
| `ES_ONLY` | any | — | Elasticsearch (only source) |
| `DUAL` | yes | yes | **Elasticsearch** (fuzzy `multi_match`, relevance order) |
| `DUAL` | yes | no | Database (`icontains` over searchable fields) |
| `DUAL` | no | — | Database (native pagination, no ES round-trip) |
| `DB_ONLY` | yes | — | Database (`icontains` over searchable fields) |

Every list response carries the **`X-Snap-Query-Backend: elasticsearch | database`**
header, so you can always verify where a query ran — including the case where ES
failed mid-request and the DB fallback silently answered (the header then says
`database`). Hide the header in production with `SNAPADMIN_QUERY_BACKEND_HEADER = False`.

### Counting objects — with and without filters

Every list response is paginated, and the envelope already carries the **total count**
for the current filter set in `count` — you do not need to fetch all pages to know how
many rows match. Ask for the smallest possible page to read just the number:

```bash
# Total rows in a model (page size 1 → tiny payload, full count in "count")
curl -s -H "Authorization: Token $TOKEN" "$BASE/models/demo/Product/?page_size=1" \
  | python -c "import sys,json; print(json.load(sys.stdin)['count'])"

# Count with filters — the count reflects the filtered queryset
curl -s -H "Authorization: Token $TOKEN" \
     "$BASE/models/demo/Product/?available=true&price__gte=100&page_size=1"
# {"count": 128, "next": "...", "results": [ ... 1 row ... ]}

# Count of a full-text match set (DUAL model → ES answers, count = ES hits)
curl -s -H "Authorization: Token $TOKEN" "$BASE/models/demo/Product/?search=laptop&page_size=1"
```

Notes per storage mode:
- **`DB_ONLY` / `DUAL` without search** — `count` is a `SELECT COUNT(*)` over the
  filtered SQL queryset (exact, uses your indexes).
- **`DUAL` with `?search=` / `ES_ONLY`** — `count` is the number of ES hits, capped by
  `SNAPADMIN_ES_SEARCH_LIMIT` (default 1000). Raise the limit if you need exact counts on
  very large result sets, or drop `?search=` and count on the DB.

### Exporting / bulk-reading data optimally

Pull large result sets **page by page** rather than with a huge `page_size` — each page
is a bounded query and a bounded response. A simple, memory-flat export loop:

```bash
# Walk every page by following the "next" link the API returns
URL="$BASE/models/demo/Product/?page_size=200"
while [ "$URL" != "null" ]; do
  PAGE=$(curl -s -H "Authorization: Token $TOKEN" "$URL")
  echo "$PAGE" | python -c "import sys,json;[print(r['id']) for r in json.load(sys.stdin)['results']]"
  URL=$(echo "$PAGE" | python -c "import sys,json; print(json.load(sys.stdin)['next'] or 'null')")
done
```

Choosing the fastest path for an export:
- **`DB_ONLY` / `DUAL` (no search)** — plain paginated listing is optimal: native SQL
  `LIMIT/OFFSET`, no ES round-trip, no row cap. Add `?ordering=id` for a stable page
  walk. FK columns are auto-`select_related` (no N+1) — see *Large-Dataset Performance*.
- **Full-text export (`DUAL`)** — only route through ES (`?search=`) when you actually
  need relevance/fuzzy matching; it is capped by `SNAPADMIN_ES_SEARCH_LIMIT`. For a
  *complete* dump of matching rows, filter on the DB instead of searching.
- **`ES_ONLY`** — listings come from ES and are bounded by `SNAPADMIN_ES_SEARCH_LIMIT`;
  raise it for larger exports, or narrow with `?search=`/filters.
- **Server-side / offline jobs** — skip the HTTP API entirely and stream the ORM:
  `Model.objects.all().iterator(chunk_size=2000)` keeps memory flat over millions of
  rows (this is exactly what `es_reindex_all()` and the ETL helper do internally).

### Same queries from the ORM (server-side)

The routing above is REST-layer sugar; in Python you address each mode directly:

```python
# DB_ONLY / DUAL — normal ORM; count is exact and index-backed
Product.objects.filter(available=True, price__gte=100).count()

# DUAL / ES_ONLY — fuzzy full-text via Elasticsearch (returns an ES-ranked result)
Product.snap_search("laptp", limit=50)          # DUAL: DB queryset in ES-relevance order
SearchLog.snap_search("checkout")               # ES_ONLY: served straight from the index

# Memory-flat export of any DB-backed model
for row in Product.objects.all().iterator(chunk_size=2000):
    ...
```

### GraphQL field naming

The schema is generated from your `SnapModel`s — you never write it by hand, so the
field names follow a fixed scheme. For a model `Product` in an app whose label is
`demo`, two root query fields are generated (graphene lower-camel-cases the Python
names, since `auto_camelcase` is on):

| Query field | Python name | Arguments | Returns |
|-------------|-------------|-----------|---------|
| `demoProduct` | `demo_product` | `id: ID!` (required) | one object by primary key |
| `allDemoProducts` | `all_demo_products` | `search: String`, `first: Int`, `offset: Int` | a plain list of objects |

The general form is **`<applabel><Model>`** for the single-object field and
**`all<Applabel><Model>s`** for the list field (from the raw
`{app_label}_{model_lower}` / `all_{app_label}_{model_lower}s`). Pluralisation is
naïve — a trailing `s` is appended, so `Category` becomes `allDemoCategorys`, not
`allDemoCategories`. The object type is named `<Applabel><Model>Type`
(e.g. `DemoProductType`).

List arguments:

- **`search`** — full-text query; routed to Elasticsearch for `DUAL`/`ES_ONLY`
  models (the same smart routing as the REST `?search=`), otherwise a DB filter.
- **`first`** — max number of rows to return (also caps the ES search size).
- **`offset`** — number of rows to skip (combine with `first` to paginate).

The result is a **plain list**, not a Relay connection, so select fields directly —
there is no `edges`/`node` wrapper:

```graphql
query {
  allDemoProducts(search: "laptop", first: 10, offset: 20) {
    id
    name
    price
    available
  }
  demoProduct(id: 42) { id name price }
}
```

Fields listed in a model's `api_exclude_fields` are omitted from its GraphQL type
too, exactly as for REST. Add or remove `SnapModel`s to change what the schema
exposes — don't edit the generated schema.

### GraphQL — same tokens, same permissions

GraphQL enforces the **same access contract as REST**: every resolver requires an
authenticated caller (admin session or `Authorization: Token`) holding the model's
`view` permission; a token's `allowed_models` scope applies on top. List fields
accept `search` (ES-routed for `DUAL`/`ES_ONLY` models), `first` and `offset`:

```bash
curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     -d '{"query": "{ allDemoProducts(search: \"laptop\", first: 10) { id name price } }"}' \
     "http://localhost:8000/api/graphql/"

# Anonymous callers get {"errors": [{"message": "Authentication required."}]}
```

GraphiQL (the interactive playground) follows `DEBUG` by default — override with
`SNAPADMIN_GRAPHIQL_ENABLED`. Auth enforcement can be disabled for private
deployments with `SNAPADMIN_GRAPHQL_REQUIRE_AUTH = False` (not recommended).

## 🔑 API Token Security

API tokens authenticate REST/GraphQL requests via the `Authorization: Token <key>` header.

```python
from snapadmin.models import APIToken

token = APIToken.create_for_user(
    user=user,
    token_name="CI Pipeline",
    allowed_models=["myapp.Product", "myapp.Order"],  # optional scope
    expires_in_days=30,
)
print(token.token_key)  # raw key — available ONLY here, right after creation
```

- **Hashed at rest** — only a SHA-256 `token_digest` and the non-secret 8-char `token_prefix` are
  stored. The raw `token_key` is never persisted; it is returned exactly once (the
  `POST /api/tokens/` response, or a one-time admin message). Afterwards `token_key` is `None` and
  only `token_prefix` identifies the token. Authentication looks the presented key up by its digest.
- **`allowed_models` — empty ≠ unrestricted.** An empty list means "any model the owning user
  already has Django permissions for"; the token scope is always AND-ed with `user.has_perm`. A
  non-empty list *narrows* access to exactly those `"app_label.ModelName"` entries.

## GDPR Data Retention

Add automatic record cleanup to any model with two class attributes:

```python
class AuditLog(snap_models.SnapModel):
    action = snap.SnapCharField(max_length=100)
    created_at = snap.SnapDateTimeField(auto_now_add=True)

    # Auto-delete records older than 90 days
    data_retention_days = 90
    data_retention_field = "created_at"  # default; can point to any DateTimeField
```

Records are removed by the `purge_expired_data` Celery task (schedule it with Celery Beat) or manually:

```bash
python manage.py purge_expired_data         # live run
python manage.py purge_expired_data --dry-run  # preview only
```

Or programmatically, per model — returns the number of records purged:

```python
AuditLog.purge_expired()              # delete expired rows now
AuditLog.purge_expired(dry_run=True)  # count only, delete nothing
```

The purge is **storage-aware**, so personal data never lingers in a secondary store:

| Mode | What gets purged |
| --- | --- |
| `DB_ONLY` | Bulk delete from the database. |
| `DUAL` | Database rows **and** the mirrored Elasticsearch documents. |
| `ES_ONLY` | Range `delete_by_query` against the index on `data_retention_field`. |

> A plain `QuerySet.delete()` never calls each model's `delete()`, so a naïve bulk
> purge would leave the Elasticsearch copy behind. `purge_expired()` closes that gap
> for `DUAL` and `ES_ONLY` models (ES operations require `ELASTICSEARCH_ENABLED=True`).

---

## 🚨 Error Monitoring & Email Alerts

Optional, zero-dependency error notifications. One middleware records every unhandled
exception and 5xx response as an `ErrorEvent` (browsable in the admin under
**Error Events**), and two email channels keep you informed:

- **Spike alert** — when `SNAPADMIN_ERROR_ALERT_THRESHOLD` errors occur within
  `SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES` (default: 20 errors / 15 min), one email goes
  out immediately. A cooldown guarantees at most one alert per window — no inbox floods.
- **Daily digest** — a grouped 24-hour report (identical errors are merged by
  exception class + endpoint, most frequent first). The digest is capped at
  `SNAPADMIN_ERROR_DIGEST_MAX_GROUPS` groups so it stays readable even on a bad day.

> **Prerequisite:** working Django email settings (`EMAIL_BACKEND`, `EMAIL_HOST`, … —
> i.e. a configured SMTP server) and `DEFAULT_FROM_EMAIL`. No emails are sent while the
> recipient lists are empty, so the feature is safely inert until you opt in.

**1. Enable the middleware:**

```python
MIDDLEWARE = [
    # ... Django middleware ...
    "snapadmin.middleware.SnapErrorMonitorMiddleware",
]
```

**2. Configure (all values shown are the defaults):**

```python
SNAPADMIN_ERROR_MONITOR_ENABLED = True        # master kill-switch (no MIDDLEWARE edit needed)

# Spike alert
SNAPADMIN_ERROR_ALERT_ENABLED = True
SNAPADMIN_ERROR_ALERT_THRESHOLD = 20          # errors ...
SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES = 15     # ... within this window → email
SNAPADMIN_ERROR_ALERT_EMAILS = ["ops@example.com"]

# Daily digest
SNAPADMIN_ERROR_DIGEST_ENABLED = True
SNAPADMIN_ERROR_DIGEST_EMAILS = ["team@example.com"]  # falls back to ALERT_EMAILS
SNAPADMIN_ERROR_DIGEST_MAX_GROUPS = 20        # cap on distinct error groups per email

# Housekeeping: ErrorEvents older than this are purged by the digest task
SNAPADMIN_ERROR_RETENTION_DAYS = 30
```

**3. Schedule the digest** — pick the send time via Celery Beat:

```python
CELERY_BEAT_SCHEDULE = {
    "send-error-digest": {
        "task": "snapadmin.send_error_digest",
        "schedule": crontab(hour=8, minute=0),   # your choice of send time
    },
}
```

…or without Celery, from cron:

```bash
0 8 * * *  python manage.py send_error_digest        # last 24h, grouped
python manage.py send_error_digest --hours 12        # custom window
```

Monitoring is **fail-safe by design**: storage or SMTP failures are logged
(`error_monitor_record_failed`, `error_monitor_alert_failed`) and swallowed — a broken
mail server never breaks your pages. Try it in the demo: hit `http://localhost:8000/demo/error/`
a few times (DEBUG only) and watch **Error Events** fill up; alert emails land in the
console with the default DEBUG email backend.

---

## 💾 3-2-1 Database Backups

Built-in scheduled backups following the classic **3-2-1 rule** — keep **3** copies of
your data, on **2** different machines, **1** of them offsite:

| Copy | Destination | Where it lives | Default frequency |
|------|-------------|----------------|-------------------|
| 1 | `local` | A directory on the **same server** (`SNAPADMIN_BACKUP_LOCAL_DIR`) | every 24 h |
| 2 | `network` | A directory on **another server on your network**, reachable as a mounted NFS/SMB share (`SNAPADMIN_BACKUP_NETWORK_DIR`) | every 24 h |
| 3 | `remote` | An **offsite server anywhere in the world** via FTP/FTPS (`SNAPADMIN_BACKUP_FTP_*`) | every 168 h (weekly) |
| 3 (alt) | `sftp` | The **same offsite copy over SSH/SFTP** (`SNAPADMIN_BACKUP_SFTP_*`), password or key auth — encrypted transport. Use instead of, or alongside, plain FTP. Needs `pip install django-snapadmin[backup]` | every 168 h (weekly) |

Dumps are gzip-compressed — a file copy for SQLite, `pg_dump` for PostgreSQL — and each
destination keeps the newest `SNAPADMIN_BACKUP_KEEP` dumps (oldest pruned automatically,
including on the FTP/SFTP server).

**Configuration** (each destination has its *own* schedule — tune how often the local,
network and remote copies are refreshed independently):

```python
SNAPADMIN_BACKUP_ENABLED = True               # strictly opt-in (default: False)
SNAPADMIN_BACKUP_KEEP = 7                     # dumps kept per destination

# Copy 1 — same server
SNAPADMIN_BACKUP_LOCAL_DIR = "/var/backups/snapadmin"
SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS = 24       # daily

# Copy 2 — another server on the same network (mounted share); empty = off
SNAPADMIN_BACKUP_NETWORK_DIR = "/mnt/backup-server/snapadmin"
SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS = 24     # daily

# Copy 3 — offsite FTP/FTPS; empty host = off
SNAPADMIN_BACKUP_FTP_HOST = "backup.example.com"
SNAPADMIN_BACKUP_FTP_PORT = 21
SNAPADMIN_BACKUP_FTP_USER = "backup"
SNAPADMIN_BACKUP_FTP_PASSWORD = "secret"
SNAPADMIN_BACKUP_FTP_DIR = "/snapadmin"
SNAPADMIN_BACKUP_FTP_TLS = True               # FTPS (recommended)
SNAPADMIN_BACKUP_REMOTE_EVERY_HOURS = 168     # weekly

# Copy 3 (alternative) — offsite over SSH/SFTP; empty host = off.
# Requires the optional paramiko dependency: pip install django-snapadmin[backup]
SNAPADMIN_BACKUP_SFTP_HOST = "offsite.example.com"
SNAPADMIN_BACKUP_SFTP_PORT = 22
SNAPADMIN_BACKUP_SFTP_USER = "backup"
SNAPADMIN_BACKUP_SFTP_KEY_FILE = "/etc/snapadmin/id_ed25519"  # key auth; or set _PASSWORD
SNAPADMIN_BACKUP_SFTP_PASSWORD = ""           # used only when no key file is set
SNAPADMIN_BACKUP_SFTP_DIR = "/snapadmin"
SNAPADMIN_BACKUP_SFTP_EVERY_HOURS = 168       # weekly
```

> **SFTP vs FTP** — prefer `sftp` for offsite copies: the transport is encrypted end-to-end and
> can authenticate with an SSH key. The backup host's key is **verified against
> `~/.ssh/known_hosts`** — an unknown host is rejected, not trusted on first connect (this
> prevents a man-in-the-middle from hijacking the offsite copy). Before enabling `sftp`, add
> the host's key to `known_hosts` for the user that runs the backups, e.g.
> `ssh-keyscan -H offsite.example.com >> ~/.ssh/known_hosts` during deployment, or connect once
> with `ssh` and accept the key. Enable `sftp` and `remote` together for two independent offsite
> copies.

**Running** — the scheduler is a separate process from your web workers. Add the
`snapadmin.run_db_backups` task to Celery Beat (an hourly check; each destination only
fires when its own interval has elapsed — last-run times persist across restarts):

```python
CELERY_BEAT_SCHEDULE = {
    "run-db-backups": {
        "task": "snapadmin.run_db_backups",
        "schedule": crontab(minute=30),   # hourly due-check
    },
}
```

…or without Celery, from cron:

```bash
30 * * * *  python manage.py db_backup            # ships only what is due
python manage.py db_backup --force                # all configured destinations, now
python manage.py db_backup --destination remote   # one destination, now (local/network/remote/sftp)
```

A failed destination (unreachable share, FTP/SFTP down) is reported and logged but never
cancels the other copies — and it stays "due", so it is retried on the next pass.

---

## 📴 Offline Mode

Make a model's admin list view survive a dropped connection with a single toggle:

```python
class Customer(snap_models.SnapModel):
    first_name = snap.SnapCharField(max_length=100, show_in_form=True)
    last_name = snap.SnapCharField(max_length=100, show_in_form=True)

    # Cache this model's list view client-side and enable offline support
    offline_mode = True
    # Prefetch only the 50 most-recent rows for offline view (default: 100)
    offline_cache_limit = 50
```

When `offline_mode = True`, SnapAdmin injects `snapadmin/js/offline.js` into that
model's admin pages only. It then:

- **Prefetches the most-recent `offline_cache_limit` rows** (default **100**) from
  `GET /api/offline-data/<app>/<model>/` and stores them in the browser's **IndexedDB**
  on every visit (the rendered list is kept as a fallback snapshot).
- **Repaints the list from cache** when the backend becomes unreachable, and shows a
  **saved-objects panel** — how many objects are cached (out of the limit), when they
  were cached, and how many changes are queued for sync.
- **Queues mutations** made while offline and **replays them on reconnect**, then
  refreshes the cache and shows a "synced *N* changes" toast.

### Real backend health checks, not just `navigator.onLine`

Connectivity is decided by whether the **Django backend actually answers**, not by the
OS network flag — a laptop can hold a Wi-Fi link while the server is down or the VPN
dropped. A lightweight `connectivity.js` loads on **all** SnapModel admin pages and:

- **Polls `GET /api/health/`** (every 15s by default — set
  `window.SNAPADMIN_HEALTH_INTERVAL` to override) with a short timeout, and re-checks
  immediately on browser `online`/`offline` events and tab refocus. The backend is
  "up" only when it responds.
- **Publishes one shared state** as a `snapadmin:connectivity` DOM event, so the
  connectivity layer and the per-model engine always agree.
- **Dynamic toasts, not static banners** — backend-lost / restored, "objects can't be
  shown right now" (non-cached pages), and "synced *N* changes" surface as
  auto-dismissing toasts.

On models that are **not** offline-capable, losing the backend shows a warning toast,
**blocks form submission**, and disables the Save buttons until it returns (preventing
silent data loss) while leaving the already-rendered page intact. **Sidebar badges**
mark every model link so you can see which models sync offline: a green **sync icon**
(spins while the backend is down) for offline-capable models, a muted **no-offline
icon** for the rest.

The badge list and per-model cache limits are served by `GET /api/offline-models/`
(authenticated), with a `localStorage` fallback so badges still render while offline.

No settings, migrations, or extra dependencies are required — it is pure client-side
behavior gated per model. Models without the flag still get the connectivity warnings
but ship no caching JS.

---

## ⚡ Large-Dataset Performance

SnapAdmin is built to stay responsive as tables grow. Most of the tuning is automatic,
and the rest is a handful of per-model knobs.

### Automatic — no admin N+1

For every model, `register_admin()` inspects the columns shown in the list view and
**auto-derives `list_select_related`** from the `ForeignKey` columns among them. A list
view that renders a related column (or a `__str__` that walks a relation) therefore
issues **one joined query**, not one query per row. Only the FKs actually displayed are
joined — relations you don't show are never pulled.

The auto-generated REST API does the same on its querysets: `select_related()` for
ForeignKeys and `prefetch_related()` for many-to-many fields, with the field lists cached
per model to keep introspection out of the hot path.

### Per-model knobs

Override these class attributes on any `SnapModel` to tune the admin list view:

```python
class AuditLog(snap_models.SnapModel):
    action = snap.SnapCharField(max_length=100, searchable=True)

    list_per_page = 50              # rows per page (default 100)
    list_max_show_all = 200         # cap on the "Show all" link
    show_full_result_count = False  # skip the unfiltered COUNT(*) on huge tables
```

| Attribute | Default | When to change it |
|-----------|---------|-------------------|
| `list_per_page` | `100` | Lower it for wide rows or heavy templates. |
| `list_max_show_all` | `200` | Guards against a "Show all" on a million-row table. |
| `show_full_result_count` | `True` | Set `False` on very large tables — the admin then skips the second, unfiltered `COUNT(*)` it runs to show the grand total, which is often the single most expensive query. |

### REST pagination

The REST API paginates by default (`PageNumberPagination`, `PAGE_SIZE = 25`), so large
collections are never serialized in one response. Tune it via the `REST_FRAMEWORK`
setting.

### Offloading search to Elasticsearch

For `DUAL` models the expensive part — full-text `?search=` — is routed to
Elasticsearch automatically, while plain listings keep the database's native
pagination (no row cap, no extra round-trip). `ES_ONLY` models are always served
from ES, since no DB table exists. See **REST API in Practice** above for the
routing matrix and the `X-Snap-Query-Backend` response header.

### Benchmarking at scale

Two demo management commands let you reproduce the numbers on your own hardware:

```bash
# Bulk-seed 100k customers + orders (batched bulk_create, flat memory)
python manage.py seed_large --count 100000

# Time the Order changelist queryset with vs without list_select_related
python manage.py benchmark_list_view --model order
```

`benchmark_list_view` iterates the changelist queryset and touches each row's
ForeignKey, so the unoptimized run pays the full N+1 cost while the optimized run
issues a single joined query. Representative output on a seeded table:

```
📊  Result
   WITHOUT :    5,001 queries       584.5 ms
   WITH    :        1 queries        37.8 ms

   Query reduction : 5,001 → 1  (5001× fewer)
   Speedup         : 15.5× faster wall time
```

The query count for the unoptimized path scales linearly with row count (`N + 1`),
while the optimized path stays flat at **1** — exactly the N+1 elimination
`list_select_related` is there to provide.

### Going deeper

The section above covers SnapAdmin's knobs. For the broader data-access patterns
behind them — `select_related` vs `prefetch_related`, `only()`/`values()`, keyset
vs offset pagination, indexing, the N+1 problem, the SQL/NoSQL trade-off, and
denormalization — see the **[Optimizations Guide](https://drofji.github.io/django-snapadmin/#optimizations)**
in the full documentation.

---

## 🔧 Environment Variables Reference

Copy `dist.env` to `.env` and configure:

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | insecure placeholder | Django secret key - **must be changed in production** |
| `DEBUG` | `True` | Enable Django debug mode - set `False` in production |
| `ALLOWED_HOSTS` | `localhost,...` | Comma-separated allowed hostnames |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `JSON_LOGS` | `False` | Structured JSON log output for production log aggregation |
| `POSTGRES_DB` | `snapadmin` | PostgreSQL database name |
| `POSTGRES_USER` | `snapadmin` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `snapadmin` | PostgreSQL password |
| `POSTGRES_HOST` | `db` | PostgreSQL host (Docker service name or IP) |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `REDIS_URL` | `redis://redis:6379/0` | Redis URL for Celery broker and result backend |
| `ELASTICSEARCH_URL` | `http://elasticsearch:9200` | Elasticsearch cluster URL |
| `ELASTICSEARCH_ENABLED` | `False` | Enable ES integration; when `False` all models use `DB_ONLY` |
| `SNAPADMIN_REST_API_ENABLED` | `True` | Serve the REST CRUD endpoints (`False` removes the routes) |
| `SNAPADMIN_SWAGGER_ENABLED` | `True` | Serve Swagger UI + ReDoc |
| `SNAPADMIN_GRAPHQL_ENABLED` | `True` | Serve the GraphQL endpoint |
| `SNAPADMIN_ES_QUERY_ROUTING` | `True` | Route `?search=` on `DUAL` models to Elasticsearch |
| `SNAPADMIN_ES_SEARCH_LIMIT` | `1000` | Max hits fetched from ES per routed search |
| `SNAPADMIN_QUERY_BACKEND_HEADER` | `True` | Expose the `X-Snap-Query-Backend` header on list responses |
| `SNAPADMIN_GRAPHQL_REQUIRE_AUTH` | `True` | Require auth + per-model perms on every GraphQL resolver |
| `SNAPADMIN_GRAPHIQL_ENABLED` | `DEBUG` | GraphiQL playground — keep out of production |
| `SNAPADMIN_URL_PREFIX` | `""` | Extra path segment prepended to every snapadmin route (relocate the whole API/GraphQL/Swagger surface) |
| `SNAPADMIN_DASHBOARD_PUBLIC` | `False` | Serve the system dashboard without the default staff gate |
| `SNAPADMIN_USER_API_ENABLED` | `False` | Serve the admin-only user-management API (`/api/users/`, `/api/permissions/`) |
| `SNAPADMIN_API_AUTHENTICATION_CLASSES` | token auth | API authenticator dotted paths (add session / JWT) |
| `SNAPADMIN_ANALYTICS_DB_ALIAS` | — | `DATABASES` alias for read-only list/retrieve routing; empty = no routing |
| `SNAPADMIN_API_DELETE_GUARD` | — | Dotted path to a `Callable[[request, obj], bool]` vetoing API deletes (403); AND-ed with each model's `api_can_delete` hook |
| `SNAPADMIN_REINDEX_API_ENABLED` | `False` | Serve the admin-only bulk ES reindex endpoint (`POST /api/es/reindex/`) |
| `SNAPADMIN_REINDEX_API_ASYNC` | `False` | Offload the reindex endpoint to the `snapadmin.run_es_reindex` Celery task |
| `SNAPADMIN_AUDIT_LOG_ENABLED` | `True` | Record admin create/update/delete as an immutable audit trail |
| `SNAPADMIN_AUDIT_RETENTION_DAYS` | `365` | Retention window for `snapadmin_audit_export --purge` |
| `SNAPADMIN_ESTIMATED_COUNT` | `True` | Use PostgreSQL's fast row estimate for huge, unfiltered changelists |
| `SNAPADMIN_ESTIMATED_COUNT_THRESHOLD` | `100000` | Only estimate the count above this many rows |
| `SNAPADMIN_EXPORT_ENABLED` | `True` | Enable the async background export API (`/api/exports/`) |
| `SNAPADMIN_EXPORT_CHUNK_SIZE` | `1000` | Rows per export chunk (progress + resume granularity) |
| `SNAPADMIN_EXPORT_DIR` | `BASE_DIR/exports` | Directory export files are written to |
| `ELASTICSEARCH_KWARGS` | `{request_timeout: 5}` | Extra kwargs merged into the `Elasticsearch(...)` client |
| `SNAPADMIN_ES_CLIENT_FACTORY` | — | Dotted path to a zero-arg callable returning a custom ES client |
| `SNAPADMIN_THROTTLE_ANON` | `60/min` | DRF rate limit for anonymous callers |
| `SNAPADMIN_THROTTLE_USER` | `600/min` | DRF rate limit for authenticated clients |
| `SNAPADMIN_SEED_ADMIN_PASSWORD` | — | Password for the seeded superuser; `admin/admin` default allowed only with `DEBUG=True` |
| `EMAIL_HOST` / `EMAIL_PORT` | `localhost` / `587` | SMTP server for notification emails |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | — | SMTP credentials |
| `EMAIL_USE_TLS` | `True` | Use STARTTLS for SMTP |
| `DEFAULT_FROM_EMAIL` | `snapadmin@localhost` | From address of alert/digest emails |
| `SNAPADMIN_ERROR_MONITOR_ENABLED` | `True` | Record unhandled exceptions / 5xx as `ErrorEvent`s |
| `SNAPADMIN_ERROR_ALERT_ENABLED` | `True` | Enable the error spike alert email |
| `SNAPADMIN_ERROR_ALERT_THRESHOLD` | `20` | Errors within the window that trigger the alert |
| `SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES` | `15` | Rolling window for the spike alert |
| `SNAPADMIN_ERROR_ALERT_EMAILS` | — | Comma-separated alert recipients (empty = no alerts) |
| `SNAPADMIN_ERROR_DIGEST_ENABLED` | `True` | Enable the daily grouped error digest |
| `SNAPADMIN_ERROR_DIGEST_EMAILS` | — | Digest recipients; falls back to alert emails |
| `SNAPADMIN_ERROR_DIGEST_MAX_GROUPS` | `20` | Max distinct error groups per digest email |
| `SNAPADMIN_ERROR_DIGEST_HOUR` / `_MINUTE` | `8` / `0` | Daily send time of the digest (Celery Beat) |
| `SNAPADMIN_ERROR_RETENTION_DAYS` | `30` | Purge `ErrorEvent`s older than this |
| `SNAPADMIN_BACKUP_ENABLED` | `False` | Enable scheduled 3-2-1 database backups |
| `SNAPADMIN_BACKUP_KEEP` | `7` | Dumps kept per destination (oldest pruned) |
| `SNAPADMIN_BACKUP_LOCAL_DIR` | `./backups` | Copy 1: directory on the same server |
| `SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS` | `24` | How often the local copy refreshes |
| `SNAPADMIN_BACKUP_NETWORK_DIR` | — | Copy 2: mounted share of a server on your network (empty = off) |
| `SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS` | `24` | How often the network copy refreshes |
| `SNAPADMIN_BACKUP_FTP_HOST` / `_PORT` | — / `21` | Copy 3: offsite FTP/FTPS server (empty host = off) |
| `SNAPADMIN_BACKUP_FTP_USER` / `_PASSWORD` | — | FTP credentials |
| `SNAPADMIN_BACKUP_FTP_DIR` | `/` | Target directory on the FTP server |
| `SNAPADMIN_BACKUP_FTP_TLS` | `False` | Use FTPS (recommended for offsite) |
| `SNAPADMIN_BACKUP_REMOTE_EVERY_HOURS` | `168` | How often the offsite copy refreshes (weekly) |
| `SNAPADMIN_AUTO_SEED` | `False` | Auto-run `seed_demo` on startup (demo only) |
| `TRAEFIK_DOMAIN` | `yourdomain.com` | Production domain for `docker-compose.traefik.prod.yml` |
| `TRAEFIK_ACME_EMAIL` | — | Email for Let's Encrypt certificate registration |
| `TRAEFIK_DASHBOARD_USER` | `admin` | Reference username (see `TRAEFIK_DASHBOARD_CREDENTIALS`) |
| `TRAEFIK_DASHBOARD_PASSWORD` | `changeme` | Reference password (see `TRAEFIK_DASHBOARD_CREDENTIALS`) |
| `TRAEFIK_DASHBOARD_CREDENTIALS` | `admin:$$apr1$$...` | Dashboard BasicAuth in htpasswd format |

---

## 🌟 Demo Application Features

The repository includes a `demo/` app and a `sandbox/` project to showcase SnapAdmin's power:

- **Complete Project Setup**: Ready-to-use Docker environment with PostgreSQL, Redis, and Elasticsearch.
- **Example Domain Models**: Product, Customer, and Order models showing complex relationships.
- **Interactive Dashboard**: A custom system dashboard with health checks and environment stats.
- **Seeder Command**: `python manage.py seed_demo` to instantly populate your environment.
- **Celery Integration**: Example background tasks for data indexing and stats generation.

---

## 🐳 Running the Demo (Docker)

```bash
git clone https://github.com/drofji/django-snapadmin.git
cd django-snapadmin
cp dist.env .env
docker compose up --build
```
- **Admin**: http://localhost:8000/admin/ (admin / admin)
- **REST API Docs**: http://localhost:8000/api/docs/
- **GraphQL API**: http://localhost:8000/api/graphql/

**With Elasticsearch** (optional, adds ~512 MB RAM):
```bash
# 1. Enable in .env
echo "ELASTICSEARCH_ENABLED=True" >> .env

# 2. Start with ES profile
docker compose --profile es up --build

# 3. Also add Kibana for visualisation
docker compose --profile es --profile dev up --build
```

### Building images with automatic retention

For the test/demo image, `scripts/docker_build.sh` builds, tags by build-day, and
self-prunes so old images never pile up:

```bash
scripts/docker_build.sh                          # image=snapadmin-test, keep 3 build-days
IMAGE=myimg scripts/docker_build.sh              # custom image name
SNAPADMIN_IMAGE_KEEP_DAYS=5 scripts/docker_build.sh   # widen the window
```

**Retention policy** — *one build per day, keep the last N build-days* (N defaults to 3,
override via `SNAPADMIN_IMAGE_KEEP_DAYS`):

- **Collapse within a day** — images are tagged `snapadmin-test:YYYY-MM-DD` plus a moving
  `:latest`. Rebuilding the same calendar day re-points that day's tag at the new image;
  the superseded build becomes a dangling layer and is reclaimed.
- **Rolling N-day window** — the last build of each of the N most-recent *build-days* is
  kept; when an (N+1)-th distinct build-day appears, the oldest day's image is pruned.
- **History gaps are irrelevant** — "N days" means the last N build-days, not calendar
  days. Idle days never consume a slot.

> *Example:* builds a month ago, a week ago, yesterday, and today leave exactly **three**
> images after today's build — one each for *a week ago*, *yesterday*, and *today*; the
> month-ago image and all superseded same-day builds are gone.

The pruner can also run standalone (e.g. in CI), with a dry-run mode:

```bash
python -m scripts.docker_retention prune --image snapadmin-test --dry-run
```

---

## 🌐 Running with Traefik

Two Traefik overlay files are provided for routing requests through a reverse proxy.

### Local development (HTTP)

Access the app at `http://snapadmin.localhost/` without a port number:

```bash
docker compose -f docker-compose.yml -f docker-compose.traefik.local.yml up --build
```

| URL | Service |
|-----|---------|
| `http://snapadmin.localhost/admin/` | Django admin |
| `http://traefik.localhost/` | Traefik dashboard (BasicAuth) |

On Windows, add to `C:\Windows\System32\drivers\etc\hosts`:
```
127.0.0.1 snapadmin.localhost traefik.localhost
```

### Production (HTTPS + Let's Encrypt)

For production with automatic TLS certificates, set these values in `.env`:

```env
TRAEFIK_DOMAIN=admin.mycompany.com
TRAEFIK_ACME_EMAIL=your@email.com
TRAEFIK_DASHBOARD_CREDENTIALS=admin:$$apr1$$...   # see dist.env for generation instructions
ALLOWED_HOSTS=admin.mycompany.com
DEBUG=False
```

Then start the production overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.traefik.prod.yml up -d
```

| URL | Service |
|-----|---------|
| `https://admin.mycompany.com/admin/` | Django admin (auto-TLS) |
| `https://traefik.admin.mycompany.com/` | Traefik dashboard (BasicAuth) |

All HTTP traffic is automatically redirected to HTTPS.

### Dashboard credentials

The default credentials in `dist.env` are `admin` / `changeme`. To change them:

```bash
# Generate htpasswd string and escape $ for Docker Compose
echo $(htpasswd -nb newuser newpassword) | sed -e 's/\$/\$\$/g'
# Paste result into TRAEFIK_DASHBOARD_CREDENTIALS in .env
```

---

## 💻 Local Development Setup

```bash
# Clone and setup environment
git clone https://github.com/drofji/django-snapadmin.git
cd django-snapadmin
python -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -r requirements.txt
pip install -e .

# Initialize DB and run
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

---

## 🔄 Migrating from `drofji-automatically-django-admin`

The legacy package **`drofji-automatically-django-admin`** (import root `drofji_autoadmin`,
last tag **v1.1.0**) is being retired. SnapAdmin is its direct successor — same declarative
admin, now with REST/GraphQL, the Unfold theme, Elasticsearch, GDPR retention and offline mode.
The underlying Django fields are unchanged, so this is a **rename + settings swap, not a data
migration** (the only new table is `snapadmin_apitoken`).

```bash
# 1. Swap the package
pip uninstall drofji-automatically-django-admin
pip install django-snapadmin            # or: pip install git+https://github.com/drofji/django-snapadmin.git

# 2. Rename the import root, base class and fields (repo-wide)
grep -rl drofji_autoadmin . | xargs sed -i '' 's/drofji_autoadmin/snapadmin/g'
grep -rl AutoAdmin       . | xargs sed -i '' 's/AutoAdmin/Snap/g'
#   drofji_autoadmin → snapadmin   |   AutoAdminModel → SnapModel   |   AutoAdminCharField → SnapCharField …
```

**What you must change by hand:**

| Concern | Old (`drofji_autoadmin`) | New (`snapadmin`) |
|---------|--------------------------|-------------------|
| Theme apps | `admin_interface`, `colorfield` | **Remove them**; add `unfold` (+ `unfold.contrib.*`) and `django_ckeditor_5` *before* `django.contrib.admin` |
| REST stack | — | add `rest_framework`, `drf_spectacular`, `django_filters`, `graphene_django`, `snapadmin` |
| `rangefilter` | present | keep it |
| Admin registration | automatic (inheritance) | **explicit** — add `SnapModel.register_all_admins()` to `admin.py` |
| Color fields | `colorfield.ColorField` | `SnapColorField` |
| APIs | none | optional: `path("", include("snapadmin.urls"))` for `/api/`, `/api/docs/`, `/graphql/` |

Then run `python manage.py migrate` (creates only `snapadmin_apitoken`) and
`collectstatic` if you serve static yourself.

> ⚠️ **Don't run both packages at once** — keeping `drofji_autoadmin` in `INSTALLED_APPS`
> alongside SnapAdmin makes both register the admin (`AlreadyRegistered`). Fully uninstall the
> old package and remove it from `INSTALLED_APPS` first.

The full step-by-step is in [docs/index.html](docs/index.html) under
*Migration Guide → drofji_autoadmin → SnapAdmin*.

---

## 🔒 Security

See [SECURITY.md](SECURITY.md) for how to report a vulnerability, the built-in protections, and a
production hardening checklist.

## 📜 License

MIT License — see [LICENSE](LICENSE). The base install uses only permissive (MIT/BSD/Apache-2.0)
dependencies and is safe for commercial/proprietary use; anything copyleft or commercial is an opt-in
extra. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full breakdown of what is used,
what is optional, and under which licence.


