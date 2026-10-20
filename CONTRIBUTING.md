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
- **Before you push — one command runs every gate CI runs:** `python scripts/gates.py`. It runs
  the suite with the coverage gate, Ruff + mypy, `pip check`, the browser suite under both themes
  and — when `SNAPADMIN_TEST_POSTGRES`/`SNAPADMIN_TEST_ES_URL`
  point at running services, and `pg_dump`/`psql` are on `PATH` (the PostgreSQL run executes a real
  backup round trip) — the PostgreSQL and Elasticsearch job. Those variables reach that gate only,
  as in CI; every other gate runs on SQLite. A gate this machine cannot run
  is reported as **not run** with what it needs, never as passed; a gate you name that cannot run
  fails. `--list` shows each gate's exact commands and the CI job that runs them. By name:
  `security` (the security regression suite on its own), `lowest-deps` (needs `uv`: the suite with
  every dependency at its declared minimum, as CI's `lowest-deps` job runs it) and `dist`;
  `--release` runs all of them plus the sdist/wheel build and `twine check`, and fails if any gate
  could not run. The only thing
  it cannot reproduce is the six-job Python × Django matrix. `tests/test_gates_script.py` fails
  when a CI step appears that no gate reproduces.
- **Static analysis:** `ruff check snapadmin && ruff format --check snapadmin` must be clean (CI
  blocks on it); `PYTHONPATH=. mypy` must be clean too — CI blocks on it, and `encryption/`,
  `crypto.py`, `sharding/`, `backup.py`, `validators.py`, `logging_config.py` and `quickstart/` run
  under raised strictness (every function annotated), so keep them that way. A mixin that calls
  `super()` or reads an attribute its own class does not declare gets an `if TYPE_CHECKING:` block
  naming what it is mixed into, rather than a suppression. A suppression is local, names its rule
  and says why: `# noqa: S603 - argv list, no shell`.
- **Tests:** `pytest` from the repo root — the `snapadmin/` package is kept at 100% line and branch
  coverage (`pytest --cov=snapadmin --cov-branch --cov-fail-under=100`).
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
- **Property-based and fuzz tests** (`tests/test_properties_*.py`, `tests/test_fuzz_*.py`) run in
  the ordinary `pytest` — no marker. Each states a law ("`decrypt(encrypt(x)) == x`", "a parameter
  on a masked field changes nothing") and [`hypothesis`](https://hypothesis.readthedocs.io/)
  generates the inputs: 100 per test locally, 300 in CI (`pytest --hypothesis-profile=ci`; both
  profiles are in `tests/conftest.py`). A failure prints the shrunk, smallest failing input and a
  `@reproduce_failure(...)` line — paste it onto the test to replay that exact case. Then decide
  whether the law or the code is wrong: a law that promised too much is narrowed *with a comment
  saying why*; a defect gets a named regression test in the example suite and a fix. Never filter
  the generator to steer around a failure.
- **Query counts** (`tests/test_query_counts.py`) are pinned per surface, at two row counts. If
  your change moves one, confirm the extra query is intended and update the pin in the same commit,
  with the reason in the commit message; if the two counts differ, you added a per-row query.
- **Mutation testing** (`mutmut`, advisory — it reports, it never blocks) asks whether a test would
  *notice* the code being wrong. One command runs it, in CI and locally:

  ```bash
  pip install "mutmut>=3.8"
  python scripts/mutation.py diff --base origin/main   # only what you changed
  python scripts/mutation.py modules                   # the dangerous modules, whole (slow)
  ```

  The first form reads your changed lines out of `git diff`, maps each to the function around it and
  runs only those functions' mutants — minutes, not hours. A **surviving** mutant means the suite
  did not notice that change to the code: read it with `mutmut show <name>` and either strengthen the
  test until it dies, or write down why the mutation is not worth catching (an *equivalent* mutant —
  an unreachable guard, a value nothing can observe — cannot be killed by any test, and a 100% score
  is not the goal). Survivors that only reword a message are counted but not listed: asserting prose
  word for word is not a bar this project sets. `mutants/` is a working copy mutmut creates; it is
  git-ignored.
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
- **Browser E2E** (`tests/e2e/`, marker `e2e`, deselected by default) drives the generated admin
  in a real browser against the demo project on pytest-django's live server. Playwright is a
  dev-only tool; install it and its browser once, then run the suite once per admin theme — the
  theme is fixed per process, and CI runs both:

  ```bash
  pip install "playwright>=1.45" && playwright install chromium
  pytest -m e2e                                    # the Unfold admin
  SNAPADMIN_TEST_ADMIN_THEME=stock pytest -m e2e   # Django's stock admin
  ```

  `SNAPADMIN_E2E_BROWSER=firefox|webkit` swaps the engine (after `playwright install <engine>`),
  `SNAPADMIN_E2E_HEADED=1` shows the window. A failing test leaves a trace in
  `test-results/e2e/` — `playwright show-trace <zip>` replays every step with its DOM, network
  and a screenshot. Every page a test opens also fails it on an uncaught JavaScript error, a
  missing asset or a 5xx, so a new scenario gets those checks for free.
  Writing one: arrange and assert through the ORM (the harness allows it for exactly the length
  of one test), sign in with the `log_in` fixture unless the login form is the subject, assert
  with `expect(...)` from `tests/e2e/support.py`, and find elements by what a user sees or by a
  URL — not by theme-specific classes — so the same test holds under both themes. When the two
  themes genuinely differ (Unfold's filter panel), take each theme's path and assert the same
  outcome. A flaky scenario gets its cause found: no sleeps, no retries, no raised timeouts. And
  a browser test is not a substitute for a fast one — a defect it finds also gets a regression
  test in the ordinary suite.
- **Migrations:** after any model change, run `python demo/manage.py makemigrations` and commit
  the generated migration; never edit an existing migration. `tests/test_migrations_complete.py`
  runs `makemigrations --check` in every CI job, so a forgotten migration fails the build.
- **Translations:** a changed translatable string means regenerating the catalogs, **never from
  the repository root** (there `makemessages` also walks `tests/` and rewrites the package
  catalogs destructively). Package strings: `cd snapadmin`, then
  `python ../demo/manage.py makemessages -l <locale> --no-location` for each locale but `en`
  (English is the source; its catalog stays header-only), then
  `python ../demo/manage.py compilemessages`. Demo strings: the same from `cd demo`.
  `makemessages -a` does nothing in this repository.
  `tests/test_translation_lint.py` fails on an untranslated, fuzzy or placeholder-breaking entry
  (with `tests/test_i18n.py`) and on a `django.mo` that no longer matches its `.po` — commit both
  files. **Native speakers wanted:** only `en` and
  `ru` have been reviewed by one; `de`, `de_CH`, `es`, `fr`, `fr_CH`, `it`, `nl` and `pl` are
  machine-assisted — a review of any of them is one of the most useful contributions there is.
- **Security fixes:** a fix ships with its regression test **and** an entry in
  `tests/security_regressions.py` naming that test, next to its note under *Security* in
  `docs/releases/Unreleased.txt`. `tests/test_security_regressions.py` fails when a Security
  entry has no registry entry, and when a named test disappears or is skipped;
  `python -m pytest -m security_regression` runs the whole set.
- **Dependencies:** a dependency declared in `pyproject.toml` is also listed in
  `demo/requirements.txt` (what CI installs) with the same specifier —
  `tests/test_dependency_compat.py` checks it, and that the Python/Django classifiers are exactly
  the CI matrix. Raising or lowering a lower bound is a promise the `lowest-deps` job then checks.

## Testing methodology

SnapAdmin ships to production installs, so a green suite is only worth what it would catch. The
how-to bullets above say *which* checks run; this section says *how* tests are designed, what a bug
fix must contain, and how the suite itself is checked. It borrows from two manufacturing traditions —
mistake-proofing (Poka-yoke) and standard work (Taylor's scientific management) — and adds the one
thing neither has: tests that test the tests.

### 1. Mistake-proofing the suite (Poka-yoke)

Poka-yoke ("mistake-proofing", Shigeo Shingo, Toyota Production System) makes a mistake impossible,
or detects it the moment it is made and at its source, instead of inspecting quality in later. Its
three detection methods map directly onto kinds of test:

| Poka-yoke method | What it checks | In this repository |
|---|---|---|
| **Contact** — the part's shape is wrong | the *structure* of code or tests | `tests/test_assertions_can_fail.py` reads the suite with `ast` and rejects assertions that cannot fail; `tests/test_public_surface_snapshot.py` diffs every top-level name against a frozen snapshot; mypy (blocking) rejects a wrong type shape; Django system checks reject a misconfiguration at startup instead of at the first request |
| **Fixed-value** — the count is wrong | a number that must match exactly | `tests/test_query_counts.py` (queries at two row counts); the security-regression registry floor (`tests/test_security_regressions.py`); the frozen `# pragma: no cover` ceiling (`tests/test_testing_docs_truth.py`); the version in six places (`tests/test_version_sync.py`) |
| **Motion-step** — a step was skipped | that every step of a flow happens, in order | `tests/test_critical_path_smoke.py` (admin form POST → row → audit entry → REST read); `python scripts/gates.py` (every CI gate; one that cannot run is never a pass); a release is push → CI green → tag, never a tag first |

The rules that follow from it:

- **Control beats warning.** A guard stops the line — it fails the suite or the CI job. A guard may
  be advisory only with a written reason; mutation testing is the one advisory layer (section 4).
- **Detect at the source.** Validate a setting in a system check, not where it is first used;
  validate a test's shape when it is written, not when a bug escapes past it.
- **Every guard says what to review.** Its failure message names the next step — "review: …" — so
  whoever trips it knows what to check without reading the guard's code.
- **A frozen list only shrinks.** An allowlist, a ceiling or a snapshot changes only deliberately,
  in the same commit as the code it describes, with the reason — never loosened to get green.

### 2. Standard work (from Taylor's scientific management)

Taylor studied the work, measured it, wrote down one standard way of doing it, decomposed it into
elementary operations, and separated planning from doing. The parts this project adopts:

- **Plan before doing.** A change starts as a written *Done when* contract; the tests are written
  from it first (TDD), then the code.
- **Decompose.** A task splits into subtasks that can each be verified on their own; a bug fix is
  always the same four operations (section 3).
- **One standard shape per test.** Arrange-Act-Assert; one behaviour per test; names a reader
  understands a year later (`admin_user`, `expected_status`, never `obj1`); assertions that state the
  contract — a value, a status code, an exception type *and* message, a side effect, a query count —
  never `assert result is not None` where a value is checkable; no shared mutable state, no wall
  clock, no order dependence (the suite runs in random order every time).
- **Measure, don't remember.** A count quoted in the docs is re-measured with
  `pytest --collect-only -q` and written as a floor; the everyday run's speed is a budget, so
  anything slow goes behind a marker.
- **Standard checklists.** `python scripts/gates.py` before pushing, `--release` before a tag.

What is deliberately **not** adopted: Taylorism's weak spot is optimising the measurement instead of
the outcome (Goodhart's law). So 100% line and branch coverage is a floor and a helper metric, not
the goal; a test is never weakened, skipped, `xfail`ed, mocked into silence or handed a new expected
value to turn a number green; and when code and test disagree, working out which one is right comes
first. Ambiguous business logic is a question for the maintainers, never a guess pinned into a test.

### 3. The bug-fix protocol — four operations, every fix

1. **Regression test** — reproduces the report and fails without the fix.
2. **Same-class sweep** — find every other place the same mistake can happen: the same code
   pattern, the same input shape, the sibling surfaces (admin, REST, GraphQL, import, export,
   background jobs; sync and async; both admin themes; every database backend). Fix each one, or
   prove it unaffected with a test.
3. **Class guard** — a test that fails on the *next* instance of this kind of bug: generated over
   every model, surface or field type; an `ast` scan; a call or query budget; a frozen allowlist.
   Its failure message says what to review.
4. **Escape analysis** — state why the existing suite did not catch it, using the routes below. A
   route with no guard yet is exactly the class guard step 3 adds.

### 4. Escape routes — how a green suite misses a real bug

| Escape route | What slips through | Countermeasure |
|---|---|---|
| **Homogeneous fixtures** — every test uses one shape (a string tenant column, fields declared on the concrete class) | a bug that only exists on another shape (a `ForeignKey` tenant column, a field inherited from an abstract base) | test models covering each supported shape (`tests/shape_matrix.py` builds the ones fixtures used to miss); suites parametrised over the shapes |
| **Mocks hide cost and call patterns** — a mock accepts any number of calls, for free | a network round-trip per save, an N+1 against a service | call and query budgets; tests against the real service (`pytest -m real_es`, the PostgreSQL job) |
| **The test client is not a browser** — it posts whatever the test writes | a widget/parser mismatch (a checkbox posts `on`, never `true`) | browser E2E (`pytest -m e2e`); posting forms back exactly as a browser serialises them |
| **WSGI only** | sync-only code that blocks or degrades an ASGI deployment | async tests (`AsyncClient`) for every middleware and async-capable view |
| **Inputs from the environment never tried** | an empty string from an unset variable crashing at request time | hostile-input cases (empty, whitespace, wrong type, very long, Unicode); validation in a system check |
| **Negative paths and state combinations** | an unknown parameter silently ignored; a crash on a line covered by a different state | explicit negative tests; property-based and fuzz tests. Coverage proves a line ran, not that every state reached it |
| **Swallowed exceptions** — a broad `except` that logs and carries on | a feature that disappears with nothing but a log line | tests asserting that nothing was logged (`tests/test_startup_is_quiet.py` starts the demo in a fresh process and fails on any startup warning); every silent fallback justified in writing |
| **Clean-checkout CI** | a problem that only exists in a working tree (e.g. an ignored local file picked up by a build) | guards comparing build output with the tracked tree |
| **A test that cannot fail** | everything — it protects nothing and still counts | `tests/test_assertions_can_fail.py`; mutation testing |

### 5. Testing the tests

**Mutation testing** (`mutmut`; how to run it is in the bullet above) is the check that coverage
cannot give: it changes the code — flips a condition, swaps an operator, removes a call — and asks
whether any test notices. It runs per push over the functions the push changed and weekly over the
most dangerous modules (`encryption/`, `crypto.py`, `sharding/router.py`, `masking.py`,
`api/filters.py`, `backup.py`, `restore.py`, `tasks.py`). It is advisory by design, because some
mutants are equivalent and cannot be killed — but every survivor in code you changed gets a
decision: strengthen the assertion until it dies, or write down why it is equivalent. The manual
form costs a minute and is expected on every new assertion: break the code on purpose, watch the
test fail, revert. An assertion you have never seen fail is a claim, not a test.

**Tests that check the suite and the docs follow this method:**

- `tests/test_assertions_can_fail.py` — no `assert True`, no `assert … or True`, and no test without
  an assertion beyond a frozen list that may only shrink, each entry with the reason that "it did not
  raise" is its whole contract (a call to a local `_assert_*` helper that itself asserts counts).
- `tests/test_public_contract.py` + `tests/test_public_surface_snapshot.py` — a public name cannot
  change or disappear silently; `tests/test_critical_path_smoke.py` — the layers still agree end to
  end.
- `tests/test_testing_docs_truth.py` — what the docs say about the test layers is true, in both
  directions (a running check is not listed as missing, a missing one is not claimed).
- `tests/test_gates_script.py` — every CI step has a local gate.
- `tests/test_security_regressions.py` — every Security note in the release notes names a live,
  unskipped test.
- `tests/test_dependency_compat.py`, `tests/test_migrations_complete.py`,
  `tests/test_distribution_contents.py`, `tests/test_ai_entry_points.py`, `tests/test_version_sync.py`,
  `tests/test_translation_lint.py` — declared facts (dependencies, migrations, package contents, docs
  maps, versions, catalogs) match reality.

**What a machine cannot catch — look for it in review:** `assert x in (200, 403, 404)` (passes
whatever the code does — decide which answer is right); `assert isinstance(hash(v), int)` (true of
every hashable object); `assert get_field("x") is not None` (`get_field` raises, so the assertion is
unreachable when false); containment where the whole set is checkable (`"E021" in ids` stays green
next to a spurious second message); a test that never reaches the branch it is named after (a
temporary `raise` in that branch must make it fail); a file named after a metric (`*_coverage.py`,
`*_extended.py`, `misc_*`) — where weak assertions collect because the goal was a number.

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
