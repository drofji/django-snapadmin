"""
tests/e2e/test_dashboard.py

The system dashboard's "Data Distribution" chart, drawn by Chart.js in a real
browser.

Found by this suite (#QA1e): from 0.1.0b6 the chart was never drawn. A Django
``{# #}`` comment only works on a single line, and a two-line one above the
chart data rendered as page text — text that contained a literal ``<script>``.
The browser opened a script element there, swallowed the chart's JSON into it
as source code and threw twice, so the canvas stayed empty. A test-client test
cannot see any of that: the JSON is in the response, byte for byte.
"""

from __future__ import annotations

import pytest

from snapadmin.tenancy import use_tenant
from tests.conftest import DEFAULT_TEST_TENANT
from tests.e2e.support import expect


@pytest.mark.parametrize(
    "dashboard_url",
    [
        pytest.param("/dashboard/", id="package-dashboard"),
        # The demo's landing page extends the package template for staff.
        pytest.param("/", id="demo-landing-dashboard"),
    ],
)
def test_the_chart_is_drawn_with_the_live_record_counts(
    page, log_in, admin_user, product, product_unavailable, customer, dashboard_url
):
    from demo.apps.shop.models import Order

    with use_tenant(DEFAULT_TEST_TENANT):
        Order.objects.create(customer=customer, total=10, tenant_id=DEFAULT_TEST_TENANT)
    log_in(admin_user)

    page.goto(dashboard_url)
    chart_is_drawn = page.wait_for_function(
        "() => window.Chart && Chart.getChart(document.getElementById('statsChart'))"
    )
    chart_is_drawn.dispose()

    drawn = page.evaluate(
        """() => {
            const chart = Chart.getChart(document.getElementById('statsChart'));
            const counts = Object.fromEntries(
                chart.data.labels.map((label, index) => [label, chart.data.datasets[0].data[index]])
            );
            return {counts, datasetLabel: chart.data.datasets[0].label, width: chart.width};
        }"""
    )
    assert drawn["counts"]["Products"] == 2
    assert drawn["counts"]["Customers"] == 1
    assert drawn["counts"]["Orders"] == 1
    assert drawn["datasetLabel"] == "Record Count"
    assert drawn["width"] > 0
    # The broken comment's own words were the visible symptom.
    expect(page.get_by_text("Serialised through json_script")).to_have_count(0)
