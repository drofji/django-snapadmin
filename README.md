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
| **Token Auth** | Secure, expirable API tokens with granular model-level access control. |
| **Configurable** | Easily enable/disable REST API, GraphQL, Swagger docs, and search modes via settings. |
| **Elasticsearch Ready** | Multi-mode storage (`DB_ONLY`, `DUAL`, `ES_ONLY`) for blazing fast search. |
| **GDPR/DSGVO Data Retention** | Per-model `data_retention_days` parameter with automatic Celery cleanup task. |
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
Add required apps to `INSTALLED_APPS` in `settings.py`:
```python
INSTALLED_APPS = [
    "unfold",
    "snapadmin",
    "rest_framework",
    "drf_spectacular",
    "graphene_django",
    # ...
]
```

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

## GDPR / DSGVO Data Retention

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

## 📜 License

MIT License — see [LICENSE](LICENSE).


