# SnapAdmin Demo

A runnable, self-contained Django project that showcases
[django-snapadmin](https://pypi.org/project/django-snapadmin/). It is **not**
published to PyPI — the package itself lives in [`../snapadmin/`](../snapadmin/).
This project exists so you can evaluate SnapAdmin against realistic models and
develop against the package in editable mode.

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
`SNAPADMIN_*` settings — the ones the package re-reads per request, e.g.
`SNAPADMIN_MASKED_FIELDS`, `SNAPADMIN_API_PAGE_SIZE`, the throttle rates, audit /
error retention days, the export ceilings, `SNAPADMIN_DASHBOARD_PUBLIC` — appears
under **Settings** in the admin (already Unfold-styled), each with a description.
Edit one there and it takes effect immediately, no restart: e.g. set
`SNAPADMIN_MASKED_FIELDS` to `{"demo.Customer": ["email"]}` and the Customer API
starts masking emails on the next request.

How it works (and why it's demo-only) lives in
[`app/managed_settings.py`](app/managed_settings.py): the `snapadmin` package
never depends on `django-extra-settings`, so the demo *syncs* each DB value back
onto `django.conf.settings`, leaving the package to read its config exactly as it
always does. Settings that are read once at boot (URL-routing toggles, admin
nesting) are intentionally **not** surfaced — editing them at runtime wouldn't
take effect — and no secret or credential is ever exposed this way.

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
