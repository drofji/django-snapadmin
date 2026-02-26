# 🚀 SnapAdmin — Production-Ready Django Auto-Admin

**SnapAdmin** is a declarative Django package that eliminates admin boilerplate. Define your model fields once — get a feature-rich, beautiful Django admin and a full REST API automatically.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2+-green?logo=django)](https://djangoproject.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **Declarative Admin** | `list_display`, `search_fields`, `list_filter` via field attributes |
| **Status Badges** | Coloured HTML badges for choice/status fields |
| **Range Filters** | Date & numeric range filters built-in |
| **Change Logging** | Detailed field-level `old → new` change history |
| **REST API** | Auto-generated CRUD API for every SnapModel |
| **Token Auth** | Named, expirable API tokens with model-scope restrictions |
| **OpenAPI Docs** | Swagger UI + ReDoc via drf-spectacular |
| **Celery** | Background tasks + Beat scheduler wired up |
| **Elasticsearch** | Optional full-text search with graceful DB fallback |
| **Structured Logs** | Colourised structlog output; JSON mode for production |
| **Docker** | One-command `docker compose up` with PostgreSQL, Redis, ES |

---

## 🏗 Architecture

```
snapadmin/          Core package — fields, models, admin registration, logging, token model, DRF views, serializers, auth
demo/               Example models (Product, Customer, Order) + seeder
sandbox/            Django project (settings, urls, celery)
tests/              pytest test suite
docker-compose.yml  Full production stack
Dockerfile          Multi-stage build (builder + runtime)
```

---

## 🐳 Quickstart — Docker (recommended)

```bash
# 1. Clone
git clone https://github.com/drofji/django-snapadmin.git
cd django-snapadmin

# 2. Configure environment
cp dist.env .env
# Edit .env if needed (defaults work out-of-the-box)

# 3. Start the full stack
docker compose up --build

# 4. Open
#   Admin:   http://localhost:8000/admin/     (admin / admin)
#   API:     http://localhost:8000/api/docs/  (Swagger UI)
```

The `app` service automatically runs migrations and seeds demo data on first boot.

To include Kibana for Elasticsearch visualisation:
```bash
docker compose --profile dev up --build
```

---

## 💻 Quickstart — Local Development

```bash
# 1. Create & activate venv
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment (SQLite, no Redis/ES required)
cp dist.env .env
# Set DEBUG=True, leave POSTGRES_DB blank for SQLite

# 4. Migrate & seed
python manage.py migrate
python manage.py seed_demo

# 5. Run
python manage.py runserver
```

**Elasticsearch is optional** — the app falls back to database queries automatically when ES is unavailable.

---

## 🛠 Usage

### Defining a SnapModel

```python
# yourapp/models.py
from snapadmin import fields as snap, models as snap_models
from django.utils.translation import gettext_lazy as _

class Product(snap_models.SnapModel):
    name = snap.SnapCharField(
        max_length=200,
        verbose_name=_("Name"),
        searchable=True,      # → search_fields
        show_in_list=True,    # → list_display
    )
    price = snap.SnapDecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Price"),
        filterable=True,      # → NumericRangeFilter
    )
    status = snap.SnapCharField(
        max_length=20,
        choices=[("active","Active"),("archived","Archived")],
        filterable=True,
    )
    status_badge = snap.SnapStatusBadgeField(
        field_name="status",
        choices=[
            snap.SnapStatusBadgeFieldChoice("active",  "#155724","#D4EDDA","#C3E6CB"),
            snap.SnapStatusBadgeFieldChoice("archived","#721C24","#F8D7DA","#F5C6CB"),
        ],
    )

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
```

### Register admin

```python
# yourapp/admin.py
from snapadmin.models import SnapModel
SnapModel.register_all_admins()
```

That's it. Navigate to `/admin/` and your model has a fully configured admin.

---

## 🔑 Field Flags Reference

| Flag | Default | Effect |
|------|---------|--------|
| `show_in_list` | `True` | Adds to `list_display` |
| `show_in_form` | `False` | Shows in the change form |
| `searchable` | `False` | Adds to `search_fields` |
| `filterable` | `False` | Adds to `list_filter` (smart type-aware) |
| `editable` | `False` | If False → always read-only in admin |
| `updatable` | `True` | If False → read-only after first save |
| `required` | `False` | If False → `null=True, blank=True` |
| `autocomplete` | `False` | Select2 widget for FKs & choices |

---

## 🌐 REST API

### Authentication

All API requests require a token in the Authorization header:

```http
Authorization: Token <your-token-key>
```

### Create a token (admin panel or seed_demo)

After seeding, the demo token key is printed to the console. Or create one programmatically:

```python
from snapadmin.models import APIToken
token = APIToken.create_for_user(
    user=user,
    token_name="My CI Token",
    allowed_models=["demo.Product"],   # restrict to specific models
    expires_in_days=30,                # None = never expires
)
print(token.token_key)
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tokens/` | List your tokens |
| POST | `/api/tokens/` | Create a new token |
| DELETE | `/api/tokens/{id}/` | Delete a token |
| GET | `/api/models/schema/` | List all available model endpoints |
| GET | `/api/models/{app}/{Model}/` | List objects |
| POST | `/api/models/{app}/{Model}/` | Create object |
| GET | `/api/models/{app}/{Model}/{pk}/` | Retrieve object |
| PATCH | `/api/models/{app}/{Model}/{pk}/` | Update object |
| DELETE | `/api/models/{app}/{Model}/{pk}/` | Delete object |
| GET | `/api/docs/` | Swagger UI |
| GET | `/api/schema/` | OpenAPI 3 JSON |

### Example: List products

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
     http://localhost:8000/api/models/demo/Product/
```

```json
{
  "count": 50,
  "next": "http://localhost:8000/api/models/demo/Product/?page=2",
  "results": [
    {"id": 1, "name": "Premium Laptop Stand", "price": "49.99", "available": true},
    ...
  ]
}
```

---

## 🧪 Testing

```bash
# Run full test suite
pytest

# With coverage report
pytest --cov=snapadmin --cov=api --cov=demo --cov-report=html

# Run specific tests
pytest tests/test_api_token.py -v
pytest tests/test_model_api.py -v
```

---

## 🌱 Demo Seeder

```bash
# Seed with default 50 objects per type
python manage.py seed_demo

# Custom count
python manage.py seed_demo --count 100

# Wipe and re-seed
python manage.py seed_demo --flush

# Skip Elasticsearch indexing
python manage.py seed_demo --no-index
```

Auto-seed on first migrate by setting `SNAPADMIN_AUTO_SEED=True` in `.env`.

---

## ⚙️ Celery

Start locally:
```bash
# Worker
celery -A sandbox worker --loglevel=info

# Beat scheduler
celery -A sandbox beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

Built-in periodic tasks:
- **02:30 daily** — Purge expired API tokens
- **Every hour**  — Re-index products to Elasticsearch
- **Midnight**    — Generate daily stats snapshot

---

## 🔍 Elasticsearch

Elasticsearch is **optional**. When `ELASTICSEARCH_ENABLED=False` (the default for local dev), all search operations fall back to Django ORM `icontains` queries silently.

Enable in Docker:
```bash
ELASTICSEARCH_ENABLED=True
ELASTICSEARCH_URL=http://elasticsearch:9200
```

---

## 📜 License

MIT License — see [LICENSE](LICENSE).
