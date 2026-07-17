# Contributing to django-snapadmin

Thanks for helping improve **django-snapadmin**! This guide covers the parts of the workflow
that aren't obvious from the code.

- **Package source:** the published package lives in [`snapadmin/`](snapadmin/). Everything
  else (`demo/`, `tests/`, `docs/`) supports development and is **not** shipped to PyPI.
- **Tests:** `pytest` — the `snapadmin/` package is kept at 100% line coverage.
- **Migrations:** after any model change, run `python manage.py makemigrations` and commit the
  generated migration; never edit an existing migration.

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
