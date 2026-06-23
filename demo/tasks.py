"""
demo/tasks.py

Celery background tasks for the demo application.

Includes:
  - Elasticsearch product re-indexing (gracefully skipped when ES is unavailable)
  - Daily stats snapshot generation (demonstrates DB interaction with Celery Beat)
"""

from datetime import date

from celery import shared_task

from snapadmin.logging_config import get_logger

logger = get_logger("snapadmin.demo.tasks")


@shared_task(bind=True, name="demo.tasks.reindex_products_to_elasticsearch")
def reindex_products_to_elasticsearch(self):
    """
    Synchronise all Product records to the Elasticsearch index via demo.search.
    """
    from demo.models import Product
    from demo.search import is_es_available, index_product

    if not is_es_available():
        logger.warning("elasticsearch_unavailable_skip_reindex")
        return {"skipped": True, "reason": "Elasticsearch not available"}

    indexed = 0
    for product in Product.objects.all():
        index_product(product)
        indexed += 1

    logger.info("elasticsearch_reindex_complete", indexed=indexed)
    return {"indexed": indexed}


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
