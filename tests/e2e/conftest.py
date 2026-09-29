"""
tests/e2e/conftest.py

The browser end-to-end suite (#QA1e): Playwright driving a real browser against
the ``demo/`` project, served over real HTTP by pytest-django's live server.

It proves what Django's test client cannot, because the test client is not a
browser: that the generated admin's JavaScript runs without throwing, that every
stylesheet and script a page references is actually served, that a select2
autocomplete, an inline's "add another row" and the dashboard's Chart.js canvas
work when a person clicks them, and that both admin themes — Unfold and Django's
stock admin — render a usable page.

**How to run it** (deselected by default, so a plain ``pytest`` never needs a
browser)::

    pip install "playwright>=1.45" && playwright install chromium
    python -m pytest -m e2e                                   # the Unfold admin
    SNAPADMIN_TEST_ADMIN_THEME=stock python -m pytest -m e2e  # the stock admin

``SNAPADMIN_E2E_BROWSER=firefox|webkit`` swaps the engine (after
``playwright install <engine>``) and ``SNAPADMIN_E2E_HEADED=1`` shows the window.
A failing test leaves a Playwright trace in ``test-results/e2e/``; open it with
``playwright show-trace <file>`` to replay the run step by step, DOM included.

**Why the harness is shaped like this.**

* **Playwright starts and stops once per test.** Its synchronous API keeps an
  asyncio loop marked as running in the main thread for as long as it is
  started, and Django refuses ORM calls from a thread with a running loop
  (``SynchronousOnlyOperation``). Arranging and asserting through the ORM
  therefore needs ``DJANGO_ALLOW_ASYNC_UNSAFE`` — and a session-wide Playwright
  would leave both the loop and that escape hatch switched on for every test
  that runs after it in the same process, the ordinary suite included. Scoping
  both to one test costs about a third of a second per test and makes the leak
  impossible rather than merely unlikely.
* **Every page is checked for the three failures no assertion names.** An
  uncaught JavaScript exception; a script, stylesheet, image, font or icon of
  this site answered with an HTTP error; and any response of this site — a page, a
  health probe, an autocomplete lookup — answered with a 5xx. Each fails the
  test at teardown, whichever page it happened on: they are exactly the defects
  a test-client suite is blind to, and they would otherwise pass every scenario
  that did not happen to look at the broken widget. (A 403 or 404 is not in the
  list, because a permission scenario expects one and asserts it itself.)
* **English, UTC, a fixed viewport.** Text assertions must not depend on the
  machine's locale (the demo ships ten), and a layout that collapses the
  sidebar on a narrow window must not depend on the runner's screen.
* **No sleeps, no retries, no raised timeouts** (``QUALITY.md`` §23). Timing is
  Playwright's auto-waiting ``expect``; a scenario that is still unstable gets
  its cause found, not a rerun.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from playwright.sync_api import Browser, BrowserContext, Page, Response
    from pytest_django.live_server_helper import LiveServer

E2E_DIR = Path(__file__).resolve().parent
#: Where a failed scenario's trace goes. ``SNAPADMIN_E2E_ARTIFACTS_DIR``
#: redirects it — the harness's own test uses that to look at one run's output.
ARTIFACTS_DIR = Path(
    os.environ.get("SNAPADMIN_E2E_ARTIFACTS_DIR")
    or E2E_DIR.parent.parent / "test-results" / "e2e"
)

_BROWSER_ENGINES = ("chromium", "firefox", "webkit")
#: Requests a scenario itself may expect to be refused (a page it has no
#: permission for, a health route a deployment does not mount). Everything else
#: a page loads on its own — scripts, styles, images, fonts, icons, manifests —
#: must exist.
_REQUESTS_A_SCENARIO_ASSERTS = frozenset({"document", "fetch", "xhr"})
_INSTALL_HINT = (
    "The browser suite needs Playwright, which is a dev-only tool: "
    'pip install "playwright>=1.45" && playwright install chromium'
)
_reports_key = pytest.StashKey[dict]()
#: Set by the ``page`` fixture when its teardown is about to fail the test. The
#: ``context`` fixture tears down after ``page`` but before pytest has built the
#: teardown report, so without this flag it cannot know the test failed.
_page_problems_key = pytest.StashKey[bool]()


# ─────────────────────────────────────────────────────────────────────────────
# Collection and reporting hooks
# ─────────────────────────────────────────────────────────────────────────────

@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything under ``tests/e2e/`` as ``e2e``, before ``-m`` filters.

    The marker comes from the directory rather than from a ``pytestmark`` line
    in each module, so a new browser test cannot forget it and slip into the
    default run. ``tryfirst`` makes this run before pytest's own marker
    deselection, which is what ``-m "not e2e"`` relies on.
    """
    for item in items:
        if E2E_DIR in item.path.parents:
            item.add_marker(pytest.mark.e2e)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator:
    """Keep each phase's report on the item, so a fixture can see a failure."""
    report = yield
    item.stash.setdefault(_reports_key, {})[report.when] = report
    return report


