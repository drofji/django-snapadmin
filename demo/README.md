# SnapAdmin Demo

A runnable, self-contained Django project that showcases
[django-snapadmin](https://pypi.org/project/django-snapadmin/). It is **not**
published to PyPI — the package itself lives in [`../snapadmin/`](../snapadmin/).
This project exists so you can evaluate SnapAdmin against realistic models and
develop against the package in editable mode.

> **Just want to run it, without cloning?** `pip install django-snapadmin && snapadmin-demo`
> downloads this directory from the matching release tag and runs it for you. The instructions below
> are for developing against the demo from a clone.

## Layout

```
demo/
  core/          Django project config — the "site"
    settings.py        env-var-driven settings (safe local-dev defaults)
    settings_test.py   pytest overrides (SQLite, ES off, Celery eager)
    urls.py, wsgi.py, asgi.py, celery.py
  app/           the example Django app (app_label "demo")
    models.py          SnapModel example domain (products, customers, orders, …)
    admin.py, views.py, search.py, tasks.py
    management/commands/  seed_demo, seed_large, benchmark_list_view, sync_exchange_rates
    migrations/, templates/
  templates/     project-level template overrides (Unfold admin index)
  manage.py      run from the repo root: `python demo/manage.py <command>`
  requirements.txt   demo/dev dependencies (the package's own deps live in ../pyproject.toml)
  dist.env       annotated template — copy to demo/.env and fill in
```

Additional demo apps can be added alongside `app/` under `demo/` later without
touching `core/`.

## Quick start (no Docker)

Run every command **from the repo root** — `demo/manage.py` puts the repo root
on `sys.path` itself, so both the `demo` project and the `snapadmin` package
import cleanly regardless of your working directory.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r demo/requirements.txt
pip install -e .                 # snapadmin in editable mode

python demo/manage.py migrate
python demo/manage.py seed_demo  # populate example data
python demo/manage.py runserver
```

Then open <http://127.0.0.1:8000/admin/> (create a superuser first with
`python demo/manage.py createsuperuser`).

## Quick start (Docker)

```bash
cp demo/dist.env demo/.env       # then edit demo/.env
docker compose up --build        # db + redis + app + celery worker + beat
```

Add Elasticsearch with `docker compose --profile es up --build`. See the
top-of-file comments in [`../docker-compose.yml`](../docker-compose.yml) for the
full profile matrix and the Traefik overlays.

**Self-healing.** `restart: unless-stopped` only restarts a container that *exits* —
a process that hangs while its healthcheck reports `unhealthy` is never restarted on
its own. The stack includes a [`willfarrell/autoheal`](https://github.com/willfarrell/docker-autoheal)
sidecar (MIT, demo-only) that watches every container labelled `autoheal=true`
(db, redis, app, worker, elasticsearch) and restarts it when its healthcheck goes
unhealthy. Paired with the `send-health-alert` Beat entry — which emails when a
subsystem probe (DB / Elasticsearch / REST API / GraphQL) is down — a hung subsystem
is both restarted and reported. Set `SNAPADMIN_HEALTH_ALERT_EMAILS` in `demo/.env` to
receive the alert.

With Elasticsearch running, the demo's `Product` (DUAL) and `SearchLog` (ES_ONLY)
models exercise the resumable bulk reindex — run it against the seeded data to
watch live progress, then try the flags:

```bash
python demo/manage.py snapadmin_reindex --model demo.Product --tune
python demo/manage.py snapadmin_reindex --model demo.Product --limit 100  # probe run
python demo/manage.py snapadmin_reindex --parallel 4        # fan out with parallel_bulk
python demo/manage.py snapadmin_reindex --resume            # continue a crashed run
```

Each run is tracked on a `SnapReindexJob` row (progress, resume cursor, cancel).
It fetches only the ES-mapped columns (`Product` maps `name`/`price`/`available`,
so its large `description` body is skipped); `--limit N` bounds a probe/canary run;
and `--tune` defaults to `SNAPADMIN_REINDEX_TUNE_DEFAULT` (use `--no-tune` to override).

Run `python demo/manage.py snapadmin_info` against this project to see the diagnostics
report — version, the database/Elasticsearch/Celery status, and the demo's registered
models with their storage modes — or `snapadmin_info --health-check` for a probe-only
readiness check that exits non-zero when a service is down. `snapadmin_info --section
features` prints a ✓/✗ checklist of which commerce-important capabilities (backups,
retention-based deletion, PII masking, read-only models, SSO, …) are actually on or in
use in the project; add `--verbose` for a per-capability count.

## Configuration

All settings are environment-variable driven with safe local-development
defaults — nothing sensitive is committed. `demo/.env` is git-ignored; copy the
annotated [`dist.env`](dist.env) template to create it. Notably:

- `SECRET_KEY` / `DEBUG` / `ALLOWED_HOSTS` — booting with `DEBUG=False` and a
  placeholder `SECRET_KEY` is refused outright (see `core/settings.py`).
- Every optional SnapAdmin surface is toggle-able from the environment:
  `SNAPADMIN_REST_API_ENABLED`, `SNAPADMIN_GRAPHQL_ENABLED`,
  `SNAPADMIN_SWAGGER_ENABLED`, `SNAPADMIN_USER_API_ENABLED`,
  `SNAPADMIN_AUDIT_LOG_ENABLED`, and more — disable one and its routes/behavior
  disappear.

### Runtime-editable settings (DB-backed, via `django-extra-settings`)

The demo also shows the `[extra-settings]` extra storing configuration in the
**database** instead of only in `settings.py`. A curated set of *runtime-editable*
`SNAPADMIN_*` settings — the ones the package re-reads per request:
`SNAPADMIN_MASKED_FIELDS`, `SNAPADMIN_AUDIT_LOG_ENABLED`, the audit / error
retention days, `SNAPADMIN_ES_SEARCH_LIMIT` and `SNAPADMIN_DASHBOARD_PUBLIC` —
appears under **Settings** in the admin (already Unfold-styled), each with a description.
Edit one there and it takes effect immediately, no restart: e.g. set
`SNAPADMIN_MASKED_FIELDS` to `{"demo.Customer": ["email"]}` and the Customer API
starts masking emails on the next request.

How it works (and why it's demo-only) lives in
[`apps/shop/managed_settings.py`](apps/shop/managed_settings.py): the `snapadmin`
package never depends on `django-extra-settings`, so the demo *syncs* each DB value
back onto `django.conf.settings`, leaving the package to read its config exactly as it
always does. Settings that are read once at boot (URL-routing toggles, admin
nesting) are intentionally **not** surfaced — editing them at runtime wouldn't
take effect — and no secret or credential is ever exposed this way.

**Capacity and abuse-protection knobs stay out of the admin on purpose.**
`SNAPADMIN_API_PAGE_SIZE`, `SNAPADMIN_API_MAX_PAGE_SIZE`, the two throttle rates and
the two export ceilings are configured only in [`dist.env`](dist.env) /
[`core/settings.py`](core/settings.py). They bound per-request cost and caller rate,
so they belong to the deployment: surfacing them as admin-editable rows would let
anyone holding the Setting change-permission relax the API's own rate limits and
export ceilings from a web form, with no deploy trail.

> If you ran an **earlier** build of this demo, those six were seeded as `Setting`
> rows and are still in your database. They are now inert — the sync no longer
> applies them, so editing them in the admin does nothing. Drop them once:
> `Setting.objects.filter(name__in=[...]).delete()`, or just recreate the demo
> database. Fresh databases never get them.

## Security controls on display

This project deliberately exercises SnapAdmin's security posture — the same
controls documented in the root [`SECURITY.md`](../SECURITY.md): the staff-gated
system dashboard, PII masking (`SNAPADMIN_MASKED_FIELDS`), the immutable audit
trail, per-model API field-exposure/write allowlists, API pagination and
throttling, and the validated async export. Treat `demo/` as a reference for how
to wire those into a real project, not just a feature tour.

## Tests

The pytest suite tests the **package** (`snapadmin/`, kept at 100% coverage)
using this project's `core.settings_test` as its Django settings. It runs from
the repo root:

```bash
pytest
```
