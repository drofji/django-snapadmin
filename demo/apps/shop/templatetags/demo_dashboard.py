"""Template tag backing the demo's custom admin index panel.

``{% demo_dashboard %}`` renders ``demo/_dashboard_body.html`` — a **partial**, not a
page. The panel used to be a template that extended an Unfold layout and was then
``{% include %}``-d from ``admin/index.html``; including a template that extends a base
renders that base too, so the whole admin shell (sidebar, header, menu) appeared a second
time inside the content area. Keeping the body in a partial and feeding it from an
inclusion tag makes that mistake impossible to repeat.

Every figure below is a live query — the demo is the package's own dogfood, so it must
not display invented numbers.
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from typing import Any

from django import template
from django.conf import settings
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

register = template.Library()

#: How many rows the "Recent activity" list shows per source.
RECENT_LIMIT = 4


def _disk_usage_percent() -> int:
    """Percentage of the volume holding the project that is in use."""
    usage = shutil.disk_usage(str(settings.BASE_DIR))
    if not usage.total:
        return 0
    return round(usage.used / usage.total * 100)


def _availability_percent(total: int, available: int) -> int:
    if not total:
        return 0
    return round(available / total * 100)


def _recent_activity() -> list[dict[str, Any]]:
    """The newest orders and customers, newest first, as display rows."""
    from demo.apps.shop.models import Customer, Order

    rows: list[dict[str, Any]] = []
    for order in Order.objects.select_related("customer").order_by("-created_at")[:RECENT_LIMIT]:
        rows.append({
            "dot": "bg-blue-500",
            "text": _("Order #%(pk)s — %(customer)s") % {
                "pk": order.pk,
                "customer": order.customer,
            },
            "when": order.created_at,
        })
    for customer in Customer.objects.order_by("-pk")[:RECENT_LIMIT]:
        rows.append({
            "dot": "bg-green-500",
            "text": _("Customer %(name)s registered") % {"name": customer},
            "when": None,
        })
    return rows


@register.inclusion_tag("demo/_dashboard_body.html")
def demo_dashboard() -> dict[str, Any]:
    """Collect the live figures rendered by the demo's admin index panel."""
    from demo.apps.shop.models import Customer, Order, Product

    product_total = Product.objects.count()
    product_available = Product.objects.filter(available=True).count()
    revenue = Order.objects.aggregate(total=Sum("total"))["total"] or Decimal("0")

    return {
        "total_orders": Order.objects.count(),
        "active_customers": Customer.objects.filter(active=True).count(),
        "revenue": revenue,
        "recent_activity": _recent_activity(),
        "disk_percent": _disk_usage_percent(),
        "product_total": product_total,
        "product_available": product_available,
        "availability_percent": _availability_percent(product_total, product_available),
    }
