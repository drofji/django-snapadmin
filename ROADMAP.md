# Roadmap — drofji-snapadmin

Version in development: `0.1.0a2` (unreleased)

---

## Bug Fixes

| # | Description | Status |
|---|-------------|--------|
| 7 | Fix Celery startup — `sandbox` app missing `celery.py` | pending |
| 1 | Django Admin Unfold: "Add" button missing top-right in table (desktop) | pending |
| 4 | Edit form: field labels above the field, not to the left | pending |
| 5 | TextField / WYSIWYG / wide fields — cap width to viewport | pending |
| 6 | Improve padding between field border and content | pending |

---

## Features

| # | Description | Status |
|---|-------------|--------|
| 2 | Clickable table rows — navigate to detail view; global admin when module is installed, SnapModel-only otherwise | pending |
| 3 | Swagger: auto-generate filter parameters for every model field | pending |
| 8 | Docs (`docs/index.html`): DB_ONLY / DUAL / ES_ONLY setup and query guide | pending |
| 9 | Offline mode — optional, per-model toggle, IndexedDB/LocalStorage, auto-sync on reconnect | pending |

---

## Documentation & Migration

| # | Description | Status |
|---|-------------|--------|
| A | Docs overhaul in PyPI style — split into 3 sections: (1) package users guide, (2) sandbox/demo guide, (3) migration from [drofji-automatically-django-admin](https://github.com/drofji/drofji-automatically-django-admin) (GitHub only, not on PyPI) with removal notes + full how-to checklist | pending |

---

## Pre-release Review

| # | Description | Status |
|---|-------------|--------|
| B | Review project for improvements before releasing stable (non-alpha) version to PyPI — API cleanup, missing tests, packaging hygiene | pending |
| C | Test suite audit — run all tests, check migrations (create missing, commit), verify overall project health | pending |
| D | Field types review — audit what additional SnapField types can be added to simplify common Django field patterns | pending |
| E | Dependency audit — verify `pyproject.toml` (package deps) and `requirements.txt` (sandbox) are correct, minimal, and consistent | pending |
| F | Elasticsearch integration overhaul — review DB_ONLY/DUAL/ES_ONLY logic; auto-sync data from model to ES (real-time via signals + optional Celery cron); add env config for external vs Docker ES/PG (skip containers if external URL set); update docs with pros/cons of each sync strategy + sandbox examples for all 3 modes | pending |
| G | Document all `.env` / settings variables with explanation of what each does (e.g. `SNAPADMIN_AUTO_SEED`, `ELASTICSEARCH_ENABLED`, etc.) — in docs and in `dist.env` inline comments | pending |
| H | Optional Traefik integration for sandbox — reverse proxy with automatic HTTPS certificate issuance, enabled via env flag | pending |

---

## Cross-cutting

| # | Description | Status |
|---|-------------|--------|
| 0 | Large dataset optimizations (~5M rows) — queryset tuning, pagination, `select_related`, `only()`, ES offloading | ongoing |