def _test_failed(item: pytest.Item) -> bool:
    if item.stash.get(_page_problems_key, False):
        return True
    return any(report.failed for report in item.stash.get(_reports_key, {}).values())


# ─────────────────────────────────────────────────────────────────────────────
# Browser, context, page
# ─────────────────────────────────────────────────────────────────────────────

def _engine_name() -> str:
    engine = os.environ.get("SNAPADMIN_E2E_BROWSER", "chromium")
    if engine not in _BROWSER_ENGINES:
        raise pytest.UsageError(
            f"SNAPADMIN_E2E_BROWSER={engine!r}: expected one of {', '.join(_BROWSER_ENGINES)}."
        )
    return engine


@pytest.fixture
def browser(live_server: LiveServer, monkeypatch: pytest.MonkeyPatch) -> Iterator[Browser]:
    """A browser for one test — and the ORM escape hatch for exactly as long.

    ``live_server`` is requested first so the server thread (and pytest-django's
    transactional database) is up before the event loop starts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.exit(_INSTALL_HINT, returncode=pytest.ExitCode.USAGE_ERROR)

    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    with sync_playwright() as playwright:
        engine = getattr(playwright, _engine_name())
        launched = engine.launch(headless=os.environ.get("SNAPADMIN_E2E_HEADED") != "1")
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture
def context(
    browser: Browser, live_server: LiveServer, request: pytest.FixtureRequest
) -> Iterator[BrowserContext]:
    """An isolated browser profile, traced; the trace is kept only on failure."""
    browser_context = browser.new_context(
        base_url=live_server.url,
        locale="en-US",
        timezone_id="UTC",
        viewport={"width": 1440, "height": 900},
    )
    browser_context.tracing.start(screenshots=True, snapshots=True)
    try:
        yield browser_context
    finally:
        if _test_failed(request.node):
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            trace_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.nodeid).strip("-")
            browser_context.tracing.stop(path=ARTIFACTS_DIR / f"{trace_name}.zip")
        else:
            browser_context.tracing.stop()
        browser_context.close()


@dataclass
class PageProblems:
    """What went wrong on a page without any scenario asking about it."""

    uncaught_errors: list[str] = field(default_factory=list)
    broken_assets: list[str] = field(default_factory=list)
    server_errors: list[str] = field(default_factory=list)

    def any(self) -> bool:
        return bool(self.uncaught_errors or self.broken_assets or self.server_errors)

    def record_response(self, response: Response, origin: str) -> None:
        if not response.url.startswith(origin):
            return
        described = f"{response.status} {response.url} (on {response.frame.url})"
        if response.status >= 500:
            self.server_errors.append(described)
        elif (
            response.status >= 400
            and response.request.resource_type not in _REQUESTS_A_SCENARIO_ASSERTS
        ):
            self.broken_assets.append(described)


@pytest.fixture
def page(
    context: BrowserContext, live_server: LiveServer, request: pytest.FixtureRequest
) -> Iterator[Page]:
    """A tab that fails the test if any page it visits throws, loses an asset or 5xxs."""
    tab = context.new_page()
    problems = PageProblems()
    tab.on("pageerror", lambda error: problems.uncaught_errors.append(f"{tab.url}: {error}"))
    tab.on("response", lambda response: problems.record_response(response, live_server.url))
    yield tab
    if problems.any():
        # Tell ``context`` before failing, so the trace of this failure is kept.
        request.node.stash[_page_problems_key] = True
    assert problems.uncaught_errors == [], (
        f"uncaught JavaScript error(s) on the page: {problems.uncaught_errors}"
    )
    assert problems.broken_assets == [], (
        f"asset(s) this site references but does not serve: {problems.broken_assets}"
    )
    assert problems.server_errors == [], (
        f"the server answered with a 5xx: {problems.server_errors}"
    )


@pytest.fixture
def log_in(context: BrowserContext, live_server: LiveServer) -> Callable[[AbstractBaseUser], None]:
    """Give the browser an authenticated session without typing a password.

    The login form has its own scenario; every other scenario starts signed in,
    through the same session machinery a real login uses.
    """
    from django.conf import settings
    from django.test import Client

    def sign_in(user: AbstractBaseUser) -> None:
        client = Client()
        client.force_login(user)
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                    "url": live_server.url,
                }
            ]
        )

    return sign_in


@pytest.fixture
def unfold_admin() -> bool:
    """Which admin this process renders — fixed at import time, see settings_test."""
    from django.conf import settings

    return "unfold" in settings.INSTALLED_APPS
