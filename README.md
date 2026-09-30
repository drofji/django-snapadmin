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

## Try it — 60 seconds, no setup

```bash
pip install "django-snapadmin[api,graphql]"
snapadmin-new myshop
cd myshop
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/admin/>. The admin, the REST API (`/api/docs/`) and GraphQL
(`/api/graphql/`) are already running against a working example model — SQLite, no Docker, nothing
to edit by hand. Only want to look around? `snapadmin-demo` downloads a fully-loaded demo (search,
audit trail, background jobs); log in with `admin` / `admin`.

| You are | Read this |
|---|---|
| **Shipping something today** | [New project: `SnapModel` in 3 steps](#a-new-project-or-an-mvp--snapmodel-in-3-steps) · [Existing project: `@snap_model`](#an-existing-project-you-cannot-rewrite--snap_model) · [Cheat sheet](#the-cheat-sheet--the-kwargs-youll-actually-use) |
| **Deciding whether to adopt it** | [Why this exists](#why-this-exists) → [Proof it holds up](#proof-it-holds-up) → [For teams and enterprise](#for-teams-and-enterprise) |
| **Already using it** | [Changelog](https://github.com/drofji/django-snapadmin/blob/main/CHANGELOG.md) · [Upgrade guides](https://github.com/drofji/django-snapadmin/tree/main/docs/migrations) · [All settings](https://drofji.github.io/django-snapadmin/#env-vars) |

---

# Quickstart

There are two ways in, and they end in the same place. Neither adds a database migration of its own.

## A new project or an MVP — `SnapModel` in 3 steps

The declarative route: your fields say how they behave, and every surface is generated from them.

**1. Declare the model.**

```python
# models.py
from snapadmin import fields as snap, models as snap_models

class Product(snap_models.SnapModel):
    name      = snap.SnapCharField(max_length=200, searchable=True, show_in_form=True)
    price     = snap.SnapDecimalField(max_digits=10, decimal_places=2, filterable=True, show_in_form=True)
    available = snap.SnapBooleanField(default=True, filterable=True, show_in_form=True)

    api_write_fields = ["name", "price", "available"]   # what an API client may set
    subject_path = None   # no personal data here — snapadmin.E011 asks every model
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
| `/admin/` | List with a search box on `name` (and the id), sidebar filters on `price` and `available`, add/edit forms, change history |
| `/api/models/shop/Product/` | REST create · read · update · delete, with filters, pagination and token auth |
| `/api/docs/` | Swagger UI + ReDoc |
| `/api/graphql/` | GraphQL schema, permission-checked |

