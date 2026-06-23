# Changelog

All notable changes to `drofji-snapadmin` are documented here.

---

## [0.1.0a1] — 2026-06-23 — First Alpha Release

Initial public alpha of **SnapAdmin** — a declarative Django admin and API package.

### Features

#### Declarative Admin
- `SnapModel` base class: inherit once, get automatic admin registration with a smart `__str__`
- `SnapField` mixin for every Django field type — control `list_display`, `search_fields`, `list_filter` directly on the field definition
- `show_in_list`, `show_in_form`, `searchable`, `filterable`, `editable`, `updatable` flags on every field
- `row` attribute: group fields into horizontal rows in the change form
- `tab` attribute: place fields into named Unfold tabs
- `wysiwyg` attribute: enable CKEditor 5 for `TextField` / `SnapTextField`
- `SnapFunctionField`: computed display columns with optional safe HTML output
- `SnapStatusBadgeField`: colour-coded pill badges for choice/status fields

#### Unfold Integration (optional)
- Native `django-unfold` support: compressed fields, unsaved-form warning, submit filter button, tabs
- Graceful fallback to Django's built-in `ModelAdmin` when Unfold is not installed

#### Change Logging
- `SnapSaveMixin`: automatic field-level change tracking (`old → new`) via Django's `LogEntry`
- Tracks inline changes in related formsets

#### REST API
- Auto-generated CRUD endpoints for every `SnapModel` subclass — zero extra code
- `GET/POST /api/models/{app}/{Model}/` — list and create
- `GET/PUT/PATCH/DELETE /api/models/{app}/{Model}/{id}/` — detail, update, delete
- `GET /api/models/schema/` — introspect all available endpoints and fields
- Toggle via `SNAPADMIN_REST_API_ENABLED` setting

#### GraphQL API
- Dynamic schema generation via Graphene-Django for all `SnapModel` subclasses
- Single-object and list resolvers auto-created per model
- Accessible at `/api/graphql/` with GraphiQL enabled
- Toggle via `SNAPADMIN_GRAPHQL_ENABLED` setting

#### API Documentation
- Interactive Swagger UI at `/api/docs/` via `drf-spectacular`
- ReDoc at `/api/redoc/`
- Toggle via `SNAPADMIN_SWAGGER_ENABLED` setting

#### Token Authentication
- `APIToken` model: named tokens with expiry, per-model access control, and active/inactive toggle
- `Authorization: Token <key>` header authentication for all API endpoints
- `APIToken.create_for_user()` helper for programmatic token creation
- Built-in Celery task `purge_expired_tokens` to clean up expired tokens

#### Elasticsearch Integration
- Three storage modes per model: `DB_ONLY` (default), `DUAL`, `ES_ONLY`
- `es_search(query_string, limit)` — fuzzy full-text search via ES with DB fallback
- `es_reindex_all()` — bulk re-index all records
- Auto index and mapping management on `post_migrate`
- Toggle globally via `ELASTICSEARCH_ENABLED` setting

#### Structured Logging
- `configure_logging(log_level, json_logs)` — call once in `settings.py`
- Colourised human-readable output for development; JSON lines for production/Docker
- `get_logger(__name__)` for any module; `SnapAdminLogger` for SnapAdmin internals

#### Demo & Sandbox
- Complete `demo/` app: Product, Customer, Order models with complex relationships
- Custom system dashboard with environment health checks and analytics
- `python manage.py seed_demo` to populate the environment instantly
- Docker Compose stack: Django + PostgreSQL + Redis + Elasticsearch
- Celery background tasks: ES re-indexing, daily stats generation

### Available SnapField Types
`SnapCharField`, `SnapTextField`, `SnapEmailField`, `SnapSlugField`, `SnapURLField`,
`SnapUUIDField`, `SnapIntegerField`, `SnapPositiveIntegerField`, `SnapFloatField`,
`SnapDecimalField`, `SnapBigIntegerField`, `SnapDateField`, `SnapDateTimeField`,
`SnapTimeField`, `SnapDurationField`, `SnapFileField`, `SnapImageField`,
`SnapBooleanField`, `SnapJSONField`, `SnapGenericIPAddressField`,
`SnapForeignKey`, `SnapOneToOneField`, `SnapManyToManyField`

### Requirements
- Python >= 3.10
- Django >= 5.2

### Known Issues
- Celery startup in the sandbox requires a `celery.py` app module in the `sandbox` package
- Swagger filter schemas are not yet auto-generated for all SnapField types
- `ES_ONLY` mode uses a pseudo-random integer PK — not suitable for high-concurrency writes

### Installation
```bash
pip install drofji-snapadmin
```
