# Contributing to django-snapadmin

Thanks for helping improve **django-snapadmin**! This guide covers the parts of the workflow
that aren't obvious from the code.

- **Package source:** the published package lives in [`snapadmin/`](snapadmin/). Everything
  else supports development and is **not** shipped to PyPI: the runnable demo project lives
  entirely under [`demo/`](demo/) (`demo/core/` = project config, `demo/apps/shop/` = example app,
  plus its own `manage.py`, `requirements.txt`, Docker/Traefik compose files and `dist.env`),
  and `tests/` + `docs/` sit at the repo root.
- **Running things:** the demo's `manage.py` lives in `demo/`; run it from the repo root as
  `python demo/manage.py <command>` (it puts the repo root on `sys.path` itself). Docker runs
  via `docker compose -f demo/docker-compose.yml up --build`. See [`demo/README.md`](demo/README.md).
- **Static analysis:** `ruff check snapadmin && ruff format --check snapadmin` must be clean (CI
  blocks on it); `PYTHONPATH=. mypy` reports type findings (advisory, but `encryption/`, `crypto.py`
  and `sharding/` are strict and clean — keep them that way). A suppression is local, names its rule
  and says why: `# noqa: S603 - argv list, no shell`.
- **Tests:** `pytest` from the repo root — the `snapadmin/` package is kept at 100% line coverage.
  The same suite ships in the **sdist** (never the wheel): `pip download --no-binary :all:
  --no-deps django-snapadmin`, unpack, install the test dependencies, and run `python -m pytest`
  inside the unpacked directory.
  The suite runs in a **random order** every time (`pytest-randomly`), because a test that only
  passes where the alphabet happens to put it is not passing for the right reason. Every run
  prints the seed it used:

  ```
  Using --randomly-seed=3432440628
  ```

  To reproduce a failure exactly, pass that seed back: `pytest -p no:cacheprovider
  --randomly-seed=3432440628`. To rule order out while debugging something else,
  `pytest -p no:randomly` restores the old file order. **A failure under one seed and not another
  is a real bug in the tests** — shared state, an ambient setting, a leaked global — not a reason
  to pin the order; see the empty-test and isolation notes in the testing rules.
- **Databases:** the suite runs on in-memory SQLite by default, so there is nothing to start. CI
  also runs the identical test files against a real PostgreSQL, because the package carries
  backend-specific code (the estimated-count paginator's `reltuples` query, the `pg_dump` backup
  path) that SQLite can never exercise. To do the same locally:

  ```bash
  docker run --rm -d -p 5432:5432 -e POSTGRES_USER=snapadmin \
      -e POSTGRES_PASSWORD=snapadmin -e POSTGRES_DB=snapadmin_test postgres:16
  SNAPADMIN_TEST_POSTGRES=localhost pytest
  ```

  Tests never branch on the backend: the same assertions hold on either. Where behaviour genuinely
  differs, both halves exist and each skips on the backend it does not apply to — keyed on the
  *capability* (`connection.features.supports_json_field_contains`), never on the vendor name.
- **Elasticsearch:** every Elasticsearch test mocks the client, which keeps the everyday run fast
  but leaves the query DSL unchecked — a mock accepts a malformed `terms` clause or a broken
  `search_after` cursor without complaint, and a real cluster answers `400`. The tests in
  `tests/test_elasticsearch_live.py` drive a real one and are **deselected by default**. To run
  them, start the same image the demo ships and ask for the marker:

  ```bash
  docker run --rm -d -p 9200:9200 -e discovery.type=single-node \
      -e xpack.security.enabled=false \
      docker.elastic.co/elasticsearch/elasticsearch:8.13.0
  SNAPADMIN_TEST_ES_URL=http://localhost:9200 pytest -m real_es
  ```

  CI runs both of the above in one job, on one Python/Django combination — a backend difference
  does not depend on the interpreter version, so spreading it across the matrix would cost six
  times the minutes to learn the same thing.
- **Migrations:** after any model change, run `python demo/manage.py makemigrations` and commit
  the generated migration; never edit an existing migration.

## Releasing to PyPI

Releases publish automatically. Pushing a version tag (`v*`) runs
[`.github/workflows/publish.yml`](.github/workflows/publish.yml), which builds the sdist +
wheel, runs `twine check --strict` (this is what guarantees the README renders correctly on the
project page), verifies the tag matches the `pyproject.toml` version, and uploads to PyPI.

### One-time setup: PyPI Trusted Publishing (OIDC)

The workflow uploads via **Trusted Publishing** — PyPI trusts GitHub's OIDC identity, so **no
API token is stored in the repository**. Configure it once:

1. Sign in to <https://pypi.org> as a maintainer of `django-snapadmin`.
2. Go to the project → **Settings → Publishing → Add a new publisher** (for the very first
   release, use **Your projects → Publishing → Add a pending publisher** instead).
3. Fill in exactly:
   | Field | Value |
   | --- | --- |
   | PyPI Project Name | `django-snapadmin` |
   | Owner | `drofji` |
   | Repository name | `django-snapadmin` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |
4. Save. From then on, every `v*` tag publishes automatically.

> The GitHub Actions job runs in the `pypi` environment and requests `id-token: write`; both are
> already declared in the workflow — no repository secret is required.

### Cutting a release

1. Bump `version` in `pyproject.toml` (PEP 440: `0.1.0a7`, `0.1.0b1`, `1.0.0`). PyPI rejects
   re-uploads of an existing version.
2. Finalize `docs/releases/Unreleased.txt` into `docs/releases/X.Y.Z.txt`, and update the version
   badge/footer in `docs/index.html`. Add a `docs/migrations/` guide only if this release needs one
   (see `docs/migrations/README.md`).
3. Commit, then tag and push:
   ```bash
   git push origin main
   git tag v0.1.0a7 && git push origin v0.1.0a7
   ```
4. Watch the **Publish to PyPI** workflow run. Once green, confirm the new version at
   <https://pypi.org/project/django-snapadmin/>.

You can also trigger a dry run from the **Actions** tab (`workflow_dispatch`): it builds and
verifies the distribution but skips the upload (upload only runs on a tag push).

## Project links

- **Documentation:** <https://drofji.github.io/django-snapadmin/>
- **Repository:** <https://github.com/drofji/django-snapadmin>
- **Issues:** <https://github.com/drofji/django-snapadmin/issues>
