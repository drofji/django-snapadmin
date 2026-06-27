# 🚀 SnapAdmin — Declarative Django Admin & API Package

**SnapAdmin** is a high-performance, declarative Django package that eliminates admin and API boilerplate. Define your model fields once — get a feature-rich, beautiful Django admin (powered by Unfold), a full REST API, and a dynamic GraphQL API automatically.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2+-green?logo=django)](https://djangoproject.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

📚 **[Full Documentation](https://drofji.github.io/django-snapadmin/)** — Configuration guide, API reference, examples

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

---

## 👀 What You'll See

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
  allProducts(first: 10) {
    edges { node { id name price available } }
  }
}
```

**System Dashboard** — `http://localhost:8000/admin/snapadmin/dashboard/`
```
┌─────────────────────────────────────────────────────────┐
│  System Dashboard                          v0.1.0a2     │
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
| **Configurable** | Easily enable/disable REST API, GraphQL, Swagger docs, and search modes via settings. |
| **Elasticsearch Ready** | Multi-mode storage (`DB_ONLY`, `DUAL`, `ES_ONLY`) for blazing fast search. |
| **GDPR Data Retention** | Per-model `data_retention_days` parameter with automatic Celery cleanup task. |
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
pip install drofji-snapadmin
```

### From GitHub (Latest/Development)
```bash
pip install git+https://github.com/drofji/django-snapadmin.git
```

---

## 🛠 Usage & Configuration

### 1. Configure Settings
Add the required apps to `INSTALLED_APPS` in `settings.py`. **Order matters** — `unfold`
and its contrib apps must come *before* `django.contrib.admin`, and `django_ckeditor_5`
must be present because `SnapModel` imports the CKEditor 5 widget at load time:
```python
INSTALLED_APPS = [
    # Theme — must precede django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",

    # WYSIWYG (required — imported by SnapModel)
    "django_ckeditor_5",

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
> Installing `drofji-snapadmin` pulls in `django-unfold`, `django-ckeditor-5`,
> `djangorestframework`, `drf-spectacular`, `django-filter` and `graphene-django`
> automatically — you only need to list them in `INSTALLED_APPS`.

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

---

## ⚙️ Advanced Settings

Control core features via Django settings:

```python
SNAPADMIN_REST_API_ENABLED = True   # Enable/Disable the REST API
SNAPADMIN_GRAPHQL_ENABLED = True    # Enable/Disable the GraphQL API
SNAPADMIN_SWAGGER_ENABLED = True    # Enable/Disable Swagger UI documentation
ELASTICSEARCH_ENABLED = False       # Toggle ES search engine support
```

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

For `DUAL` and `ES_ONLY` models the REST list endpoint serves results straight from
Elasticsearch (`es_search`) instead of the database, moving full-text search and
large-result pagination off the primary database. See **Elasticsearch Storage Modes**
above.

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
pip install drofji-snapadmin            # or: pip install git+https://github.com/drofji/django-snapadmin.git

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

## 📜 License

MIT License — see [LICENSE](LICENSE).


