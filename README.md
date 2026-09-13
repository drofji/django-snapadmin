# 🚀 SnapAdmin

**Write your data model once. Get the admin panel, the REST API, the GraphQL endpoint and the search
index — automatically.**

[![Tests](https://github.com/drofji/django-snapadmin/actions/workflows/test.yml/badge.svg)](https://github.com/drofji/django-snapadmin/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](#quality--compatibility)
[![PyPI](https://img.shields.io/pypi/v/django-snapadmin?logo=pypi&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Downloads](https://img.shields.io/pypi/dm/django-snapadmin)](https://pypi.org/project/django-snapadmin/)
[![Python](https://img.shields.io/pypi/pyversions/django-snapadmin?logo=python&logoColor=white)](https://pypi.org/project/django-snapadmin/)
[![Django](https://img.shields.io/badge/Django-5.2%20%7C%206.0-092E20?logo=django&logoColor=white)](https://djangoproject.com)
[![License](https://img.shields.io/github/license/drofji/django-snapadmin)](https://github.com/drofji/django-snapadmin/blob/main/LICENSE)

📚 [Documentation](https://drofji.github.io/django-snapadmin/) ·
📦 [Django Packages](https://djangopackages.org/packages/p/django-snapadmin/) ·
📝 [Changelog](https://github.com/drofji/django-snapadmin/blob/main/CHANGELOG.md) ·
🔒 [Security](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md) ·
🧭 [llms.txt](https://drofji.github.io/django-snapadmin/llms.txt)

---

## Start where you are

| You are | Read this | Time |
|---|---|---|
| **Deciding whether to adopt it** — CTO, tech lead, architect | [Why this exists](#why-this-exists) → [Proof it holds up](#proof-it-holds-up) → [Enterprise checklist](#for-teams-and-enterprise) | ~3 min |
| **Shipping something today** — developer, intern, new joiner | [60-second try](#try-it--60-seconds-no-setup) → [Your first model](#your-first-model--3-steps) → [Cheat sheet](#the-cheat-sheet--the-kwargs-youll-actually-use) | ~5 min |
| **Already using it** | [Changelog](https://github.com/drofji/django-snapadmin/blob/main/CHANGELOG.md) · [Upgrade guides](https://github.com/drofji/django-snapadmin/tree/main/docs/migrations) · [All settings](https://drofji.github.io/django-snapadmin/#env-vars) | — |

---

# Why this exists

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
| Audit trail, GDPR retention, PII masking, backups | you write it (or you don't) | built in |
| Field defined in | 4 places | **1 place** |

**What that buys you, concretely.** The admin screen your ops team asks for on Friday is a keyword
argument, not a sprint. A new field reaches the API, the search index and the audit log the moment
it reaches the model — there is no second ticket, and no "we forgot to add it to the serializer"
incident. And the fields you *don't* want exposed are excluded once, in the model, rather than
re-excluded in every surface by whoever writes it next.

**What it is not.** It is not a theme, not a framework, and not a lock-in. Underneath it is ordinary
Django — models, `ModelAdmin`, DRF viewsets. Override any generated piece, or stop using it, without
rewriting your data layer. Your hand-written `ModelAdmin` classes are never replaced: SnapAdmin
skips any model you registered yourself.

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

</details>

---

## Proof it holds up

Adoption risk is the real question, so every answer below is something you can verify yourself
rather than take on trust.

| Question | Evidence |
|---|---|
| **Is it tested?** | **4,600+ tests** and **100% line coverage** on the shipped package (11,000+ statements), enforced in CI — the build fails below 100% |
| **On our Python and Django?** | Every push runs the full matrix: **Python 3.10–3.13 × Django 5.2 / 6.0** |
| **Will an upgrade break us?** | **270+ tests exist only to fail** if a public name, signature or default changes — a breaking change cannot ship by accident |
| **Are the docs actually true?** | **95+ tests** assert that the README, the docs site and the in-package module map describe the code that really ships |
| **Does the whole pipeline still connect?** | An end-to-end smoke test posts the real admin form, then proves the REST API serves that same row and the audit trail recorded it |
| **Is `snapadmin-info` telling the truth?** | **200+ tests** cover the diagnostics report; every capability probe is tested both switched **on and off**, so the readiness audit cannot report a false green |
| **Can we ship it commercially?** | MIT. The base install carries **only** permissive licences (MIT/BSD/Apache); anything copyleft is an opt-in extra. `snapadmin-license-check` audits what you actually installed |

→ [The full quality story](#quality--compatibility), with the names of the test files, so a reviewer
can read them.

---

## For teams and enterprise

The questions a tech lead or a manager asks before approving a dependency:

| Question | Answer |
|---|---|
| **Can we use it commercially?** | Yes. MIT, and the base install carries **only** permissive licences (MIT/BSD/Apache). Anything copyleft is an opt-in extra, never installed by default |
| **How do we prove that?** | `snapadmin-license-check` audits what you actually installed and returns a verdict. Full inventory in [THIRD_PARTY_NOTICES.md](https://github.com/drofji/django-snapadmin/blob/main/THIRD_PARTY_NOTICES.md) |
| **Who changed that record?** | An [immutable audit trail](https://drofji.github.io/django-snapadmin/#audit-trail) with per-field `old → new` diffs and a per-object timeline |
| **GDPR / data retention?** | Declare `data_retention_days` (+ `data_retention_files` to take uploaded files with the row) per model; the same purge also covers the audit log and, if you opt in, finished export/reindex job files. [Full purge table](https://drofji.github.io/django-snapadmin/#retention-table) |
| **A subject requests everything about them?** | `manage.py snapadmin_subject_request export\|delete` walks every model's declared `subject_path` — unmasked export (optionally AGE-encrypted), or a dry-run-by-default deletion that refuses up front if a protected relation would block it. [Details](https://drofji.github.io/django-snapadmin/#gdpr-subject-request) |
| **Personal data in the API?** | [PII masking](https://drofji.github.io/django-snapadmin/#pii-masking) — declare a field sensitive once and it is masked in the admin, REST, GraphQL, exports **and** the audit diff. Per-field rules can unlock one field for one permission |
| **Only HR should see salary?** | [`api_field_permissions`](https://drofji.github.io/django-snapadmin/#field-permissions) gates a field's very presence, per Django permission — absent from a response for anyone lacking it, an explicit `400` naming the field on a denied write, orthogonal to masking (which only controls display) |
| **Multi-tenant SaaS?** | [Row-level tenant isolation](https://drofji.github.io/django-snapadmin/#multi-tenancy) — opt a model in with `tenant_scoped = True` plus a tenant column, and every generated surface (admin, REST, GraphQL, Elasticsearch routing, exports, imports, the offline cache) becomes unreachable without a bound tenant: default-deny, not opt-out. Logical isolation, not physical — the limitation is documented as plainly as the feature |
| **Is it tested?** | **100% line coverage** on the shipped package, enforced in CI, across 4,600+ tests. The matrix runs Python 3.10–3.13 × Django 5.2/6.0 on every push. [What those tests cover](#quality--compatibility) |
| **Will it break on upgrade?** | A written [API-stability policy](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md), covered by semantic versioning as of `1.0`: deprecations warn before removal and name their replacement — and a [contract suite](#backward-compatibility-is-a-test-not-a-promise) fails the build if a public name changes |
| **Will it survive our load?** | Read-replica routing, estimated counts, paging caps, streaming exports, and a reusable [quota primitive](https://drofji.github.io/django-snapadmin/#quotas) (`snapadmin.limits.reserve()`) for per-tenant windows, concurrency caps and outbound-call cooldowns. [Enterprise config](https://drofji.github.io/django-snapadmin/#enterprise-config) |
| **Single sign-on?** | [SSO / OAuth2 login helper](https://drofji.github.io/django-snapadmin/#enterprise-config); auth is pluggable — JWT, session, or your own |
| **How do we know it is up?** | Health probes, error-spike alerts and daily digests to email, Slack, Discord, Teams or Telegram. `snapadmin-info --health-check` exits non-zero for your monitoring |
| **Backups?** | [3-2-1 database backups](https://drofji.github.io/django-snapadmin/#backups) — local, network share, offsite over FTPS/SFTP/S3-compatible (AWS, MinIO, Backblaze B2, Hetzner Object Storage, Wasabi — [Storage Box is SFTP, not S3](https://drofji.github.io/django-snapadmin/#storage-box)), optionally **AGE-encrypted** in-stream so a compromised destination never sees plaintext. `SNAPADMIN_BACKUP_INCLUDE` optionally bundles media and an encrypted `.env` alongside the database, with a checksummed manifest and a [restore command](https://drofji.github.io/django-snapadmin/#restore) — dry-run by default, with an automatic pre-restore snapshot and a matching [rollback command](https://drofji.github.io/django-snapadmin/#restore-rollback) |
| **Are we locked in?** | No. It is ordinary Django underneath — models, `ModelAdmin`, DRF viewsets. Override any piece, or stop using the generated ones. Your models need not even inherit from ours: [`@snap_model`](https://drofji.github.io/django-snapadmin/#snap-model-decorator) opts a plain `models.Model` in from the outside |
| **Outgrowing one database?** | [Declarative sharding and read-replica routing](#database-sharding-and-replica-routing) — any number of shards/replicas from one settings dict, opt-in per model, with automatic failover. [Details](https://drofji.github.io/django-snapadmin/#sharding) |
| **What is coming next?** | Whatever lands next follows the rule everything here follows: **additive, opt-in and completely inert until you configure it** — an install that ignores a new feature is byte-for-byte unaffected, with no new setting required and no migration from the package itself. [Changelog](https://github.com/drofji/django-snapadmin/blob/main/CHANGELOG.md) for what has actually shipped |

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

## Database sharding and replica routing

Outgrowing a single PostgreSQL or MySQL instance today usually means hand-rolling Django
`DATABASES` and `DATABASE_ROUTERS`. `SNAPADMIN_SHARDING` replaces that with one settings dict, at
two levels of effort — and is **completely inert** until you set `ENABLED: True`: no new
`DATABASES` entry, no extra router, no query overhead for an install that ignores it.

```python
# settings.py — the simple path: a flat list of DSNs, sliced automatically
SNAPADMIN_SHARDING = {
    "ENABLED": True,
    "SHARDING_ENABLED": True,    # partition data across shards
    "MIRRORING_ENABLED": True,   # give each shard read replicas
    "REPLICAS_PER_SHARD": 1,
    "DATABASES": [
        "postgres://user:pass@s1-primary:5432/db", "postgres://user:pass@s1-replica:5432/db",
        "postgres://user:pass@s2-primary:5432/db", "postgres://user:pass@s2-replica:5432/db",
    ],
}
```

Or name every shard's primary/replicas explicitly (`SHARDS = {"shard_1": {"PRIMARY": ..., "REPLICAS": [...]}}`)
for full manual control, with `STRATEGY` picking how a row's shard is resolved — `modulo`, `hash`,
`range`, or your own `CUSTOM_ROUTER_FUNC`.

A model opts in with `shard_key` — a field name, or `True` for the project-wide default — mirroring
exactly how `tenant_scoped` opts a model into multi-tenancy above, so nothing (including Django's
own `auth`/`sessions`/`admin` tables) is ever routed across shards without asking:

```python
class Order(SnapModel):
    shard_key = "customer_id"
```

`snap_master_only()` / `snap_target(shard="shard_2", replica=True)` force routing for one block of
code — both work as a context manager or a decorator, on a plain function or an `async def` one
alike. `manage.py snap_migrate` migrates every shard's **primary** only (sequentially, or all at
once with `--parallel`), and `manage.py snapadmin_db_backup` backs up every shard's primary too —
neither command ever touches a replica. `HA_SETTINGS` controls failover (promote a live replica to
serve writes when the primary is down) and fallback (read from the primary once every replica is
down, or refuse outright if you'd rather protect the primary from that traffic). Full reference:
[Database sharding](https://drofji.github.io/django-snapadmin/#sharding).

## Encrypted model fields

PII masking hides a value at render time and encrypted backups protect the whole artefact. Neither
protects the **column**: a stolen dump, a rogue read-replica or an over-broad `SELECT` still sees
everything. `SnapEncrypted*Field` closes that — **ciphertext at rest, the ordinary Python value in
your code**, and nothing above the field changes:

```python
from snapadmin import fields as snap, models as snap_models

class Patient(snap_models.SnapModel):
    name  = snap.SnapCharField(max_length=200, searchable=True)
    ssn   = snap.SnapEncryptedCharField(max_length=32, show_in_list=False)
    email = snap.SnapEncryptedEmailField(blind_index=True)   # still findable by value

Patient.objects.create(name="A. Wiese", ssn="123-45-6789")
# the ssn column now holds: snap1.2026-09.<nonce>.<ciphertext>
Patient.objects.get(email="a@example.org").ssn      # → "123-45-6789"
```

Eight types — `Char`, `Text`, `Email`, `JSON`, `Integer`, `Decimal`, `Date`, `DateTime` — each its
plain counterpart plus encryption, keeping its own form widget, validation and Python type.
AES-256-GCM behind the optional `[encryption]` extra, a fresh random nonce on every write, and each
value bound to its own `app.model.field` so a ciphertext copied into another column fails to
decrypt instead of quietly relocating a secret. Keys come from a KMS/Vault provider, a mounted
secret, the environment or settings — never `SECRET_KEY`, which a startup check refuses outright —
and rotation is prepending one key: every ciphertext records the id that opens it.

**What it costs, stated up front.** The database cannot compare, order or index a column it cannot
read. `icontains`, `gt`, `startswith` and `ORDER BY` are impossible, and each raises a `FieldError`
naming the field rather than returning an empty queryset — encrypted data silently becoming
invisible data is the failure that matters here. `blind_index=True` buys back `__exact` / `__in`
and `unique=True` through an HMAC sibling column, at the documented cost that equality becomes
observable to anyone who can read that column: fine for an email address, wrong for a national ID.

Encrypted values are excluded from Elasticsearch, redacted in the audit trail, masked by default in
REST/GraphQL/exports/the changelist through the existing PII permission model, and emitted as
ciphertext by `dumpdata`. `manage.py snapadmin_encrypt_fields` adopts an existing plaintext column,
rotates rows onto a new key and rebuilds blind indexes — batched, resumable, and writing nothing
without `--apply`. Full reference:
[Field encryption](https://drofji.github.io/django-snapadmin/#field-encryption).

---

---

# Getting started

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

## Your first model — 3 steps

You add keyword arguments to your fields. They describe how the field should *behave*, and they
**add no database migration**.

**1. Declare the model.**

```python
# models.py
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name      = snap.SnapCharField(max_length=200, searchable=True, show_in_form=True)
    price     = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True, show_in_form=True)
    available = snap.SnapBooleanField(default=True, filterable=True, show_in_form=True)

    api_write_fields = ["name", "price", "available"]   # what an API client may set
```

**2. Turn on the surfaces you want.**

```python
# settings.py
SNAPADMIN_REST_API_ENABLED = True    # off by default — you opt in
SNAPADMIN_GRAPHQL_ENABLED  = True    # same
SNAPADMIN_SWAGGER_ENABLED  = True
```

**3. Register every model — one line.**

```python
# admin.py
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

---

## The cheat sheet — the kwargs you'll actually use

Six keyword arguments cover most of what a new joiner needs on day one. None of them touch the
database, so you can change your mind without a migration.

| Kwarg | Default | What it does |
|---|---|---|
| `show_in_list=True` | `True` | Field appears as a column on the admin list |
| `show_in_form=True` | `False` | Field appears on the add/edit form. **Set it** — an unset model gets an empty form (`snapadmin.W015` warns you at startup) |
| `searchable=True` | `False` | Adds the field to the admin search box, the REST `?search=` filter and the Elasticsearch mapping |
| `filterable=True` | `False` | Adds a sidebar filter in the admin and a `?field=…` query filter in the API |
| `required=True` | `False` | `null=False, blank=False`. The one kwarg that *does* change the column — set it instead of Django's two, so the database and the search index agree |
| `updatable=False` | `True` | Write-once: the value can be set on create but never changed |

On the model itself:

| Attribute | What it does |
|---|---|
| `api_write_fields = [...]` | The allowlist of fields an API client may set |
| `api_exclude_fields = [...]` | Fields that never leave the server, on any surface |
| `data_retention_days = 365` | The GDPR purge deletes rows older than this |
| `es_storage_mode = EsStorageMode.DUAL` | Mirror rows to Elasticsearch |
| `tenant_scoped = True` | Row-level isolation: unreachable without a bound tenant |

More kwargs — `tab` / `row` layout, `autocomplete`, `wysiwyg`, upload validation — in
[the field reference](https://drofji.github.io/django-snapadmin/#snap-fields). There are 30+ field
types, from `SnapCharField` to `SnapPhoneField`, `SnapColorField` and `SnapStatusBadgeField`.

---

## Already have models you cannot rewrite?

Subclassing `SnapModel` is the full route, and it is the natural choice for a new model. If your
model layer already exists — a brownfield schema, a base class from a third-party package, fields
from `django-money` or `phonenumber_field` — opt in from the outside instead. `@snap_model` adds no
field and no attribute, so it needs **no migration**:

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

<details>
<summary>Just one field? Use <code>snap_field()</code></summary>

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
`snapadmin_import`, `snapadmin_health_alert`, `snapadmin_db_backup`, `snapadmin_send_error_digest`,
`snapadmin_purge_expired_data`, `snapadmin_audit_export`, `snapadmin_encryption_key`
(generates a field-encryption key and prints it once) and `snapadmin_encrypt_fields`
(`--adopt` an existing plaintext column, `--rotate` rows onto a new key, `--reindex` blind-index
columns — reports only unless given `--apply`); see
[Field encryption](https://drofji.github.io/django-snapadmin/#field-encryption).

> ⏱ **Nothing runs on a schedule by itself.** SnapAdmin ships no daemon — backups, digests and the
> data purge need a Celery Beat entry or a cron line.
> → [Background tasks & scheduling](https://drofji.github.io/django-snapadmin/#celery)

</details>

---

## Is your integration actually correct?

A runnable checklist — every row names the command that proves it, not just the thing to remember.
`snapadmin-init` prints this itself, with a ✅/❌/⚠️ per row (⚠️ = needs a running project to check,
never a false green):

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

# What you get

**🖥 Admin panel** — list columns, search and filters derived from your fields · themed responsive UI ·
status badges, tabs, inlines, autocomplete · field-level change history · an
[offline mode](https://drofji.github.io/django-snapadmin/#offline) that keeps a list usable with no
connection

**🔌 APIs** — [REST CRUD](https://drofji.github.io/django-snapadmin/#api-rest) with Swagger and
auto-derived filters · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) with
permissions on every traversed relation ·
[API tokens](https://drofji.github.io/django-snapadmin/#api-tokens) hashed at rest · per-model and
[per-field](https://drofji.github.io/django-snapadmin/#field-permissions) guards for what may be
read and written · [`@snap_action`](https://drofji.github.io/django-snapadmin/#snap-action) exposes
a business operation — approve, refund, recalculate — as a permission-checked endpoint, not just CRUD ·
a model rule that rejects a write answers
[`400` naming the field](https://drofji.github.io/django-snapadmin/#api-validation-errors), never an
HTML `500`, and [`api_full_clean`](https://drofji.github.io/django-snapadmin/#api-full-clean) makes
your `Model.clean()` cross-field rules hold for API clients, not just in the admin

**🔍 Search** *(optional)* — [Elasticsearch](https://drofji.github.io/django-snapadmin/#elasticsearch)
with the index mapping derived from your fields · `?search=`
[routed to ES automatically](https://drofji.github.io/django-snapadmin/#es-routing), falling back to
the database when ES is down · [resumable bulk reindex](https://drofji.github.io/django-snapadmin/#bulk-reindex-command) ·
[deletes keep the index in step](https://drofji.github.io/django-snapadmin/#es-delete-sync), including a bulk
`QuerySet.delete()` and rows removed by a cascade

**⚙️ Operations** — [audit trail](https://drofji.github.io/django-snapadmin/#audit-trail) ·
[GDPR retention](https://drofji.github.io/django-snapadmin/#gdpr) ·
[PII masking](https://drofji.github.io/django-snapadmin/#pii-masking) ·
[backups](https://drofji.github.io/django-snapadmin/#backups) ·
[error and health alerts](https://drofji.github.io/django-snapadmin/#alert-channels) to email, Slack,
Discord, Teams or Telegram · [structured logging](https://drofji.github.io/django-snapadmin/#logging) ·
10 languages

**🧭 Operability** — misconfiguration surfaces **at startup** as a Django system check
(`snapadmin.E0xx` / `W0xx`), not as a mystery at request time · `snapadmin-info` reports what is
switched on, what is actually in use, and what is unreachable · `snapadmin-license-check` answers
the legal question in one command

---

# Quality & compatibility

This is a package other people's products depend on, so the test suite is treated as part of the
product rather than as developer hygiene. Concretely, on the current release:

- **4,600+ tests**, run on every push.
- **100% line coverage** on the shipped `snapadmin/` package — 11,000+ statements, no exclusions, no
  `# pragma: no cover` to hide untested code. CI runs
  `pytest --cov=snapadmin --cov-fail-under=100`, so a pull request that adds an untested line fails.
- **The full compatibility matrix on every push** — Python 3.10 / 3.11 / 3.12 / 3.13 × Django 5.2
  and 6.0. A release is gated on the same matrix: the tag-triggered publish workflow runs it before
  anything reaches PyPI.

## Backward compatibility is a test, not a promise

These suites exist for one purpose only: to fail loudly when a public name, signature or default
changes, so a breaking change is a deliberate decision rather than a side effect of a refactor.

| Suite | What it pins |
|---|---|
| `tests/test_public_contract.py` | **230+ checks** over the public API surface — every import path (`from snapadmin.backup import …`), every default, every documented signature |
| `tests/test_public_surface_snapshot.py` | An **AST-derived inventory** of every public class and function actually defined in the package, compared against a frozen snapshot. Unlike a hand-maintained list it is read from the source, not from memory, so a rename or a silent removal cannot slip past it |
| `tests/test_ecosystem_compat.py` | That the Django ecosystem still composes: `django-import-export`, `reversion`, `simple-history` and `guardian` mixins layer onto a generated admin, and auto-registration never clobbers an admin you registered yourself |
| `tests/test_version_sync.py` | That the version is identical in every place the repo publishes it — `pyproject.toml`, the docs site, `SECURITY.md` and the rest |

Removing or renaming a public name is a **major-version-only** change under the
[API-stability policy](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md);
deprecations warn first and name their replacement.

## The documentation is tested too

Docs rot silently: nothing breaks at import time when a section disappears or a setting is never
written down, it just teaches the next reader something false. So the docs are asserted, not
trusted:

| Suite | What it asserts |
|---|---|
| `tests/test_docs_completeness.py` | Every `SNAPADMIN_*` setting the code reads (100+ of them) appears in the docs **and** in the demo project · every registered system-check id is explained somewhere a reader will find it · every optional extra is listed consistently across the README, the docs, `THIRD_PARTY_NOTICES.md` and the licence inventory |
| `tests/test_ai_entry_points.py` | **80+ checks** that the two machine-readable entry points stay true: the module map in the `snapadmin` package docstring (the only docs layer that reaches every `pip install`) names modules that really import, and every docs anchor `llms.txt` links to really exists |
| `tests/test_docs_site.py` | Structural integrity of the docs site — every section has exactly one sidebar link, and every sidebar link points at a section that exists |

## Automated checks on the operator tooling

`snapadmin-info` is the command an operator runs to answer "is this configured correctly?" — so a
false green there is worse than no report at all. **200+ tests** across eleven files cover the
diagnostics package, including **60+ in `tests/test_diagnostics_features.py` alone**, where every
capability probe in the readiness audit is exercised **both switched on and switched off**. A
capability cannot ship without a probe, and a probe cannot ship without both tests.

The same standard applies to the rest of the operator surface: **150+ tests** on the startup system
checks, **60+** on the licence audit and its command, and full suites on the scaffolding
(`snapadmin-new`), the read-only integrator (`snapadmin-init`) and the demo fetcher
(`snapadmin-demo`).

## An end-to-end tripwire

Every layer has its own deep suite — and each can keep passing while the seam *between* layers
quietly breaks. `tests/test_critical_path_smoke.py` walks the whole declarative pipeline in one
test: it posts the real generated admin add form, then asserts the row that comes out is the same
one the REST API serves and the same one the audit trail recorded, with the acting user and a
per-field diff. It is deliberately small — a tripwire for "the pipeline stopped connecting", not a
second copy of the deep suites.

<details>
<summary>Where the rest of the coverage goes</summary>

Beyond the contract and docs suites, the heaviest areas are the ones with the most ways to go
wrong — each figure below is a **floor**, checked against a collection run rather than kept up to
date by arithmetic: field behaviour and encrypted fields (300+), the REST surface (190+), field-level
encryption end to end (370+ across the cipher, the keyset, the blind index, every leak surface and
the conversion command), export (130+), backups (120+) and restore (60+), PII masking (120+),
settings resolution (90+), API tokens (90+ across issuing, hashing and validation),
internationalisation (90+ across the package and the demo), bulk import (70+), alert channels (70+),
the audit trail (60+), offline mode (60+), multi-tenancy (60+ across the model, admin, audit and
Elasticsearch layers) and data retention (50+). Accessibility (WCAG 2.1 AA) and GraphQL permission
enforcement have their own suites.

**`tests/` is not shipped in the wheel or sdist** — only `snapadmin/` (the published package),
`README.md`, `LICENSE` and the docs are. That is a packaging-size choice, not a coverage gap: the
suite runs in CI on every push against the matrix above, so what a release ships is exactly what
that suite already verified, on a clone of this repository — not a second, weaker copy trailing
behind inside every install.

</details>

---

# Install

```bash
pip install django-snapadmin
```

Requires **Python ≥ 3.10** and **Django ≥ 5.2**. Pin an exact version in production.

**Adding it to an existing project?** Run `snapadmin-init`. It inspects your project and prints a
checklist plus the exact snippets to paste. It edits nothing, so there is nothing to undo.

<details>
<summary>Minimal <code>INSTALLED_APPS</code> — the smallest thing that works</summary>

A bare `pip install django-snapadmin` brings Django, structlog and nh3 — nothing else. That is
already enough for the generated admin, because **the REST API and GraphQL are off by default**
(`SNAPADMIN_REST_API_ENABLED` / `SNAPADMIN_GRAPHQL_ENABLED`, both `False` unless you set them).

```python
INSTALLED_APPS = [
    # ── Django itself ───────────────────────────────────────────────────────
    "django.contrib.admin",          # SnapAdmin generates ModelAdmins into this site
    "django.contrib.auth",           # permissions gate both the admin and the API
    "django.contrib.contenttypes",   # required by auth; the audit trail keys off it
    "django.contrib.sessions",       # admin login
    "django.contrib.messages",       # admin "saved successfully" banners
    "django.contrib.staticfiles",    # serves SnapAdmin's CSS/JS

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
    path("api/", include("snapadmin.urls")),   # health check, and any surface you turn on
]
```

That is a working admin-only install.

**Want the REST API and GraphQL?** They need their extras — installing them and listing the apps
without turning the settings on serves nothing, and turning the settings on without the extras is
caught at `manage.py check` (`snapadmin.E010`) rather than at the first request:

```bash
pip install "django-snapadmin[api,graphql]"   # or [api] alone, or [all]
```

```python
INSTALLED_APPS = [
    ...,
    # ── The API stack — [api] + [graphql] extras ────────────────────────────
    "rest_framework",                # the generated REST endpoints are DRF viewsets
    "drf_spectacular",               # builds the OpenAPI schema behind /api/docs/
    "django_filters",                # backs the auto-generated ?field=… query filters
    "graphene_django",               # the generated GraphQL schema
    "snapadmin",
    ...,
]

# settings.py — the surfaces are opt-in
SNAPADMIN_REST_API_ENABLED = True
SNAPADMIN_GRAPHQL_ENABLED = True
SNAPADMIN_SWAGGER_ENABLED = True      # follows the REST setting unless set explicitly
```

One line does the same thing: `SNAPADMIN_PROFILE = "api"` turns REST, GraphQL and Swagger on
together. You still install the extras.

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

    # ── REST API — pip install django-snapadmin[api] ────────────────────────
    # Needed only if you set SNAPADMIN_REST_API_ENABLED/SNAPADMIN_SWAGGER_ENABLED
    # to True — both default to False.
    "rest_framework",
    "drf_spectacular",
    "django_filters",

    # ── GraphQL — pip install django-snapadmin[graphql] ──────────────────────
    # Needed only with SNAPADMIN_GRAPHQL_ENABLED = True (default False). Independent of [api].
    "graphene_django",

    # ── SnapAdmin ───────────────────────────────────────────────────────────
    "snapadmin",

    # ── Background tasks — pip install django-snapadmin[celery] ─────────────
    # Needed for the GDPR purge, async exports, error digests and backups.
    "django_celery_beat",            # edit the schedule from the admin
    "django_celery_results",         # store task results in the database

    # ── Admin-editable settings — pip install django-snapadmin[extra-settings]
    # SnapAdmin does not use it; add it if you want a runtime key/value Setting
    # model in the admin (the demo shows the pattern). If you re-home that admin
    # with EXTRA_SETTINGS_ADMIN_APP, the value is matched against INSTALLED_APPS
    # verbatim: pass the entry ("myapps.shop"), never the app label ("shop") —
    # snapadmin.E025 names the entry to use when the two differ.
    "extra_settings",

    # ── Autocomplete list filters — [autocomplete-filter] (LGPL) ────────────
    # For your own AutocompleteFilter admin filters; SnapAdmin core never imports it.
    "admin_auto_filters",

    "myapp",
]
```

> **Elasticsearch needs no app entry** — `pip install django-snapadmin[elasticsearch]` and set
> `ELASTICSEARCH_ENABLED = True`. The supported server is **Elasticsearch 8.x**, and the extra
> pins the client to match (`>=8,<9`). Same for `[backup]` (SFTP offsite backups) and `[age]`
> (encrypted backups): a dependency, not an app.

</details>

<details>
<summary>Optional extras and their licences</summary>

The base install is self-contained and carries **only permissive licences** (MIT/BSD/Apache), so it
is safe for commercial and proprietary use. Everything with a licence caveat is opt-in:

| Extra | Pulls in | Gives you |
|-------|----------|-----------|
| `api` | `djangorestframework`, `drf-spectacular`, `django-filter` | The REST API + OpenAPI schema/Swagger/ReDoc — needed once you set `SNAPADMIN_REST_API_ENABLED`/`SNAPADMIN_SWAGGER_ENABLED` to `True` (default `False`) |
| `graphql` | `graphene-django` | The generated GraphQL schema — needed once you set `SNAPADMIN_GRAPHQL_ENABLED` to `True` (default `False`), independent of `api` |
| `theme` | `django-unfold` | The themed admin UI (stock Django admin without it) |
| `elasticsearch` | `elasticsearch` (`>=8,<9`) | Full-text search, `DUAL` / `ES_ONLY` models — against an **Elasticsearch 8.x** server |
| `celery` | `celery`, `django-celery-beat`, `django-celery-results` | Background tasks: async export, GDPR purge, digests, backups |
| `backup` | `paramiko` | SFTP offsite database backups |
| `age` | `pyrage` | AGE-encrypted backups (MIT — or skip this extra and use the `age` CLI instead) |
| `s3` | `boto3` | S3-compatible offsite database backups (AWS, MinIO, Backblaze B2, Hetzner Object Storage, Wasabi) |
| `extra-settings` | `django-extra-settings` | An in-admin dynamic key/value `Setting` model |
| `wysiwyg` | `django-ckeditor-5` | Rich-text fields — **bundles CKEditor 5 (GPL-or-commercial)** |
| `autocomplete-filter` | `django-admin-autocomplete-filter` | `AutocompleteFilter` list filters (LGPL) |
| `xlsx` | `openpyxl` | XLSX output for the async export API (MIT — optional for size, not licence) |
| `encryption` | `cryptography` | Field-level encryption — `SnapEncrypted*Field` columns (Apache-2.0/BSD — optional for weight, not licence) |
| `all` | everything above | — |

Run `snapadmin-license-check` after installing to see exactly what you ended up with and whether it
is still proprietary-safe.

→ [Full installation guide](https://drofji.github.io/django-snapadmin/#installation) — compatibility
matrix, extras gotchas, and the MySQL driver licence note.

</details>

---

# Configuration

Every surface is a plain Django setting. Switching one off removes its routes entirely:

```python
SNAPADMIN_REST_API_ENABLED       = True    # REST CRUD endpoints — off by default, opt in explicitly
SNAPADMIN_GRAPHQL_ENABLED        = True    # GraphQL endpoint — same, off by default
SNAPADMIN_SWAGGER_ENABLED        = True    # Swagger UI + ReDoc
SNAPADMIN_URL_PREFIX             = ""      # relocate the whole API surface
SNAPADMIN_CONNECTIVITY_ENABLED   = False   # admin-wide health poll + offline save-guard (opt-in)
```

The first two default to `False` — a plain-admin migration that never asked for an API doesn't get
one by accident. Set them to `True` to serve REST and/or GraphQL.

Don't want to decide all ~110 of them? `SNAPADMIN_PROFILE = "admin"` (or `"api"` / `"full"`) picks
sane defaults for the handful that actually matter — an explicit setting always overrides it.

Misconfiguration shows up **at startup** as a Django system check (`snapadmin.W0xx` / `E0xx`), not
as a mystery at request time.

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

# Documentation

| Topic | |
|-------|--|
| Getting started | [Installation](https://drofji.github.io/django-snapadmin/#installation) · [New project](https://drofji.github.io/django-snapadmin/#scaffold) · [Existing project](https://drofji.github.io/django-snapadmin/#snapadmin-init) · [SnapModel](https://drofji.github.io/django-snapadmin/#snap-model) · [Field types](https://drofji.github.io/django-snapadmin/#snap-fields) · [Admin registration](https://drofji.github.io/django-snapadmin/#admin-registration) |
| APIs | [REST](https://drofji.github.io/django-snapadmin/#api-rest) · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) · [Tokens](https://drofji.github.io/django-snapadmin/#api-tokens) · [Bulk import](https://drofji.github.io/django-snapadmin/#bulk-import) · [Auth / JWT / ETL](https://drofji.github.io/django-snapadmin/#integrating) |
| Search | [Elasticsearch modes](https://drofji.github.io/django-snapadmin/#elasticsearch) · [Query routing](https://drofji.github.io/django-snapadmin/#es-routing) · [Filters](https://drofji.github.io/django-snapadmin/#es-filter) · [Facets](https://drofji.github.io/django-snapadmin/#es-aggregate) · [Deep scan](https://drofji.github.io/django-snapadmin/#es-scan) |
| Operations | [Diagnostics](https://drofji.github.io/django-snapadmin/#snapadmin-info) · [Licence audit](https://drofji.github.io/django-snapadmin/#license-check) · [Celery & scheduling](https://drofji.github.io/django-snapadmin/#celery) · [GDPR](https://drofji.github.io/django-snapadmin/#gdpr) · [Backups](https://drofji.github.io/django-snapadmin/#backups) · [Error monitoring](https://drofji.github.io/django-snapadmin/#error-monitoring) · [Performance](https://drofji.github.io/django-snapadmin/#performance) |
| Reference | [All settings](https://drofji.github.io/django-snapadmin/#env-vars) · [Theming](https://drofji.github.io/django-snapadmin/#theming) · [Enterprise config](https://drofji.github.io/django-snapadmin/#enterprise-config) · [Extending](https://drofji.github.io/django-snapadmin/#extending) · [Migration guides](https://drofji.github.io/django-snapadmin/#migration-guides) |

**Working with an AI assistant?** Two entry points ship for exactly that, and both are pinned by
tests so they cannot drift from the code: the module map in the `snapadmin` package docstring
(`help(snapadmin)` — no network needed) and
[llms.txt](https://drofji.github.io/django-snapadmin/llms.txt).

Upgrading from `drofji-automatically-django-admin`? See the
[migration guide](https://github.com/drofji/django-snapadmin/blob/main/docs/migrations/drofji-automatically-django-admin_to_django-snapadmin.md).

---

# Security

API tokens are hashed at rest, rich-text HTML is sanitized on write, GraphQL enforces permissions on
every traversed relation, and PII masking covers both APIs. Report vulnerabilities privately — see
[SECURITY.md](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md) for the policy, the
supported-versions row and the production-hardening checklist.

# Contributing

See [CONTRIBUTING.md](https://github.com/drofji/django-snapadmin/blob/main/CONTRIBUTING.md). The
suite lives at [`tests/`](https://github.com/drofji/django-snapadmin/tree/main/tests) in the source
repository and must stay green with 100% coverage on `snapadmin/`:

```bash
pytest
```

Regression tests for a specific reported issue live next to the subsystem they cover (e.g.
`tests/test_fields.py` for a field validator, `tests/test_pii_masking.py` for masking) — if you hit
a bug, check there before filing one that might already be covered.

# License

MIT — see [LICENSE](https://github.com/drofji/django-snapadmin/blob/main/LICENSE).
