"""
demo/search.py

Elasticsearch integration for the demo app.
Provides search, index, and delete helpers for Product with DB fallback.
"""

import logging
from django.conf import settings

logger = logging.getLogger("snapadmin.demo.search")

ES_INDEX = "snap_demo_product"


def get_es_client():
    from elasticsearch import Elasticsearch
    url = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
    return Elasticsearch([url], request_timeout=5)


def is_es_available() -> bool:
    if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
        return False
    try:
        return bool(get_es_client().ping())
    except Exception:
        return False


def search_products(query: str, limit: int = 20) -> list[dict]:
    """Search products via ES when available, fall back to ORM."""
    if is_es_available():
        try:
            es = get_es_client()
            response = es.search(
                index=ES_INDEX,
                body={
                    "query": {"multi_match": {"query": query, "fields": ["name", "description"], "fuzziness": "AUTO"}},
                    "size": limit,
                },
            )
            return [hit["_source"] for hit in response["hits"]["hits"]]
        except Exception:
            pass

    from demo.app.models import Product
    qs = Product.objects.filter(name__icontains=query)[:limit]
    return [
        {
            "id": p.pk,
            "name": p.name,
            "price": float(p.price),
            "available": bool(p.available),
        }
        for p in qs
    ]


def index_product(product) -> None:
    """Index a single product into Elasticsearch."""
    if not is_es_available():
        return
    try:
        es = get_es_client()
        es.index(
            index=ES_INDEX,
            id=product.pk,
            document={
                "id": product.pk,
                "name": product.name,
                "price": float(product.price),
                "available": bool(product.available),
            },
        )
    except Exception:
        pass


def delete_product(product_id: int) -> None:
    """Remove a product document from Elasticsearch."""
    if not is_es_available():
        return
    try:
        es = get_es_client()
        es.delete(index=ES_INDEX, id=product_id, ignore=[404])
    except Exception:
        pass
