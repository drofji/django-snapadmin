"""
snapadmin/es/storage.py

The storage-mode enum a SnapModel declares to opt into Elasticsearch.
"""

from __future__ import annotations

from enum import Enum


class EsStorageMode(str, Enum):
    """Modes for Elasticsearch integration."""

    DB_ONLY = "db_only"  # Standard Django behavior
    DUAL = "dual"        # Save to both DB and ES, search via ES
    ES_ONLY = "es_only"  # Save/retrieve only via ES, no DB table needed
