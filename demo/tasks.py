"""
demo/tasks.py

Celery background tasks for the demo application.

Includes:
  - Elasticsearch product re-indexing (gracefully skipped when ES is unavailable)
  - Daily stats snapshot generation (demonstrates DB interaction with Celery Beat)
"""

import logging
from datetime import date

from celery import shared_task

logger = logging.getLogger("snapadmin.demo.tasks")


@shared_task(bind=True, name="demo.tasks.reindex_products_to_elasticsearch")
def reindex_products_to_elasticsearch(self):
    """
    Synchronise all Product records to the Elasticsearch index.
    """
    from demo.models import Product
    result = Product.es_reindex_all()
    if result.get("skipped"):
        logger.warning("elasticsearch_unavailable_skip_reindex")
    else:
        logger.info("elasticsearch_reindex_complete", indexed=result["indexed"])
    return result


@shared_task(bind=True, name="demo.tasks.generate_daily_stats")
def generate_daily_stats(self):
    """
    Compute and log daily business stats from the demo models.

    This task demonstrates a Celery Beat → Django ORM → structured log pipeline.
    In a production app, you would persist these stats to a Stats model or BI DB.

    Returns:
        dict: Snapshot of today's key metrics.
    """
    from demo.models import Customer, Order, Product
    from django.db.models import Sum, Avg, Count

    today = date.today()

    stats = {
        "date":              today.isoformat(),
        "total_products":    Product.objects.count(),
        "active_products":   Product.objects.filter(available=True).count(),
        "total_customers":   Customer.objects.count(),
        "active_customers":  Customer.objects.filter(active=True).count(),
        "total_orders":      Order.objects.count(),
        "total_revenue":     float(Order.objects.aggregate(t=Sum("total"))["t"] or 0),
        "avg_order_value":   float(Order.objects.aggregate(a=Avg("total"))["a"] or 0),
    }

    logger.info("daily_stats_generated", **stats)
    return stats
