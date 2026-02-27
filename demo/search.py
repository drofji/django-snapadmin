"""
demo/search.py

Elasticsearch integration for SnapAdmin demo.

This module provides:
  - is_es_available()   : Safe connectivity probe (never raises).
  - get_es_client()     : Returns a configured Elasticsearch client.
  - search_products()   : Full-text product search with DB fallback.
  - index_product()     : Index or update a single product document.
  - delete_product()    : Remove a product document from the index.

Design principle: Every public function that touches Elasticsearch wraps
its call in a try/except and falls back to a DB query when ES is down.
This ensures the application remains fully functional without Elasticsearch.
"""

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger("snapadmin.demo.search")

# Index name used for product documents
PRODUCTS_INDEX = "snapadmin_products"


def get_es_client():
    """
    Build and return an Elasticsearch client using settings.ELASTICSEARCH_URL.

    Returns:
        elasticsearch.Elasticsearch instance.
    """
    from elasticsearch import Elasticsearch

    url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
    return Elasticsearch([url], request_timeout=5)


def is_es_available() -> bool:
    """
    Probe Elasticsearch and return True if it is reachable.

    This function never raises — all connectivity errors are caught and
    logged as warnings, returning False to signal graceful degradation.

    Returns:
        True when Elasticsearch is reachable and the cluster is healthy.
    """
    if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
        return False

    try:
        es = get_es_client()
        return es.ping()
    except Exception as exc:
        logger.warning("elasticsearch_ping_failed", error=str(exc))
        return False


def _ensure_index(es) -> None:
    """
    Create the products index with basic mappings if it does not already exist.

    Args:
        es: An active Elasticsearch client.
    """
    if es.indices.exists(index=PRODUCTS_INDEX):
        return

    es.indices.create(
        index=PRODUCTS_INDEX,
        body={
            "mappings": {
                "properties": {
                    "id":        {"type": "integer"},
                    "name":      {"type": "text", "analyzer": "standard"},
                    "price":     {"type": "float"},
                    "available": {"type": "boolean"},
                }
            }
        },
    )
    logger.info("elasticsearch_index_created", index=PRODUCTS_INDEX)


def search_products(query: str, limit: int = 20) -> list:
    """
    Search products by name using Elasticsearch with automatic DB fallback.

    When Elasticsearch is unavailable the function performs a Django ORM
    ``icontains`` query so the UI continues to work seamlessly.

    Args:
        query: The search string entered by the user.
        limit: Maximum number of results to return.

    Returns:
        A list of dicts, each containing at least: id, name, price, available.
    """
    if is_es_available():
        try:
            return _search_products_es(query, limit)
        except Exception as exc:
            logger.warning("elasticsearch_search_failed_fallback_to_db", error=str(exc))

    return _search_products_db(query, limit)


def _search_products_es(query: str, limit: int) -> list:
    """
    Perform a full-text product search against Elasticsearch.

    Args:
        query: Search string.
        limit: Max number of hits.

    Returns:
        List of product dicts extracted from ES _source.
    """
    es = get_es_client()
    response = es.search(
        index=PRODUCTS_INDEX,
        body={
            "query": {
                "multi_match": {
                    "query":  query,
                    "fields": ["name^3"],
                    "fuzziness": "AUTO",
                }
            },
            "size": limit,
        },
    )
    hits = response.get("hits", {}).get("hits", [])
    return [hit["_source"] for hit in hits]


def _search_products_db(query: str, limit: int) -> list:
    """
    Fallback: search products using a Django ORM ``icontains`` query.

    Args:
        query: Search string.
        limit: Max results.

    Returns:
        List of product dicts.
    """
    from demo.models import Product

    qs = Product.objects.filter(name__icontains=query)[:limit]
    return [
        {
            "id":        p.pk,
            "name":      p.name,
            "price":     float(p.price) if p.price else 0.0,
            "available": p.available,
        }
        for p in qs
    ]


def index_product(product) -> None:
    """
    Index or update a single Product document in Elasticsearch.

    Silently skips when Elasticsearch is unavailable.

    Args:
        product: A Product model instance.
    """
    if not is_es_available():
        return

    try:
        es = get_es_client()
        _ensure_index(es)
        es.index(
            index=PRODUCTS_INDEX,
            id=product.pk,
            document={
                "id":        product.pk,
                "name":      product.name,
                "price":     float(product.price) if product.price else 0.0,
                "available": product.available,
            },
        )
        logger.debug("product_indexed", product_id=product.pk)
    except Exception as exc:
        logger.warning("product_index_failed", product_id=product.pk, error=str(exc))


def delete_product(product_id: int) -> None:
    """
    Remove a product document from the Elasticsearch index.

    Silently skips when Elasticsearch is unavailable.

    Args:
        product_id: Primary key of the product to remove.
    """
    if not is_es_available():
        return

    try:
        es = get_es_client()
        es.delete(index=PRODUCTS_INDEX, id=product_id, ignore=[404])
        logger.debug("product_deleted_from_index", product_id=product_id)
    except Exception as exc:
        logger.warning("product_delete_from_index_failed", product_id=product_id, error=str(exc))
