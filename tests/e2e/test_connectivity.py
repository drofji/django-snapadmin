"""
tests/e2e/test_connectivity.py

``connectivity.js`` — the admin's backend-outage guard — in a real browser.

It polls ``/api/health/``; once the backend is confirmed down it warns and
disables every Save button so nobody types into a form that cannot be saved,
and when the backend answers again it says so and gives the buttons back. None
of that runs without a browser, so until this suite nothing tested it.

The first scenario is a regression test: the "Back online" toast was meant for
a recovery and appeared on every healthy page load instead (#QA1e), because the
first-probe check read a flag that had already been set.

The poll interval is shortened through the ``SNAPADMIN_HEALTH_INTERVAL`` global
the script reads, and an outage is a routed abort of the health request — the
same failure a dropped connection produces — so no scenario waits on the
fifteen-second production cadence or on a sleep.
"""

from __future__ import annotations

import pytest

from tests.e2e.support import expect

PRODUCT_ADD_URL = "/admin/demo/product/add/"
HEALTH_ROUTE = "**/api/health/"

#: Runs before any page script: a fast poll, a record of every state the
#: script broadcasts (so a scenario waits for "the first probe resolved"
#: instead of guessing how long that takes), and a record of every toast ever
#: shown. The toast log is what makes "nothing was announced" checkable at
#: all: a toast removes itself after a few seconds, so an auto-waiting
#: "count is zero" assertion simply waits it out and passes — which is exactly
#: how the first draft of this file let the regression through.
RECORD_CONNECTIVITY = """
window.SNAPADMIN_HEALTH_INTERVAL = 150;
window.snapConnectivityStates = [];
window.snapToastsShown = [];
document.addEventListener("snapadmin:connectivity", (event) => {
    window.snapConnectivityStates.push(event.detail.up);
});
new MutationObserver((mutations) => {
    for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
            if (node.classList && node.classList.contains("snap-toast")) {
                window.snapToastsShown.push(node.firstChild.textContent);
            }
        }
    }
}).observe(document, {childList: true, subtree: true});
"""


@pytest.fixture
def watched_page(page, context, log_in, admin_user):
    context.add_init_script(RECORD_CONNECTIVITY)
    log_in(admin_user)
    return page


def test_a_healthy_page_load_announces_nothing(watched_page):
    page = watched_page

    page.goto(PRODUCT_ADD_URL)
    first_probe = page.wait_for_function("() => window.snapConnectivityStates.length >= 1")
    first_probe.dispose()

    assert page.evaluate("() => window.snapConnectivityStates") == [True]
    assert page.evaluate("() => window.snapToastsShown") == []
    expect(page.locator('[name="_save"]').first).to_be_enabled()


def test_an_outage_blocks_saving_and_the_recovery_gives_it_back(watched_page):
    page = watched_page
    page.goto(PRODUCT_ADD_URL)
    page.wait_for_function("() => window.snapConnectivityStates.length >= 1").dispose()
    save = page.locator('[name="_save"]').first
    expect(save).to_be_enabled()

    page.route(HEALTH_ROUTE, lambda route: route.abort())

    expect(page.get_by_text("Backend unreachable")).to_be_visible()
    expect(save).to_be_disabled()
    assert "snap-offline" in page.evaluate("() => document.body.className")

    page.unroute(HEALTH_ROUTE)

    expect(page.get_by_text("Back online — backend reachable.")).to_be_visible()
    expect(save).to_be_enabled()
    assert page.evaluate("() => window.snapConnectivityStates") == [True, False, True]
    assert page.evaluate("() => window.snapToastsShown") == [
        "Backend unreachable — objects can't be shown right now. "
        "This page is read-only until you reconnect.",
        "Back online — backend reachable.",
    ]
