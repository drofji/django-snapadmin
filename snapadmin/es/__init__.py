"""
snapadmin/es/

The Elasticsearch integration's model-layer pieces: the storage-mode enum a
``SnapModel`` opts in with, the manager that resolves to Elasticsearch for an
ES_ONLY model, the mock queryset it returns, and the exception raised when a
DUAL model's ES backend is unavailable and the caller has opted out of the
database fallback.

Split out of ``snapadmin.models`` (#SIMPL1f) — every name here is also
re-exported from ``snapadmin.models``, which stays the stable public import
path (``from snapadmin.models import EsManager`` keeps working unchanged).
This package is the one to extend or read when working on the ES integration
itself; ``snapadmin.models`` is the one downstream code imports from.
"""

from __future__ import annotations

from snapadmin.es.errors import SnapEsUnavailable
from snapadmin.es.manager import EsManager
from snapadmin.es.queryset import EsQuerySet
from snapadmin.es.storage import EsStorageMode

__all__ = ["EsManager", "EsQuerySet", "EsStorageMode", "SnapEsUnavailable"]
