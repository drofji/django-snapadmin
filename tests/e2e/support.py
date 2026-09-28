"""
tests/e2e/support.py

Helpers shared by the browser scenarios.

Kept out of ``conftest.py`` so a test module can import them by name, and kept
free of a module-level Playwright import so every scenario module imports — and
is collected, and counted — on a machine that has never installed Playwright.
Only running a scenario needs the browser; see ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, LocatorAssertions, Page, PageAssertions


def expect(actual: Page | Locator) -> PageAssertions | LocatorAssertions:
    """Playwright's auto-waiting ``expect``, imported when a scenario runs."""
    from playwright.sync_api import expect as playwright_expect

    return playwright_expect(actual)


def result_rows(page: Page) -> Locator:
    """The changelist's data rows — the same ``#result_list`` in both themes."""
    return page.locator("#result_list tbody tr")


def change_link(page: Page, url: str) -> Locator:
    """The changelist link to one object, found by its URL, not its theme markup."""
    return page.locator(f'#result_list a[href="{url}"]')
