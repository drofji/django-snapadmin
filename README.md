# 🚀 SnapAdmin — Declarative Django Admin & API Package

**SnapAdmin** is a high-performance, declarative Django package that eliminates admin and API boilerplate. Define your model fields once — get a feature-rich, beautiful Django admin (powered by Unfold), a full REST API, and a dynamic GraphQL API automatically.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2+-green?logo=django)](https://djangoproject.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

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

    # 2. Pick an Elasticsearch mode
    es_storage_mode = snap_models.EsStorageMode.DUAL  # DB + ES in sync
    data_retention_days = 365  # 3. DSGVO: auto-delete after 1 year
```

That's it. You instantly get:

| What | How |
|------|-----|
| Django Admin | Auto-registered, filtered, searchable — no `admin.py` needed |
| REST API | Full CRUD at `/api/product/` with Swagger docs |
| GraphQL | Dynamic schema at `/api/graphql/` |
| ES Search | Celery Beat syncs DB → Elasticsearch nightly |
| DSGVO Cleanup | `purge_expired_data` task auto-runs; or `manage.py purge_expired_data` |

### Elasticsearch Storage Modes

| Mode | Where data lives | When to use |
|------|-----------------|-------------|
| `DB_ONLY` (default) | PostgreSQL only | Any model where search speed isn't critical |
| `DUAL` | PostgreSQL + Elasticsearch | Full-text search on large product/article catalogs |
| `ES_ONLY` | Elasticsearch only | High-frequency write logs, analytics events |

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
| **DSGVO/GDPR Retention** | Per-model `data_retention_days` parameter with automatic Celery cleanup task. |
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

## DSGVO / GDPR Data Retention

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


