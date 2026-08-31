# 🚀 SnapAdmin

**Write your data model once. Get the admin panel, the REST API, the GraphQL endpoint and the search
index — automatically.**

[![Tests](https://github.com/drofji/django-snapadmin/actions/workflows/test.yml/badge.svg)](https://github.com/drofji/django-snapadmin/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/django-snapadmin?logo=pypi&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Downloads](https://img.shields.io/pypi/dm/django-snapadmin)](https://pypi.org/project/django-snapadmin/)
[![Python](https://img.shields.io/pypi/pyversions/django-snapadmin?logo=python&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Django](https://img.shields.io/badge/Django-5.2%20%7C%206.0-092E20?logo=django&logoColor=white)](https://djangoproject.com)
[![License](https://img.shields.io/github/license/drofji/django-snapadmin)](LICENSE)

📚 [Documentation](https://drofji.github.io/django-snapadmin/) ·
📦 [Django Packages](https://djangopackages.org/packages/p/django-snapadmin/) ·
📝 [Changelog](https://github.com/drofji/django-snapadmin/blob/main/CHANGELOG.md) ·
🔒 [Security](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md) ·
🧭 [llms.txt](https://drofji.github.io/django-snapadmin/llms.txt)

---

## What problem does this solve?

Every internal tool needs the same four things: a screen where staff manage the data, an API for the
mobile app, an API for the frontend team, and a search box. Today each one is written and maintained
separately — **four descriptions of the same data**, four places to update when a field changes, four
chances to leak a field you meant to keep private.

SnapAdmin generates all four from **one** description.

> **In one sentence for a decision maker:** it turns weeks of internal-tool plumbing into a
> declaration your developers write once, and keeps the admin panel, the APIs and the search in sync
> automatically — so a field is defined in exactly one place.

|  | Without | With SnapAdmin |
|---|---|---|
| Admin panel | you write it | generated |
| REST API + Swagger docs | you write it | generated |
| GraphQL | you write it | generated |
| Search index | you write it | generated |
| Field defined in | 4 places | **1 place** |

---

## Try it — 60 seconds, no setup

```bash
pip install django-snapadmin
snapadmin-new myshop
cd myshop
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/admin/>. The admin, the REST API (`/api/docs/`) and GraphQL
(`/api/graphql/`) are already running against a working example model. SQLite, no Docker, nothing to
edit by hand.

**Just want to look around first?** `snapadmin-demo` downloads a fully-loaded demo — search, audit
trail, background jobs, the works. Log in at `/admin/` with `admin` / `admin`.

---

## Is your integration actually correct?

A runnable checklist — every row names the command that proves it, not just the thing to remember.
`snapadmin-init` now prints this itself, with a ✅/❌/⚠️ per row (⚠️ = needs a running project to
check, never a false green):

| Check | Verify |
|---|---|
| App boots, models registered | `manage.py check` · `snapadmin_info --section inventory` |
| Migrations applied | `manage.py migrate --check` |
| Auth on the API, PII masked where it matters | `snapadmin_info --section features` |
| Backups on, **2+ destinations**, encryption (**strongly recommended**) | `snapadmin_info --section features` |
| Have you actually run a restore? | `snapadmin_restore <bundle> --confirm` against a recent dump — an untested backup is the most common form of not having one |

→ [Full checklist](https://drofji.github.io/django-snapadmin/#integration-checklist) — Must work /
Should be configured / Data safety / Optional, with a "why it matters" column.

---

## How it works — the whole idea

There are two ways to declare a model, and most projects want the first one: `@snap_model` and
`snap_field()` add SnapAdmin to models and fields you already have, without subclassing or
rewriting anything — the natural starting point for an existing codebase. Subclassing `SnapModel`
below is the full route: reach for it when you want everything SnapAdmin can do (Elasticsearch
mirroring, retention purge, a fully generated admin) on a new model, or you're prototyping fast and
don't yet have a model to preserve.

You add keyword arguments to your fields. They describe how the field should *behave*, and they
**add no database migration**.

```python
# models.py
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name      = snap.SnapCharField(max_length=200, searchable=True)
    price     = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True)
    available = snap.SnapBooleanField(default=True, filterable=True)

    api_write_fields = ["name", "price", "available"]   # what an API client may set
```

```python
# admin.py — one line registers every model
from snapadmin.models import SnapModel
SnapModel.register_all_admins()
```

**That is the whole setup.** You now have:

| URL | What is there |
|---|---|
| `/admin/` | List with a search box on `name`, sidebar filters on `price` and `available`, add/edit forms, change history |
| `/api/models/shop/Product/` | REST create · read · update · delete, with filters, pagination and token auth |
| `/api/docs/` | Swagger UI + ReDoc |
| `/api/graphql/` | GraphQL schema, permission-checked |
| `/dashboard/` | Row counts, service health, scheduled jobs |

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

<details>
<summary>Already have models you cannot rewrite? Use the decorator instead</summary>

Subclassing `SnapModel` is the full route. If your model layer already exists — a brownfield schema,
a base class from a third-party package, fields from `django-money` or `phonenumber_field` — opt in
from the outside instead. `@snap_model` adds no field and no attribute, so it needs **no migration**:

```python
from django.db import models
from snapadmin import snap_model

@snap_model(
    api_write_fields=["name", "price"],   # what an API client may set
    api_exclude_fields=["cost_price"],    # never leaves the server
    search_fields=["name"],               # what ?search= matches on
)
class Product(models.Model):
    name       = models.CharField(max_length=200)
    price      = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
```

You get the REST API, the GraphQL schema, the offline endpoints, the system checks and the
`snapadmin-info` inventory. You **do not** get the parts that need `SnapModel`'s machinery —
Elasticsearch mirroring, the retention purge, the generated admin — and those sweeps skip the model
rather than half-work. [The full comparison
table](https://drofji.github.io/django-snapadmin/#two-ways) says exactly which is which.

Need this for one field rather than a whole model — a single `django-money` or `phonenumber_field`
column on an otherwise ordinary model? `snap_field()` is the same idea at field scope:

```python
from django.db import models
from snapadmin.fields import snap_field

class Product(models.Model):
    name = snap_field(models.CharField(max_length=255), searchable=True, filterable=True)
```

It sets the same attributes a `Snap*Field` sets on itself, on a field instance you already have —
every reader treats the result identically, and it adds no migration either.

You never have to convert a whole model at once, either — a `SnapModel` is a normal Django model, so
bare fields, `snap_field()`-wrapped fields and `Snap*Field`s freely mix in the same class body; only
the fields that need Snap behaviour get it. [Worked example](https://drofji.github.io/django-snapadmin/#mixing-fields).

</details>

→ [Field types](https://drofji.github.io/django-snapadmin/#snap-fields) ·
[SnapModel reference](https://drofji.github.io/django-snapadmin/#snap-model) ·
[`@snap_model` for plain models](https://drofji.github.io/django-snapadmin/#snap-model-decorator) ·
[`snap_field()` for one field](https://drofji.github.io/django-snapadmin/#snap-field-wrapper) ·
[Mixing Snap & plain fields](https://drofji.github.io/django-snapadmin/#mixing-fields)

---

## The commands

| Command | Use it when |
|---|---|
| `snapadmin-new myshop` | **Starting a new project.** Generates one you keep. `--full` adds Docker, PostgreSQL, Redis, Elasticsearch |
| `snapadmin-demo` | **You want to see it first.** Downloads and serves a throwaway demo |
| `snapadmin-init` | **Adding it to a project you already have.** Read-only — prints what is missing and the exact code to paste. Never edits your files |
| `snapadmin-info` | **Is everything configured and healthy?** Versions, database, search, queues, plus a ✓/✗ feature checklist |
| `snapadmin-license-check` | **Can we ship this commercially?** Every dependency's licence, with a verdict |

<details>
<summary>More flags, and the scheduled-job commands</summary>

```bash
snapadmin-new myshop --app-name storefront   # name the example app yourself
snapadmin-new myshop --full                  # + Dockerfile, compose, Postgres/Redis/ES

snapadmin-init --api --graphql               # also check the REST / GraphQL config

snapadmin-info --section features            # just the ✓/✗ capability checklist
snapadmin-info --health-check                # probes only; non-zero exit if one fails
snapadmin-info --json                        # the same report for CI / monitoring

snapadmin-license-check --critical-only      # only what blocks commercial use
```

`snapadmin-info` and `snapadmin-license-check` inspect a live project, so run them from inside one.
Every spelling works — `snapadmin-info` ≡ `python manage.py snapadmin_info`.

Opt-in background commands, none of which run on their own: `snapadmin_reindex`,
`snapadmin_health_alert`, `snapadmin_db_backup`, `snapadmin_send_error_digest`,
`snapadmin_purge_expired_data`, `snapadmin_audit_export`.

> ⏱ **Nothing runs on a schedule by itself.** SnapAdmin ships no daemon — backups, digests and the
> data purge need a Celery Beat entry or a cron line.
> → [Background tasks & scheduling](https://drofji.github.io/django-snapadmin/#celery)

</details>

---

## What you get

**🖥 Admin panel** — list columns, search and filters derived from your fields · themed responsive UI ·
status badges, tabs, inlines, autocomplete · field-level change history · an
[offline mode](https://drofji.github.io/django-snapadmin/#offline) that keeps a list usable with no
connection

**🔌 APIs** — [REST CRUD](https://drofji.github.io/django-snapadmin/#api-rest) with Swagger and
auto-derived filters · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) with
permissions on every traversed relation ·
[API tokens](https://drofji.github.io/django-snapadmin/#api-tokens) hashed at rest · per-model guards
for what may be read and written

**🔍 Search** *(optional)* — [Elasticsearch](https://drofji.github.io/django-snapadmin/#elasticsearch)
with the index mapping derived from your fields · `?search=`
[routed to ES automatically](https://drofji.github.io/django-snapadmin/#es-routing), falling back to
the database when ES is down · [resumable bulk reindex](https://drofji.github.io/django-snapadmin/#bulk-reindex-command)

**⚙️ Operations** — [audit trail](https://drofji.github.io/django-snapadmin/#audit-trail) ·
[GDPR retention](https://drofji.github.io/django-snapadmin/#gdpr) ·
[PII masking](https://drofji.github.io/django-snapadmin/#pii-masking) ·
[backups](https://drofji.github.io/django-snapadmin/#backups) ·
[error and health alerts](https://drofji.github.io/django-snapadmin/#alert-channels) to email, Slack,
Discord, Teams or Telegram · [structured logging](https://drofji.github.io/django-snapadmin/#logging) ·
10 languages

<details>
<summary>How is this different from Unfold, Jazzmin or Grappelli?</summary>

Those are **themes**. They restyle the admin you already wrote — you still write the `ModelAdmin`,
the `list_display`, the `search_fields`, and they make it look modern.

SnapAdmin **generates** that admin from your field declarations, and generates the REST API, the
GraphQL schema and the search mapping from the same ones. It is not a competing theme — it sits a
layer above, and it uses **Unfold as its optional theme**.

|  | Themes | SnapAdmin |
|---|---|---|
| Admin look | ✅ their whole point | Unfold's, via the `[theme]` extra |
| Who writes the `ModelAdmin` | you | generated |
| REST API + Swagger | — | generated |
| GraphQL | — | generated |
| Elasticsearch | — | generated |
| Audit, GDPR, backups, health | — | built in |

**If you only want a better-looking admin, use a theme** — it is less machinery. SnapAdmin earns its
place when the same models must also be an API, a search index and an auditable system of record.

Your hand-written `ModelAdmin` classes are never replaced — SnapAdmin skips any model you registered
yourself.

</details>

<details>
<summary>How fast is it?</summary>

**There are no published benchmark numbers, so this README will not quote any.** What ships instead
is the means to measure it on your own hardware and data:

```bash
python demo/manage.py seed_large            # 100,000 customers and orders
python demo/manage.py benchmark_list_view   # query count + wall time
```

What is *designed in* rather than measured is documented under
[large-dataset tuning](https://drofji.github.io/django-snapadmin/#performance): automatic
`list_select_related` (no admin N+1), estimated counts instead of `COUNT(*)` on large tables, paging
caps, and streaming exports that hold memory flat regardless of result size.

</details>

---

## 🏢 For teams and enterprise

The questions a tech lead or a manager asks before approving a dependency:

| Question | Answer |
|---|---|
| **Can we use it commercially?** | Yes. MIT, and the base install carries **only** permissive licences (MIT/BSD/Apache). Anything copyleft is an opt-in extra, never installed by default |
| **How do we prove that?** | `snapadmin-license-check` audits what you actually installed and returns a verdict. Full inventory in [THIRD_PARTY_NOTICES.md](https://github.com/drofji/django-snapadmin/blob/main/THIRD_PARTY_NOTICES.md) |
| **Who changed that record?** | An [immutable audit trail](https://drofji.github.io/django-snapadmin/#audit-trail) with per-field `old → new` diffs and a per-object timeline |
| **GDPR / data retention?** | Declare `data_retention_days` (+ `data_retention_files` to take uploaded files with the row) per model; the same purge also covers the audit log and, if you opt in, finished export/reindex job files. [Full purge table](https://drofji.github.io/django-snapadmin/#retention-table) |
| **Personal data in the API?** | [PII masking](https://drofji.github.io/django-snapadmin/#pii-masking) — declare a field sensitive once and it is masked in the admin, REST, GraphQL, exports **and** the audit diff. Per-field rules can unlock one field for one permission |
| **Is it tested?** | **100% line coverage** on the shipped package, enforced in CI. The matrix runs Python 3.10–3.13 × Django 5.2/6.0 on every push |
| **Will it break on upgrade?** | A written [API-stability policy](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md): deprecations warn before removal and name their replacement. Still beta — pin an exact version in production |
| **Will it survive our load?** | Read-replica routing, estimated counts, paging caps, streaming exports, and a reusable [quota primitive](https://drofji.github.io/django-snapadmin/#quotas) (`snapadmin.limits.reserve()`) for per-tenant windows, concurrency caps and outbound-call cooldowns. [Enterprise config](https://drofji.github.io/django-snapadmin/#enterprise-config) |
| **Single sign-on?** | [SSO / OAuth2 login helper](https://drofji.github.io/django-snapadmin/#enterprise-config); auth is pluggable — JWT, session, or your own |
| **How do we know it is up?** | Health probes, error-spike alerts and daily digests to email, Slack, Discord, Teams or Telegram. `snapadmin-info --health-check` exits non-zero for your monitoring |
| **Backups?** | [3-2-1 database backups](https://drofji.github.io/django-snapadmin/#backups) — local, network share, offsite over FTPS/SFTP/S3-compatible (AWS, MinIO, Backblaze B2, Hetzner Object Storage, Wasabi — [Storage Box is SFTP, not S3](https://drofji.github.io/django-snapadmin/#storage-box)), optionally **AGE-encrypted** in-stream so a compromised destination never sees plaintext. `SNAPADMIN_BACKUP_INCLUDE` optionally bundles media and an encrypted `.env` alongside the database, with a checksummed manifest and a [restore command](https://drofji.github.io/django-snapadmin/#restore) — dry-run by default, with an automatic pre-restore snapshot and a matching [rollback command](https://drofji.github.io/django-snapadmin/#restore-rollback) |
| **Are we locked in?** | No. It is ordinary Django underneath — models, `ModelAdmin`, DRF viewsets. Override any piece, or stop using the generated ones. Your models need not even inherit from ours: [`@snap_model`](https://drofji.github.io/django-snapadmin/#snap-model-decorator) opts a plain `models.Model` in from the outside |

---

## Install

```bash
pip install django-snapadmin
```

Requires **Python ≥ 3.10** and **Django ≥ 5.2**. The package is **beta** — pin an exact version in
production.

**Adding it to an existing project?** Run `snapadmin-init`. It inspects your project and prints a
checklist plus the exact snippets to paste. It edits nothing, so there is nothing to undo.

<details>
<summary>Minimal <code>INSTALLED_APPS</code> — the smallest thing that works</summary>

Everything below is already installed by `pip install django-snapadmin`; you only have to *list* it.

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

That is a working install. Turning a surface off with `SNAPADMIN_*_ENABLED = False` removes its
routes entirely (404), but you still list the app.

</details>

<details>
<summary>Full <code>INSTALLED_APPS</code> — everything switched on</summary>

Each block corresponds to one optional extra. Add the block **and** the extra, or neither.

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
> `ELASTICSEARCH_ENABLED = True`. Same for `[backup]` (SFTP offsite backups) and `[age]`
> (encrypted backups): a dependency, not an app.

</details>

<details>
<summary>Optional extras and their licences</summary>

The base install is self-contained and carries **only permissive licences** (MIT/BSD/Apache), so it
is safe for commercial and proprietary use. Everything with a licence caveat is opt-in:

| Extra | Pulls in | Gives you |
|-------|----------|-----------|
| `theme` | `django-unfold` | The themed admin UI (stock Django admin without it) |
| `elasticsearch` | `elasticsearch` | Full-text search, `DUAL` / `ES_ONLY` models |
| `celery` | `celery`, `django-celery-beat`, `django-celery-results` | Background tasks: async export, GDPR purge, digests, backups |
| `backup` | `paramiko` | SFTP offsite database backups |
| `age` | `pyrage` | AGE-encrypted backups (MIT — or skip this extra and use the `age` CLI instead) |
| `s3` | `boto3` | S3-compatible offsite database backups (AWS, MinIO, Backblaze B2, Hetzner Object Storage, Wasabi) |
| `extra-settings` | `django-extra-settings` | An in-admin dynamic key/value `Setting` model |
| `wysiwyg` | `django-ckeditor-5` | Rich-text fields — **bundles CKEditor 5 (GPL-or-commercial)** |
| `autocomplete-filter` | `django-admin-autocomplete-filter` | `AutocompleteFilter` list filters (LGPL) |
| `xlsx` | `openpyxl` | XLSX output for the async export API (MIT — optional for size, not licence) |
| `all` | everything above | — |

Run `snapadmin-license-check` after installing to see exactly what you ended up with and whether it
is still proprietary-safe.

→ [Full installation guide](https://drofji.github.io/django-snapadmin/#installation) — compatibility
matrix, extras gotchas, and the MySQL driver licence note.

</details>

---

## Configuration

Every surface is a plain Django setting. Switching one off removes its routes entirely:

```python
SNAPADMIN_REST_API_ENABLED       = True    # REST CRUD endpoints
SNAPADMIN_GRAPHQL_ENABLED        = True    # GraphQL endpoint
SNAPADMIN_SWAGGER_ENABLED        = True    # Swagger UI + ReDoc
SNAPADMIN_URL_PREFIX             = ""      # relocate the whole API surface
SNAPADMIN_CONNECTIVITY_ENABLED   = False   # admin-wide health poll + offline save-guard (opt-in)
```

Don't want to decide all ~90 of them? `SNAPADMIN_PROFILE = "admin"` (or `"api"` / `"full"`) picks
sane defaults for the handful that actually matter — an explicit setting always overrides it.

Misconfiguration shows up **at startup** as a Django system check (`snapadmin.W001`–`W011`,
`E001`–`E007`), not as a mystery at request time.

→ [Every setting, with defaults](https://drofji.github.io/django-snapadmin/#env-vars) ·
[SNAPADMIN_PROFILE presets](https://drofji.github.io/django-snapadmin/#profiles)

<details>
<summary>Extending it — SnapAdmin is meant to be customised, not forked</summary>

- **Add field types** — subclass `SnapField` with your own admin introspection
- **Extend a `SnapModel`** — override `save()`, add managers, mix in your own behaviour
- **Add or override REST endpoints** — mount your router before SnapAdmin's
- **Swap auth, permissions and the ES client** — configuration, no code
- **Override admin templates and the dashboard** — standard Django template resolution

→ [Extending & Overriding guide](https://drofji.github.io/django-snapadmin/#extending)

</details>

<details>
<summary>Running the demo from a clone (full Docker stack)</summary>

`snapadmin-demo` is the fast path. From a clone you also get PostgreSQL, Redis and Elasticsearch:

```bash
git clone https://github.com/drofji/django-snapadmin.git
cd django-snapadmin
cp demo/dist.env demo/.env
docker compose -f demo/docker-compose.yml up --build
```

Then open <http://localhost:8000/admin/> (`admin` / `admin`). The demo lives under
[`demo/`](https://github.com/drofji/django-snapadmin/tree/main/demo) and is **not** published to
PyPI — only `snapadmin/` is.

→ [Demo guide](https://drofji.github.io/django-snapadmin/#demo-setup)

</details>

---

## Documentation

| Topic | |
|-------|--|
| Getting started | [Installation](https://drofji.github.io/django-snapadmin/#installation) · [New project](https://drofji.github.io/django-snapadmin/#scaffold) · [Existing project](https://drofji.github.io/django-snapadmin/#snapadmin-init) · [SnapModel](https://drofji.github.io/django-snapadmin/#snap-model) · [Field types](https://drofji.github.io/django-snapadmin/#snap-fields) · [Admin registration](https://drofji.github.io/django-snapadmin/#admin-registration) |
| APIs | [REST](https://drofji.github.io/django-snapadmin/#api-rest) · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) · [Tokens](https://drofji.github.io/django-snapadmin/#api-tokens) · [Auth / JWT / ETL](https://drofji.github.io/django-snapadmin/#integrating) |
| Search | [Elasticsearch modes](https://drofji.github.io/django-snapadmin/#elasticsearch) · [Query routing](https://drofji.github.io/django-snapadmin/#es-routing) · [Filters](https://drofji.github.io/django-snapadmin/#es-filter) · [Facets](https://drofji.github.io/django-snapadmin/#es-aggregate) · [Deep scan](https://drofji.github.io/django-snapadmin/#es-scan) |
| Operations | [Diagnostics](https://drofji.github.io/django-snapadmin/#snapadmin-info) · [Licence audit](https://drofji.github.io/django-snapadmin/#license-check) · [Celery & scheduling](https://drofji.github.io/django-snapadmin/#celery) · [GDPR](https://drofji.github.io/django-snapadmin/#gdpr) · [Backups](https://drofji.github.io/django-snapadmin/#backups) · [Error monitoring](https://drofji.github.io/django-snapadmin/#error-monitoring) · [Performance](https://drofji.github.io/django-snapadmin/#performance) |
| Reference | [All settings](https://drofji.github.io/django-snapadmin/#env-vars) · [Theming](https://drofji.github.io/django-snapadmin/#theming) · [Enterprise config](https://drofji.github.io/django-snapadmin/#enterprise-config) · [Extending](https://drofji.github.io/django-snapadmin/#extending) · [Migration guides](https://drofji.github.io/django-snapadmin/#migration-guides) |

Upgrading from `drofji-automatically-django-admin`? See the
[migration guide](https://github.com/drofji/django-snapadmin/blob/main/docs/migrations/drofji-automatically-django-admin_to_django-snapadmin.md).

---

## Security

API tokens are hashed at rest, rich-text HTML is sanitized on write, GraphQL enforces permissions on
every traversed relation, and PII masking covers both APIs. Report vulnerabilities privately — see
[SECURITY.md](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md) for the policy, the
supported-versions row and the production-hardening checklist.

## Contributing

See [CONTRIBUTING.md](https://github.com/drofji/django-snapadmin/blob/main/CONTRIBUTING.md). The
suite must stay green with 100% coverage on `snapadmin/`:

```bash
pytest
```

## License

MIT — see [LICENSE](https://github.com/drofji/django-snapadmin/blob/main/LICENSE).
