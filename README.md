# 🚀 SnapAdmin — Declarative Django Admin & API Package

**SnapAdmin** is a high-performance, declarative Django package that eliminates admin and API boilerplate. Define your model fields once — get a feature-rich, beautiful Django admin (powered by Unfold), a full REST API, and a dynamic GraphQL API automatically.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2+-green?logo=django)](https://djangoproject.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 📦 SnapAdmin Package Features

The core `snapadmin` package provides everything you need to bootstrap your project's admin and API:

| Feature | Description |
|---------|-------------|
| **Declarative Admin** | Configure `list_display`, `search_fields`, `list_filter` directly in your models using `SnapField`. |
| **Beautiful UI** | Native integration with `django-unfold` for a modern, responsive admin experience. |
| **Status Badges** | Easily add color-coded HTML badges for choices and status fields. |
| **Range Filters** | Built-in date and numeric range filters for efficient data exploration. |
| **Change Logging** | Automatic tracking of field-level changes (`old → new`) with a dedicated history view. |
| **Automatic REST API** | Instantly generated CRUD endpoints for every `SnapModel` with zero extra code. |
| **Dynamic GraphQL API** | Automatically generated GraphQL schema with support for complex data fetching. |
| **Token Auth** | Secure, expirable API tokens with granular model-level access control. |
| **Configurable** | Easily enable/disable REST API, GraphQL, Swagger docs, and search modes via settings. |
| **Elasticsearch Ready** | Multi-mode storage (`DB_ONLY`, `DUAL`, `ES_ONLY`) for blazing fast search. |
| **Structured Logging** | Integrated `structlog` for readable local logs and JSON logs in production. |

---

## 🏗 Package Architecture

```
snapadmin/
├── api/             # REST & GraphQL API core: views, serializers, auth
├── management/      # Custom management commands
├── migrations/      # Core package migrations (e.g., APIToken)
├── static/          # UI assets
├── templates/       # Custom admin templates
├── fields.py        # SnapField definitions with admin introspection
├── models.py        # SnapModel base and core models
└── urls.py          # Auto-configurable API and documentation routes
```

---

## 🚀 Quickstart: Using the Package

### 1. Install
```bash
pip install drofji-snapadmin
```

### 2. Configure Settings
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

### 3. Define your Model
```python
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)
    price = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)
```

### 4. Register Admin
```python
# admin.py
from snapadmin.models import SnapModel
SnapModel.register_all_admins()
```

---

## 🛠 Advanced Package Configuration

You can control core features via Django settings:

```python
SNAPADMIN_REST_API_ENABLED = True  # Enable/Disable the REST API
SNAPADMIN_GRAPHQL_ENABLED = True   # Enable/Disable the GraphQL API
SNAPADMIN_SWAGGER_ENABLED = True   # Enable/Disable Swagger UI
ELASTICSEARCH_ENABLED = False      # Toggle ES search fallback
```

---

## 🌟 Demo Application Features

The repository includes a `demo/` app and a `sandbox/` project to showcase SnapAdmin's power:

- **Complete Project Setup**: Ready-to-use Docker environment with PostgreSQL, Redis, and Elasticsearch.
- **Example Domain Models**: Product, Customer, and Order models showing complex relationships.
- **Interactive Dashboard**: A custom system dashboard with health checks and environment stats.
- **Seeder Command**: `python manage.py seed_demo` to instantly populate your environment.
- **Celery Integration**: Example background tasks for data indexing and stats generation.
- **Full Test Suite**: Comprehensive `pytest` coverage for all package features.

---

## 🐳 Running the Demo (Docker)

```bash
cp dist.env .env
docker compose up --build
```
- **Admin**: http://localhost:8000/admin/ (admin / admin)
- **REST API Docs**: http://localhost:8000/api/docs/
- **GraphQL API**: http://localhost:8000/api/graphql/

---

## 📜 License

MIT License — see [LICENSE](LICENSE).
