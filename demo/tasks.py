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

    Gracefully degrades when Elasticsearch is unavailable — logs a warning
    and returns early instead of raising an exception that would flood the
    Celery beat log with retries.

    Returns:
        dict: Summary with the count of indexed documents or a skip reason.
    """
    from demo.search import get_es_client, PRODUCTS_INDEX, is_es_available

    if not is_es_available():
        logger.warning("elasticsearch_unavailable_skip_reindex")
        return {"skipped": True, "reason": "Elasticsearch not available"}

    from demo.models import Product

    es = get_es_client()
    products = Product.objects.all()
    indexed = 0

    for product in products:
        doc = {
            "id":        product.pk,
            "name":      product.name,
            "price":     float(product.price) if product.price else 0.0,
            "available": product.available,
        }
        es.index(index=PRODUCTS_INDEX, id=product.pk, document=doc)
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