`snapadmin-new` already wired `INSTALLED_APPS` and `urls.py` for you. By hand, it is
[the minimal install](https://drofji.github.io/django-snapadmin/#installed-apps-minimal) plus the
four API apps; `subject_path` is the GDPR subject-access declaration every model answers
([what to put there](https://drofji.github.io/django-snapadmin/#gdpr-subject-request)).

## An existing project you cannot rewrite — `@snap_model`

A brownfield schema, a base class from a third-party package, fields from `django-money` or
`phonenumber_field`: opt the model in from the outside. No inheritance change, and — because the
decorator adds no field and no attribute — **no migration**.

**1. Install, and let `snapadmin-init` tell you what is missing.** It reads your project and prints
the exact snippets to paste; it never edits a file.

```bash
pip install "django-snapadmin[api]"
snapadmin-init --api
```

**2. Add the apps and the routes it names.**

```python
# settings.py
INSTALLED_APPS += ["rest_framework", "drf_spectacular", "django_filters", "snapadmin"]
SNAPADMIN_REST_API_ENABLED = True

# urls.py  (from django.urls import include, path)
urlpatterns += [path("api/", include("snapadmin.urls"))]
```

**3. Decorate the model.**

```python
from django.db import models
from snapadmin import snap_model

@snap_model(
    api_write_fields=["name", "price"],   # what an API client may set
    api_exclude_fields=["cost_price"],    # never leaves the server
    search_fields=["name"],               # what ?search= matches on
    subject_path=None,                    # no personal data here — snapadmin.E011 asks every model
)
class Product(models.Model):
    name       = models.CharField(max_length=200)
    price      = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
```

You get the REST API (`/api/models/<app_label>/Product/`, documented at `/api/docs/`), GraphQL
with the `[graphql]` extra, the offline endpoints, the system checks and the `snapadmin-info`
inventory. You keep **your own** `ModelAdmin` — the parts that need `SnapModel`'s machinery
(Elasticsearch mirroring, the retention purge, the generated admin) skip a decorated model rather
than half-work — so the decorator reads `search_fields` from its own keyword, not from field
flags. When you later move a model onto `SnapModel`, `snap_field()` gives a field you already have
(a `django-money` or `phonenumber_field` column) the Snap flags without changing its class, and
bare, wrapped and `Snap*Field` fields mix freely in one class body.
[Which capability lives where, side by side](https://drofji.github.io/django-snapadmin/#two-ways) ·
[`snap_field()`](https://drofji.github.io/django-snapadmin/#snap-field-wrapper) ·
[mixing fields](https://drofji.github.io/django-snapadmin/#mixing-fields).

## The cheat sheet — the kwargs you'll actually use

None of these touch the database except `required`, so you can change your mind without a migration.

| Kwarg | Default | What it does |
|---|---|---|
| `show_in_list=True` | `True` | Field appears as a column on the admin list |
| `show_in_form=True` | `False` | Field appears on the add/edit form. **Set it** — an unset model gets an empty form (`snapadmin.W015` warns you at startup) |
| `searchable=True` | `False` | Adds the field to the admin search box and the REST `?search=` filter. It does *not* build the Elasticsearch mapping — that is `es_mapping` / `es_auto_mapping` on the model |
| `filterable=True` | `False` | Adds a sidebar filter in the admin and a `?field=…` query filter in the API |
| `required=True` | `False` | `null=False, blank=False`. The one kwarg that *does* change the column — set it instead of Django's two, so the database and the search index agree |
| `updatable=False` | `True` | Write-once: the value can be set on create but never changed |

On the model itself:

| Attribute | What it does |
|---|---|
| `api_write_fields = [...]` | The allowlist of fields an API client may set |
| `api_exclude_fields = [...]` | Fields that never leave the server, on any surface |
| `data_retention_days = 365` | The GDPR purge deletes rows older than this — or `data_retention_date_field = "delete_at"` for a per-row deadline |
| `es_storage_mode = EsStorageMode.DUAL` | Mirror rows to Elasticsearch. Pair it with `es_mapping` or `es_auto_mapping = True` — a mirror with neither would index ids only, so `snapadmin.E026` refuses it |
| `tenant_scoped = True` | Row-level isolation: unreachable without a bound tenant |

30+ field types (`SnapPhoneField`, `SnapColorField`, `SnapStatusBadgeField`, …) and the layout kwargs
(`tab`, `row`, `autocomplete`, `wysiwyg`, upload validation) are in
[the field reference](https://drofji.github.io/django-snapadmin/#snap-fields).

## The commands

| Command | Use it when |
|---|---|
| `snapadmin-new myshop` | **Starting a new project.** `--full` adds Docker, PostgreSQL, Redis, Elasticsearch; `--admin-only` leaves out the APIs |
| `snapadmin-demo` | **You want to see it first.** Downloads and serves a throwaway demo |
| `snapadmin-init` | **Adding it to a project you already have.** Read-only — prints what is missing and the exact code to paste |
| `snapadmin-info` | **Is everything configured and healthy?** `--section features` for the ✓/✗ checklist, `--health-check` exits non-zero for monitoring, `--json` for CI |
| `snapadmin-license-check` | **Can we ship this commercially?** Every dependency's licence, with a verdict; `--critical-only` for the blockers |

`snapadmin-info` ≡ `python manage.py snapadmin_info`, and both inspect a live project. You also see
the short version without asking: under `DEBUG`, `runserver` prints one block to stderr saying which
capabilities are on, which are off, which lack their extra, and whether your dependencies allow
commercial use — configuration only, never a query or a secret. `SNAPADMIN_STARTUP_REPORT = False`
silences it ([startup report](https://drofji.github.io/django-snapadmin/#startup-report)).

> ⏱ **Nothing runs on a schedule by itself.** Backups, digests and the data purge are management
> commands (`snapadmin_db_backup`, `snapadmin_purge_expired_data`, `snapadmin_send_error_digest`, …)
> that need a Celery Beat entry or a cron line.
> → [Background tasks & scheduling](https://drofji.github.io/django-snapadmin/#celery)

## Is your integration actually correct?

`snapadmin-init` prints this checklist itself, with a ✅/❌/⚠️ per row (⚠️ = needs a running project
to check, never a false green):

| Check | Verify |
|---|---|
| App boots, models registered | `manage.py check` · `snapadmin_info --section inventory` |
| Migrations applied | `manage.py migrate --check` |
| Auth on the API, PII masked where it matters | `snapadmin_info --section features` |
| Backups on, **2+ destinations**, encrypted | `snapadmin_info --section features` · `manage.py check` warns (`snapadmin.W021`) when an off-host destination has no `SNAPADMIN_BACKUP_AGE_RECIPIENTS` |
| Have you actually run a restore? | `snapadmin_restore <bundle> --database <drill-alias> --confirm` restores into a throwaway database — an untested backup is the most common form of not having one |

→ [Full checklist](https://drofji.github.io/django-snapadmin/#integration-checklist) ·
[Production playbook](https://drofji.github.io/django-snapadmin/#playbook) — the decisions to settle
before the first migration, the build order, what your own test suite should cover, and which
defaults stop being right under load.

---

# Why this exists

Every internal tool needs the same four things: a screen where staff manage the data, an API for the
mobile app, an API for the frontend team, and a search box. Today each one is written and maintained
separately — **four descriptions of the same data**, four places to update when a field changes, four
chances to leak a field you meant to keep private. SnapAdmin generates all four from **one**.

|  | Without | With SnapAdmin |
|---|---|---|
| Admin panel | you write it | generated |
| REST API + Swagger docs | you write it | generated |
| GraphQL | you write it | generated |
| Search index | you write it | generated |
| Audit trail, GDPR retention, PII masking, backups | you write it (or you don't) | built in |
| Field defined in | 4 places | **1 place** |

The admin screen your ops team asks for on Friday is a keyword argument, not a sprint. A new field
reaches the API, the search index and the audit log the moment it reaches the model, and the fields
you *don't* want exposed are excluded once, in the model, rather than in every surface.

**What it is not.** Not a theme, not a framework, not a lock-in. Underneath it is ordinary Django —
models, `ModelAdmin`, DRF viewsets — so you can override any generated piece, or stop using it,
without rewriting your data layer. Your hand-written `ModelAdmin` classes are never replaced, and
`manage.py check` tells you when one of them shows masked fields unmasked (`snapadmin.W025`).
Unfold, Jazzmin and Grappelli *restyle* an admin you still write; SnapAdmin *generates* it — and uses
Unfold as its optional theme. If a better-looking admin is all you need, a theme is less machinery
([the comparison](https://drofji.github.io/django-snapadmin/#vs-themes)).

# What you get

**🖥 Admin panel** — list columns, search and filters derived from your fields · themed responsive UI ·
status badges, tabs, inlines, autocomplete · field-level change history · an
[offline mode](https://drofji.github.io/django-snapadmin/#offline) that keeps a list usable with no
connection

**🔌 APIs** — [REST CRUD](https://drofji.github.io/django-snapadmin/#api-rest) with Swagger and
auto-derived filters · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) with
permissions on every traversed relation ·
[API tokens](https://drofji.github.io/django-snapadmin/#api-tokens) hashed at rest ·
[per-field](https://drofji.github.io/django-snapadmin/#field-permissions) read/write guards ·
[`@snap_action`](https://drofji.github.io/django-snapadmin/#snap-action) exposes approve / refund /
recalculate as a permission-checked endpoint · a rejected write answers
[`400` naming the field](https://drofji.github.io/django-snapadmin/#api-validation-errors), and
[`api_full_clean`](https://drofji.github.io/django-snapadmin/#api-full-clean) runs your
`Model.clean()` for API clients too

**🔍 Search** *(optional)* — [Elasticsearch](https://drofji.github.io/django-snapadmin/#elasticsearch)
with the mapping derived from your fields · `?search=`
[routed to ES automatically](https://drofji.github.io/django-snapadmin/#es-routing), falling back to
the database when ES is down · [resumable bulk reindex](https://drofji.github.io/django-snapadmin/#bulk-reindex-command) ·
[deletes keep the index in step](https://drofji.github.io/django-snapadmin/#es-delete-sync), cascades
included

**⚙️ Operations** — [audit trail](https://drofji.github.io/django-snapadmin/#audit-trail) ·
[GDPR retention](https://drofji.github.io/django-snapadmin/#gdpr) ·
[PII masking](https://drofji.github.io/django-snapadmin/#pii-masking) ·
[backups](https://drofji.github.io/django-snapadmin/#backups) ·
[alerts](https://drofji.github.io/django-snapadmin/#alert-channels) to email, Slack, Discord, Teams
or Telegram · [structured logging](https://drofji.github.io/django-snapadmin/#logging) · 10 languages

**🔐 Encrypted model fields** — `SnapEncrypted*Field` (eight types, behind the `[encryption]` extra)
stores AES-256-GCM ciphertext in the column and hands your code the ordinary value; keys come from a
KMS/Vault provider, a mounted secret, the environment or settings — never `SECRET_KEY` — and rotate by
prepending one. **The cost is stated up front:** the database cannot compare, order or index what it
cannot read, so `icontains`, `gt` and `ORDER BY` raise a `FieldError` naming the field rather than
returning nothing, and `blind_index=True` buys back `__exact` / `__in` / `unique` at the documented
price that equality becomes observable. [Field encryption](https://drofji.github.io/django-snapadmin/#field-encryption)

**🗄 Sharding and replica routing** — `SNAPADMIN_SHARDING` replaces hand-rolled `DATABASES` /
`DATABASE_ROUTERS` with one settings dict: a flat list of DSNs or named shards, `modulo` / `hash` /
`range` / custom strategies, opt-in per model with `shard_key`, failover in `HA_SETTINGS`, and
`snap_migrate` / `snapadmin_db_backup` that only ever touch primaries. Completely inert until
`ENABLED: True`. [Database sharding](https://drofji.github.io/django-snapadmin/#sharding)

**🧭 Operability** — misconfiguration surfaces **at startup** as a Django system check
(`snapadmin.E0xx` / `W0xx`), not as a mystery at request time · `snapadmin-info` reports what is
switched on, what is in use and what is unreachable · `snapadmin-license-check` answers the legal
question in one command

---

## Proof it holds up

Adoption risk is the real question, so every answer below is something you can verify yourself.

| Question | Evidence |
|---|---|
| **Is it tested?** | **5,000+ tests** across **153 files** and **100% line and branch coverage** on the shipped package (11,000+ statements), enforced in CI — the build fails below 100% |
| **On our Python and Django?** | Every push runs the full matrix: **Python 3.10–3.13 × Django 5.2 / 6.0** |
| **Against a real database, or only SQLite?** | A separate CI job runs the **whole suite against PostgreSQL 16**, and a marker-gated suite against a **live Elasticsearch 8.13.0** — the same image the demo ships |
| **Do the tests lean on each other?** | Every run is in **random order** (`pytest-randomly`), so a test that depends on another having run first fails instead of passing quietly |
| **Will an upgrade break us?** | **340+ tests exist only to fail** if a public name, signature or default changes, under a written [API-stability policy](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md): deprecations warn before removal and name their replacement |
| **Are the docs actually true?** | **140+ tests** assert that the README, the docs site and the in-package module map describe the code that really ships |
| **Does the whole pipeline still connect?** | An end-to-end smoke test posts the real admin form, then proves the REST API serves that same row and the audit trail recorded it |
| **Is `snapadmin-info` telling the truth?** | **220+ tests** cover the diagnostics report; every capability probe is tested both switched **on and off**, so the readiness audit cannot report a false green |
| **Can we ship it commercially?** | MIT. The base install carries **only** permissive licences (MIT/BSD/Apache); anything copyleft is an opt-in extra. `snapadmin-license-check` audits what you actually installed ([THIRD_PARTY_NOTICES.md](https://github.com/drofji/django-snapadmin/blob/main/THIRD_PARTY_NOTICES.md)) |

## For teams and enterprise

| Question | Answer |
|---|---|
| **Who changed that record?** | An [immutable audit trail](https://drofji.github.io/django-snapadmin/#audit-trail) with per-field `old → new` diffs and a per-object timeline |
| **GDPR / data retention?** | `data_retention_days` per model, or [`data_retention_date_field`](https://drofji.github.io/django-snapadmin/#retention-per-row) for a per-row expiry; the same purge covers the audit log and, if you opt in, finished export and reindex job files. [Every purge](https://drofji.github.io/django-snapadmin/#retention-table) |
| **A subject requests everything about them?** | [`snapadmin_subject_request export\|delete`](https://drofji.github.io/django-snapadmin/#gdpr-subject-request) walks every model's declared `subject_path` — deletion is dry-run by default and refuses up front if a protected relation would block it |
| **Personal data in the API?** | [PII masking](https://drofji.github.io/django-snapadmin/#pii-masking) — declared once, masked in the admin, REST, GraphQL, exports **and** the audit trail |
| **Only HR should see salary?** | [`api_field_permissions`](https://drofji.github.io/django-snapadmin/#field-permissions) — the field is absent for anyone lacking the permission, and a denied write answers `400` naming it |
| **Multi-tenant SaaS?** | [Row-level tenant isolation](https://drofji.github.io/django-snapadmin/#multi-tenancy) with `tenant_scoped = True`: every generated surface is default-deny without a bound tenant. Logical isolation, not physical — and documented as plainly as the feature |
| **Will it survive our load?** | Read-replica routing, estimated counts, paging caps, streaming exports and a [quota primitive](https://drofji.github.io/django-snapadmin/#quotas). There are **no published benchmark numbers**; `seed_large` + `benchmark_list_view` in the demo measure it on your hardware ([performance](https://drofji.github.io/django-snapadmin/#performance)) |
| **Single sign-on?** | [SSO / OAuth2 login helper](https://drofji.github.io/django-snapadmin/#enterprise-config); auth is pluggable — JWT, session, or your own |
| **How do we know it is up?** | Health probes, error-spike alerts and daily digests; `snapadmin-info --health-check` exits non-zero for your monitoring |
| **Backups?** | [3-2-1 backups](https://drofji.github.io/django-snapadmin/#backups) — local, network share, offsite over FTPS / SFTP / S3-compatible, optionally **AGE-encrypted** in-stream, with media and `.env` in a checksummed bundle, a dry-run-by-default [restore](https://drofji.github.io/django-snapadmin/#restore) and a [rollback](https://drofji.github.io/django-snapadmin/#restore-rollback) |
| **Are we locked in?** | No. Ordinary Django underneath, and your models need not even inherit from ours: [`@snap_model`](https://drofji.github.io/django-snapadmin/#snap-model-decorator) opts a plain `models.Model` in from the outside |
| **What is coming next?** | Whatever lands follows the rule everything here follows: **additive, opt-in and inert until configured** — no new required setting, no migration from the package itself |

---

# Quality & compatibility

This is a package other people's products depend on, so the test suite is treated as part of the
product. Every count is a **floor**, read off a real `pytest --collect-only -q` run rather than kept
current by arithmetic. The full account — every layer, how each check works, where the tests go —
is [Testing & Quality Engineering](https://drofji.github.io/django-snapadmin/#testing).

- **5,000+ tests across 153 files**, in random order on every run. Twelve need a live Elasticsearch
  and are deselected by default, so cloning the repository and typing `pytest` starts no container
  and takes about forty seconds.
- **100% line and branch coverage** of the shipped `snapadmin/` package, gated in CI
  (`pytest --cov=snapadmin --cov-branch --cov-fail-under=100`) — never closed with
  `# pragma: no branch`. **Seventeen lines across twelve modules carry a `# pragma: no cover`**, each
  with a written reason (abstract methods, `if TYPE_CHECKING:` blocks, optional-import branches);
  that list is pinned by a test so it cannot quietly grow.
- **Every push runs, in CI**: the six-way Python × Django matrix; the whole suite on
  **PostgreSQL 16** plus the live **Elasticsearch 8.13.0** tests; Ruff + mypy, clean and blocking;
  the generated admin driven in a **real browser** under both admin themes; and `lowest-deps`, which
  installs every dependency at its declared minimum. A release is gated on the same jobs.
- **Property-based and fuzz tests** (`hypothesis`, 100 examples per test locally, 300 in CI) state
  laws that must hold for every generated input — `deconstruct()` round-trips, the encryption
  envelope, masking rules — and throw hostile input at the request-facing surfaces.
- **Mutation testing** (`mutmut`, `scripts/mutation.py`) proves the tests can *detect* a wrong
  change, not merely run the line — per push over the functions the push changed, weekly over the
  modules where a wrong answer costs most. Advisory: it reports, never blocks.
- **Every security fix ever shipped keeps a named test** (30+, `pytest -m security_regression`), and
  a new security note without one fails the build.
- **Ten translation catalogs, linted mechanically** (placeholders, plurals, markup, a `.mo` matching
  its `.po`) by `tests/test_translation_lint.py`. Only `en` and `ru` are native-reviewed; the other
  eight [await a native speaker](https://drofji.github.io/django-snapadmin/#i18n-review).

**How the tests are written.** Test-first, with a regression test pinned to the exact input for
every bug fix. Assertions state a contract — the value, the status code, the query count — and a
guard (`tests/test_assertions_can_fail.py`) fails on an assertion no outcome could falsify. No test
is ever weakened, skipped or mocked into silence to get green. No test assumes a backend: where
SQLite and PostgreSQL genuinely differ, both halves are written.

**The public surface and the docs are tests too.**

| Suite | What it pins |
|---|---|
| `tests/test_public_contract.py` | **260+ checks** over every import path, default and documented signature — part of the **340+** contract tests that can only fail when the public surface changes |
| `tests/test_public_surface_snapshot.py` | An AST-derived inventory of every public class and function, diffed against a frozen snapshot — a rename or silent removal cannot slip past it |
| `tests/test_ai_entry_points.py` | **90+ checks** that the in-package module map imports and every `llms.txt` anchor exists |
| `tests/test_docs_completeness.py` | Every `SNAPADMIN_*` setting is documented and in the demo; every check id is explained; every extra is listed everywhere |
| `tests/test_critical_path_smoke.py` | One walk across the seams: admin form POST → database row → audit entry → REST read |

**Run every gate yourself** — the same checks CI runs, one command:

```bash
python scripts/gates.py            # everything this machine can run; a gate it cannot is "not run", never "passed"
python scripts/gates.py --release  # every gate must run, and pass
pytest -m e2e                                    # the browser suite, Unfold admin
SNAPADMIN_TEST_ADMIN_THEME=stock pytest -m e2e   # the browser suite, Django's stock admin
```

The browser scenarios live in `tests/e2e/test_admin_flows.py`, `tests/e2e/test_admin_forms.py` and
`tests/e2e/test_connectivity.py`; every page also fails on an uncaught JavaScript error, a missing
asset or a 5xx. Their first run found defects no test-client test could see — a chart not drawn,
relation fields missing from forms — each now pinned by a fast test as well.

## What is not in place yet

A quality section that only lists what exists is marketing. None of these is claimed above:

| Missing | Status |
|---|---|
| **Load testing** — throughput and latency under concurrent users. A library has no traffic of its own; this belongs in a project built on it | Not in this repository. No Locust, no k6 |
| **Firefox and WebKit in CI** — the same scenarios run on them locally (`SNAPADMIN_E2E_BROWSER=firefox`) | Chromium only in CI |

---

# Install and configure

```bash
pip install django-snapadmin
```

Requires **Python ≥ 3.10** and **Django ≥ 5.2**; pin an exact version in production. A bare install
brings Django, structlog and nh3 — enough for the generated admin, because **the REST API and
GraphQL are off by default**. Everything else is an extra, and the base install carries only
permissive licences:

`api` · `graphql` · `theme` (Unfold) · `elasticsearch` (8.x) · `celery` · `backup` (SFTP) · `s3` ·
`age` (encrypted backups) · `encryption` (encrypted fields) · `xlsx` · `extra-settings` ·
`autocomplete-filter` (LGPL) · `wysiwyg` (**bundles CKEditor 5, GPL-or-commercial**) · `all`

`snapadmin-license-check` tells you what you ended up with.
→ [Installation](https://drofji.github.io/django-snapadmin/#installation) — the minimal and the full
`INSTALLED_APPS`, what each [extra](https://drofji.github.io/django-snapadmin/#extras) pulls in, and
the MySQL driver licence note.

Every surface is a plain Django setting, and switching one off removes its routes entirely:

```python
SNAPADMIN_REST_API_ENABLED       = True    # REST CRUD endpoints — off by default
SNAPADMIN_GRAPHQL_ENABLED        = True    # GraphQL endpoint — off by default
SNAPADMIN_SWAGGER_ENABLED        = True    # Swagger UI + ReDoc
SNAPADMIN_URL_PREFIX             = ""      # relocate the whole API surface
SNAPADMIN_CONNECTIVITY_ENABLED   = False   # admin-wide health poll + offline save-guard (opt-in)
```

Don't want to decide all ~110 of them? `SNAPADMIN_PROFILE = "admin"` (or `"api"` / `"full"`) sets
the handful that matter; an explicit setting always wins. Snap fields accept Django's positional
label (`SnapCharField("Label", max_length=200)`), so existing declarations switch over unchanged, and
`snapadmin.E027` stops `SnapModel` from silently replacing an `objects` manager your mixins provide.

→ [Every setting](https://drofji.github.io/django-snapadmin/#env-vars) ·
[Profiles](https://drofji.github.io/django-snapadmin/#profiles) ·
[Extending & overriding](https://drofji.github.io/django-snapadmin/#extending) — your own field
types, endpoints, auth and templates, without forking ·
[The full demo from a clone](https://drofji.github.io/django-snapadmin/#demo-setup) — Docker with
PostgreSQL, Redis and Elasticsearch

---

# Documentation

| Topic | |
|-------|--|
| Getting started | [Installation](https://drofji.github.io/django-snapadmin/#installation) · [New project](https://drofji.github.io/django-snapadmin/#scaffold) · [Existing project](https://drofji.github.io/django-snapadmin/#snapadmin-init) · [SnapModel](https://drofji.github.io/django-snapadmin/#snap-model) · [Field types](https://drofji.github.io/django-snapadmin/#snap-fields) · [Admin registration](https://drofji.github.io/django-snapadmin/#admin-registration) |
| APIs | [REST](https://drofji.github.io/django-snapadmin/#api-rest) · [GraphQL](https://drofji.github.io/django-snapadmin/#api-graphql) · [Tokens](https://drofji.github.io/django-snapadmin/#api-tokens) · [Bulk import](https://drofji.github.io/django-snapadmin/#bulk-import) · [Auth / JWT / ETL](https://drofji.github.io/django-snapadmin/#integrating) |
| Search | [Elasticsearch modes](https://drofji.github.io/django-snapadmin/#elasticsearch) · [Query routing](https://drofji.github.io/django-snapadmin/#es-routing) · [Filters](https://drofji.github.io/django-snapadmin/#es-filter) · [Facets](https://drofji.github.io/django-snapadmin/#es-aggregate) · [Deep scan](https://drofji.github.io/django-snapadmin/#es-scan) |
| Operations | [Diagnostics](https://drofji.github.io/django-snapadmin/#snapadmin-info) · [Startup report](https://drofji.github.io/django-snapadmin/#startup-report) · [Licence audit](https://drofji.github.io/django-snapadmin/#license-check) · [Celery & scheduling](https://drofji.github.io/django-snapadmin/#celery) · [GDPR](https://drofji.github.io/django-snapadmin/#gdpr) · [Backups](https://drofji.github.io/django-snapadmin/#backups) · [Error monitoring](https://drofji.github.io/django-snapadmin/#error-monitoring) · [Performance](https://drofji.github.io/django-snapadmin/#performance) |
| Reference | [All settings](https://drofji.github.io/django-snapadmin/#env-vars) · [Theming](https://drofji.github.io/django-snapadmin/#theming) · [Enterprise config](https://drofji.github.io/django-snapadmin/#enterprise-config) · [Extending](https://drofji.github.io/django-snapadmin/#extending) · [Testing](https://drofji.github.io/django-snapadmin/#testing) · [Migration guides](https://drofji.github.io/django-snapadmin/#migration-guides) |

**Working with an AI assistant?** Two entry points ship for exactly that, both pinned by tests: the
module map in the `snapadmin` package docstring (`help(snapadmin)` — no network needed) and
[llms.txt](https://drofji.github.io/django-snapadmin/llms.txt).

# Security

API tokens are hashed at rest, rich-text HTML is sanitized on write, GraphQL enforces permissions on
every traversed relation, and PII masking covers both APIs. Report vulnerabilities privately — see
[SECURITY.md](https://github.com/drofji/django-snapadmin/blob/main/SECURITY.md) for the policy, the
supported-versions row and the production-hardening checklist.

# Contributing

See [CONTRIBUTING.md](https://github.com/drofji/django-snapadmin/blob/main/CONTRIBUTING.md). The
suite lives at [`tests/`](https://github.com/drofji/django-snapadmin/tree/main/tests) and must stay
green with 100% line and branch coverage on `snapadmin/`; `pytest` runs it. The sdist carries the
suite too, so an installed version can be held to the same checks without cloning. Regression tests
live next to the subsystem they cover (`tests/test_fields.py`, `tests/test_pii_masking.py`, …) —
check there before filing a bug that might already be covered.

# License

MIT — see [LICENSE](https://github.com/drofji/django-snapadmin/blob/main/LICENSE).
