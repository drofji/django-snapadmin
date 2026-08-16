# 🚀 SnapAdmin — Declarative Django Admin & API

**Describe a model's fields once. Get a full Django admin, a REST API with Swagger docs, a GraphQL
endpoint and optional Elasticsearch search — no boilerplate.** Every surface is one setting away from
being switched off.

[![Tests](https://github.com/drofji/django-snapadmin/actions/workflows/test.yml/badge.svg)](https://github.com/drofji/django-snapadmin/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/django-snapadmin?logo=pypi&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Downloads](https://img.shields.io/pypi/dm/django-snapadmin)](https://pypi.org/project/django-snapadmin/)
[![Python](https://img.shields.io/pypi/pyversions/django-snapadmin?logo=python&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Django](https://img.shields.io/badge/Django-5.2%20%7C%206.0-092E20?logo=django&logoColor=white)](https://djangoproject.com)
[![License](https://img.shields.io/github/license/drofji/django-snapadmin)](LICENSE)

📚 **[Full Documentation](https://drofji.github.io/django-snapadmin/)** ·
📦 **[Django Packages](https://djangopackages.org/packages/p/django-snapadmin/)** ·
📝 **[Changelog](https://github.com/drofji/django-snapadmin/blob/main/CHANGELOG.md)** ·
🔒 **[Security policy](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md)** ·
🧭 **[llms.txt](https://drofji.github.io/django-snapadmin/llms.txt)** (docs map for AI assistants)

---

## 🏁 See it running in 60 seconds

No project, no config, nothing to clone:

```bash
pip install django-snapadmin
snapadmin-demo
```

That downloads a ready-made demo project, migrates it, seeds sample data and serves it on
<http://localhost:8000>. Log in at `/admin/` with **`admin` / `admin`** and click around. Delete
`~/.cache/snapadmin-demo/` when you're done.

> **Upgraded SnapAdmin later?** `pip install -U django-snapadmin` upgrades the package but not the
> `demo/` directory it once extracted — that tree keeps its old models and templates. Re-run
> `snapadmin-demo` to refresh it; it tells you which release the existing tree came from and
> removes files the new release no longer ships (never files you added).

---

## 🧠 The idea, in three steps

**1. Declare the model.** The `Snap*Field` kwargs describe how the field should *behave* — in the
admin, in the API, in search. They add **no database migration**; they are stripped before Django
sees the field.

```python
# models.py
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name      = snap.SnapCharField(max_length=200, searchable=True, show_in_list=True)
    price     = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)
    available = snap.SnapBooleanField(default=True, filterable=True)
```

**2. Turn the surfaces you want on.**

```python
# settings.py
SNAPADMIN_REST_API_ENABLED = True   # /api/… CRUD
SNAPADMIN_GRAPHQL_ENABLED  = True   # /api/graphql/
SNAPADMIN_SWAGGER_ENABLED  = True   # /api/docs/
```

**3. Register.** One line — every `SnapModel` in the project gets an admin.

```python
# admin.py
from snapadmin.models import SnapModel
SnapModel.register_all_admins()
```

**You now have:**

| | |
|---|---|
| `/admin/` | list view with a search box on `name`, sidebar filters for `price` (range) and `available`, an add/edit form, and field-level change logging |
| `/api/models/demo/Product/` | REST list · retrieve · create · update · delete, with filtering, pagination and token auth |
| `/api/docs/` | Swagger UI + ReDoc, generated from the same models |
| `/api/graphql/` | a Graphene schema with `allDemoProducts`, permission-checked per relation |
| `/dashboard/` | row counts per model, service health, scheduled jobs |

```
┌────────────────────────────────────────────────────────────┐
│  SnapAdmin                           🔍 Search...    admin ▾│
├──────────────┬─────────────────────────────────────────────┤
│  SHOP        │  Products                         + Add     │
│  Categories  │ ┌──────────────────────────────────────────┐│
│  Products    │ │ Name            Price  In Stock  Category ││
│  Customers   │ │ Premium Laptop  $249   ● Active   Audio   ││
│  Orders      │ │ Ergonomic Mouse $89    ● Active   Access. ││
│  SYSTEM      │ │ USB-C Hub       $49    ○ Out      Electr. ││
│  Dashboard   │ └──────────────────────────────────────────┘│
└──────────────┴─────────────────────────────────────────────┘
```

→ **[Field types](https://drofji.github.io/django-snapadmin/#snap-fields)** ·
**[SnapModel reference](https://drofji.github.io/django-snapadmin/#snap-model)** ·
**[Admin registration](https://drofji.github.io/django-snapadmin/#admin-registration)**

---

## 🧭 How this differs from Unfold, Jazzmin and Grappelli

Those are **themes**: they restyle the admin you have written. You still write the `ModelAdmin` —
the `list_display`, the `search_fields`, the filters — and they make it look modern.

SnapAdmin **generates** that admin from your field declarations, and generates the REST API,
the GraphQL schema and the search mapping from the same ones. It is not an alternative theme;
it sits a layer above, and it uses **Unfold as its optional theme** (`[theme]` extra). Keep your
theme, keep your hand-written `ModelAdmin` where you want one — SnapAdmin never replaces an admin
class you registered yourself.

| | Themes (Unfold · Jazzmin · Grappelli) | SnapAdmin |
|---|---|---|
| Admin look | ✅ their whole point | Unfold's, when you install `[theme]` |
| Who writes the `ModelAdmin` | you | generated from the field kwargs |
| REST API + OpenAPI/Swagger | — | generated from the same fields |
| GraphQL schema | — | generated from the same fields |
| Elasticsearch indexing/search | — | generated from the same fields |
| Ops (audit log, GDPR purge, backups, health) | — | built in, each one setting away |

If all you want is a better-looking admin, use a theme — it is less machinery. SnapAdmin earns its
place when the same models also have to be an API, a search index and an auditable system of record,
and you would rather declare that once than maintain four descriptions of the same fields.

---

## 🖥 The four commands

| Command | Answers | What it does |
|---|---|---|
| `snapadmin-demo` | *"What does this thing actually look like?"* | Downloads and serves a throwaway demo project. Needs no project of your own |
| `snapadmin-init` | *"How do I add this to my existing project?"* | A **read-only** doctor: prints a present/missing checklist and ready-to-paste snippets. It never edits your code |
| `snapadmin-info` | *"Is it configured correctly, and is everything up?"* | Version, database, Elasticsearch, Celery, models, system checks, plus a ✓/✗ **feature-adoption audit** |
| `snapadmin-license-check` | *"Can I ship this commercially?"* | The licence and 🟢/🟡/🔴 tier of every installed dependency, with a verdict |

The last two inspect a live project, so run them from inside one. They are `manage.py` commands with
a shell shim, and **every spelling works** — the shim just finds your `manage.py` and forwards:

```bash
snapadmin-info            # ≡ snapadmin_info  ≡  python manage.py snapadmin_info
snapadmin-license-check   # ≡ snapadmin_license_check
```

```bash
snapadmin-init                                  # what's missing to wire SnapAdmin in
snapadmin-init --api --graphql                  # also check the REST / GraphQL config

snapadmin-info                                  # full diagnostic report
snapadmin-info --section features               # just the ✓/✗ capability checklist
snapadmin-info --health-check                   # probes only; non-zero exit if one fails
snapadmin-info --json                           # the same report for CI / monitoring
snapadmin-info --verbose                        # + the full text of any system-check message

snapadmin-license-check                         # every dependency's licence + tier
snapadmin-license-check --critical-only         # only what blocks commercial use
```

Other management commands, all opt-in and none of them running on their own:
`snapadmin_reindex` (Elasticsearch), `snapadmin_health_alert`, `snapadmin_db_backup`,
`snapadmin_send_error_digest`, `snapadmin_purge_expired_data`, `snapadmin_audit_export`.

> ⏱ **Nothing runs on a schedule by itself.** SnapAdmin ships no daemon — the retention purge,
> digests and backups need a Celery Beat entry or a cron line.
> → **[Background tasks & scheduling](https://drofji.github.io/django-snapadmin/#celery)**

---

## 📦 Install

```bash
pip install django-snapadmin
```

Requires **Python ≥ 3.10** and **Django ≥ 5.2**. The package is **beta** — the public API is
stabilising but may still change before `0.1.0` stable, so pin an exact version in production.

### Minimal `INSTALLED_APPS` — the smallest thing that works

Everything below is installed for you by `pip install django-snapadmin`; you only have to *list* it.

```python
INSTALLED_APPS = [
    # ── Django itself ───────────────────────────────────────────────────────
    "django.contrib.admin",          # SnapAdmin generates ModelAdmins into this site
    "django.contrib.auth",           # permissions gate both the admin and the API
    "django.contrib.contenttypes",   # required by auth; the audit trail keys off it
    "django.contrib.sessions",       # admin login
    "django.contrib.messages",       # admin "saved successfully" banners
    "django.contrib.staticfiles",    # serves SnapAdmin's CSS/JS

    # ── The API stack (pulled in as dependencies — just list them) ───────────
    "rest_framework",                # the generated REST endpoints are DRF viewsets
    "drf_spectacular",               # builds the OpenAPI schema behind /api/docs/
    "django_filters",                # backs the auto-generated ?field=… query filters
    "graphene_django",               # the generated GraphQL schema

    # ── SnapAdmin ───────────────────────────────────────────────────────────
    "snapadmin",

    "myapp",                         # …your own apps
]
```

```python
# urls.py
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("snapadmin.urls")),   # REST + Swagger + GraphQL
]
```

That's a working install: themed-less admin, REST, Swagger and GraphQL. Turning a surface off with
`SNAPADMIN_*_ENABLED = False` removes its routes (404) but you still list the app.

### Full `INSTALLED_APPS` — everything switched on

Each block below corresponds to one optional extra. Add the block **and** the extra, or neither.

```python
INSTALLED_APPS = [
    # ── Themed UI — pip install django-snapadmin[theme] ─────────────────────
    # MUST come before django.contrib.admin: Unfold overrides admin templates,
    # and Django resolves templates in INSTALLED_APPS order.
    "unfold",
    "unfold.contrib.filters",        # the sidebar range/dropdown filters SnapAdmin generates
    "unfold.contrib.forms",          # themed form widgets
    "unfold.contrib.inlines",        # themed inline formsets

    # ── Rich text — pip install django-snapadmin[wysiwyg] ───────────────────
    # Only needed for wysiwyg=True / SnapRichTextField. Bundles CKEditor 5,
    # which is GPL-or-commercial — that is why it is not a core dependency.
    "django_ckeditor_5",

    # ── Django itself ───────────────────────────────────────────────────────
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # ── The API stack (always required) ─────────────────────────────────────
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "graphene_django",

    # ── SnapAdmin ───────────────────────────────────────────────────────────
    "snapadmin",

    # ── Background tasks — pip install django-snapadmin[celery] ─────────────
    # Needed for the GDPR purge, async exports, error digests and backups.
    "django_celery_beat",            # edit the schedule from the admin
    "django_celery_results",         # store task results in the database

    # ── Admin-editable settings — pip install django-snapadmin[extra-settings]
    # SnapAdmin does not use it; add it if you want a runtime key/value Setting
    # model in the admin (the demo shows the pattern).
    "extra_settings",

    # ── Autocomplete list filters — [autocomplete-filter] (LGPL) ────────────
    # For your own AutocompleteFilter admin filters; SnapAdmin core never imports it.
    "admin_auto_filters",

    "myapp",
]
```

> **Elasticsearch needs no app entry** — `pip install django-snapadmin[elasticsearch]` and set
> `ELASTICSEARCH_ENABLED = True`. Same for `[backup]` (SFTP offsite backups): a dependency, not an app.

### Optional extras

The base install is self-contained and carries **only permissive licences** (MIT/BSD/Apache), so it
is safe for commercial and proprietary use. Everything with a licence caveat is opt-in:

| Extra | Pulls in | Gives you |
|-------|----------|-----------|
| `theme` | `django-unfold` | The themed admin UI (stock Django admin without it) |
| `elasticsearch` | `elasticsearch` | Full-text search, `DUAL` / `ES_ONLY` models |
| `celery` | `celery`, `django-celery-beat`, `django-celery-results` | Background tasks: async export, GDPR purge, digests, backups |
| `backup` | `paramiko` | SFTP offsite database backups |
| `extra-settings` | `django-extra-settings` | An in-admin dynamic key/value `Setting` model |
| `wysiwyg` | `django-ckeditor-5` | Rich-text fields — **bundles CKEditor 5 (GPL-or-commercial)** |
| `autocomplete-filter` | `django-admin-autocomplete-filter` | `AutocompleteFilter` list filters (LGPL) |
| `all` | everything above | — |

Run `python manage.py snapadmin_license_check` after installing to see exactly what you ended up
with and whether it is still proprietary-safe.

→ **[Full installation guide](https://drofji.github.io/django-snapadmin/#installation)** —
compatibility matrix, extras gotchas, and the MySQL driver licence note.

### Adding it to a project you already have

```bash
pip install django-snapadmin
snapadmin-init
```

`snapadmin-init` inspects your project and prints a per-item checklist plus the exact snippets to
paste — the `INSTALLED_APPS` ordering, the URL include, the settings block. It **edits nothing**, so
there is no bad automatic change to undo.
→ **[Integration guide](https://drofji.github.io/django-snapadmin/#snapadmin-init)**

---

## ✨ What you get

**Admin**
- `list_display`, `search_fields` and `list_filter` derived from the field kwargs — no `ModelAdmin` to write
- Themed responsive UI with the `[theme]` extra, colour-coded [status badges](https://drofji.github.io/django-snapadmin/#status-badges), tabs and horizontal field rows
- Date and numeric range filters, autocomplete foreign keys, inlines
- Field-level change logging (`old → new`) with a history view
- [Offline mode](https://drofji.github.io/django-snapadmin/#offline) — a per-model IndexedDB cache that keeps a list view usable with no connection and syncs on reconnect

**APIs**
- [REST CRUD](https://drofji.github.io/django-snapadmin/#api-rest) per model, with Swagger + ReDoc and filters derived from each field's type — ranges, `__in`, `__isnull`, text lookups, JSON paths
- [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) from the same models, with permissions enforced on every traversed relation
- [API tokens](https://drofji.github.io/django-snapadmin/#api-tokens) hashed at rest, shown once, scoped per model — or plug in [JWT / session / your own auth](https://drofji.github.io/django-snapadmin/#integrating)
- Per-model guards: `api_exclude_fields` (never leaves the server), `api_write_fields` (mass-assignment allowlist), [`api_read_only`](https://drofji.github.io/django-snapadmin/#api-read-only) (writes answer 405), PII masking

**Search (optional)**
- Per-model [`DB_ONLY` / `DUAL` / `ES_ONLY`](https://drofji.github.io/django-snapadmin/#elasticsearch) storage, with the index mapping derived from the fields
- `?search=` on a `DUAL` model is [routed to Elasticsearch](https://drofji.github.io/django-snapadmin/#es-routing) automatically (fuzzy, ranked); plain listings stay on SQL
- Query helpers that fall back to the database when ES is down: [`es_filter()`](https://drofji.github.io/django-snapadmin/#es-filter), [`es_aggregate()`](https://drofji.github.io/django-snapadmin/#es-aggregate), [`es_count()`](https://drofji.github.io/django-snapadmin/#es-count), [`es_scan()`](https://drofji.github.io/django-snapadmin/#es-scan)
- [Resumable bulk reindex](https://drofji.github.io/django-snapadmin/#bulk-reindex-command) with live progress, `--resume`, `--parallel` and `--limit`

**Operations**
- [GDPR retention](https://drofji.github.io/django-snapadmin/#gdpr) (`data_retention_days`) and an immutable audit trail
- [Error monitoring](https://drofji.github.io/django-snapadmin/#error-monitoring) with spike alerts and daily grouped digests, plus health-probe emails when a subsystem goes down
- [3-2-1 database backups](https://drofji.github.io/django-snapadmin/#backups) — local, network share, offsite FTPS/SFTP
- [Large-dataset tuning](https://drofji.github.io/django-snapadmin/#performance) — automatic `list_select_related` (no admin N+1), estimated counts, paging caps
- [ETL helpers](https://drofji.github.io/django-snapadmin/#integrating) — `upsert_from_source()` and `stale_sync()` with a wipe guard
- [Structured logging](https://drofji.github.io/django-snapadmin/#logging) via `structlog`; the UI is [translated into 10 locales](https://drofji.github.io/django-snapadmin/#i18n)

### How fast is it?

**There are no published benchmark numbers, so this README will not quote any.** What ships instead
is the means to measure it on your own hardware and data shape, which is the only figure worth
acting on:

```bash
python demo/manage.py seed_large              # 100,000 customers and orders, batched bulk_create
python demo/manage.py benchmark_list_view     # admin changelist: query count + wall time
```

`benchmark_list_view` runs the changelist queryset with and without SnapAdmin's automatic
`list_select_related`, touching a foreign key on every row, so the N+1 it removes shows up in the
query count rather than in prose. What is designed in — rather than measured — is documented under
[large-dataset tuning](https://drofji.github.io/django-snapadmin/#performance): estimated counts in
place of `COUNT(*)` on large tables, paging caps, and streaming exports and `es_scan()` that hold
memory flat regardless of result size.

---

## ⚙️ Configuration

Every surface is a plain Django setting; switching one off removes its URL routes entirely (404):

```python
SNAPADMIN_REST_API_ENABLED     = True   # REST CRUD endpoints
SNAPADMIN_GRAPHQL_ENABLED      = True   # GraphQL endpoint
SNAPADMIN_SWAGGER_ENABLED      = True   # Swagger UI + ReDoc
SNAPADMIN_ES_QUERY_ROUTING     = True   # route ?search= on DUAL models to Elasticsearch
SNAPADMIN_GRAPHQL_REQUIRE_AUTH = True   # auth + per-model perms on every resolver
SNAPADMIN_URL_PREFIX           = ""     # relocate the whole API surface
```

Misconfiguration shows up at startup as a Django system check (`snapadmin.W001`–`W007`), not as a
mystery at request time — read those first when something behaves unexpectedly.

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

## 🌟 The demo, the long way

`snapadmin-demo` (above) is the fast path. From a clone you also get the **full Docker stack** with
PostgreSQL, Redis and Elasticsearch. The demo lives under
[`demo/`](https://github.com/drofji/django-snapadmin/tree/main/demo) with example models (Product,
Customer, Order) and a seeded database; it is **not** published to PyPI — only `snapadmin/` is.

```bash
git clone https://github.com/drofji/django-snapadmin.git
cd django-snapadmin
cp demo/dist.env demo/.env
docker compose -f demo/docker-compose.yml up --build
```

Then open <http://localhost:8000/admin/> (`admin` / `admin`).

→ **[Demo guide](https://drofji.github.io/django-snapadmin/#demo-setup)** — Traefik overlays with
HTTPS, the Elasticsearch profile, manual setup without Docker, and the seed command.

---

## 📖 Documentation

| Topic | |
|-------|--|
| Getting started | [Installation](https://drofji.github.io/django-snapadmin/#installation) · [Integrate an existing project](https://drofji.github.io/django-snapadmin/#snapadmin-init) · [SnapModel](https://drofji.github.io/django-snapadmin/#snap-model) · [Field types](https://drofji.github.io/django-snapadmin/#snap-fields) · [Admin registration](https://drofji.github.io/django-snapadmin/#admin-registration) |
| APIs | [REST](https://drofji.github.io/django-snapadmin/#api-rest) · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) · [Tokens](https://drofji.github.io/django-snapadmin/#api-tokens) · [Integrating auth / JWT / ETL](https://drofji.github.io/django-snapadmin/#integrating) |
| Search | [Elasticsearch modes](https://drofji.github.io/django-snapadmin/#elasticsearch) · [Query routing](https://drofji.github.io/django-snapadmin/#es-routing) · [Filters](https://drofji.github.io/django-snapadmin/#es-filter) · [Facets](https://drofji.github.io/django-snapadmin/#es-aggregate) · [Deep scan](https://drofji.github.io/django-snapadmin/#es-scan) |
| Operations | [Diagnostics (`snapadmin_info`)](https://drofji.github.io/django-snapadmin/#snapadmin-info) · [Licence audit](https://drofji.github.io/django-snapadmin/#license-check) · [Celery & scheduling](https://drofji.github.io/django-snapadmin/#celery) · [GDPR](https://drofji.github.io/django-snapadmin/#gdpr) · [Backups](https://drofji.github.io/django-snapadmin/#backups) · [Error monitoring](https://drofji.github.io/django-snapadmin/#error-monitoring) · [Performance](https://drofji.github.io/django-snapadmin/#performance) |
| Reference | [All settings](https://drofji.github.io/django-snapadmin/#env-vars) · [Theming](https://drofji.github.io/django-snapadmin/#theming) · [Enterprise config](https://drofji.github.io/django-snapadmin/#enterprise-config) · [Extending](https://drofji.github.io/django-snapadmin/#extending) · [Migration guides](https://drofji.github.io/django-snapadmin/#migration-guides) |

Upgrading from `drofji-automatically-django-admin`? See the
**[migration guide](https://github.com/drofji/django-snapadmin/blob/main/docs/migrations/drofji-automatically-django-admin_to_django-snapadmin.md)**.

---

## 🔒 Security

API tokens are hashed at rest, rich-text HTML is sanitized before display, GraphQL enforces
permissions on every traversed relation, and PII masking is available on both APIs. Report
vulnerabilities privately — see
[SECURITY.md](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md) for the policy, the
supported-versions row, and the production-hardening checklist.

Third-party dependency licences are inventoried in
[THIRD_PARTY_NOTICES.md](https://github.com/drofji/django-snapadmin/blob/main/THIRD_PARTY_NOTICES.md)
— or run `python manage.py snapadmin_license_check` for the same inventory computed from what you
actually installed, with a commercial-usability verdict.

## 🤝 Contributing

See [CONTRIBUTING.md](https://github.com/drofji/django-snapadmin/blob/main/CONTRIBUTING.md). The
suite must stay green with 100% coverage on `snapadmin/`:

```bash
pytest
```

## 📜 License

MIT — see [LICENSE](https://github.com/drofji/django-snapadmin/blob/main/LICENSE).
