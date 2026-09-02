"""
snapadmin/es/manager.py

``EsManager`` — the default manager every ``SnapModel`` gets, which resolves to
Elasticsearch for an ES_ONLY model and to a normal (tenant-scoped) queryset otherwise.
"""

from __future__ import annotations

from django.db import models

from snapadmin.conf import get_setting
from snapadmin.es.queryset import EsQuerySet
from snapadmin.es.storage import EsStorageMode


class EsManager(models.Manager):
    """Manager that uses Elasticsearch for ES_ONLY models."""

    def get_queryset(self):
        # The single point every SnapModel query — the admin, the REST/GraphQL
        # APIs, the offline cache, purge, import's duplicate-key lookup —
        # funnels through, so it is where tenant scoping (#FUT1) is enforced
        # once for all of them. A no-op for a model that never opted in
        # (tenant_scoped = False, the default); see snapadmin.tenancy.
        from snapadmin.tenancy import scope_queryset

        if getattr(self.model, "es_storage_mode", None) == EsStorageMode.ES_ONLY:
            limit = get_setting("SNAPADMIN_ES_SEARCH_LIMIT", 1000)
            qs = self.model.es_search(limit=limit)
            if not isinstance(qs, EsQuerySet):
                qs = EsQuerySet(self.model, [])
            return scope_queryset(self.model, qs)
        # No default ordering is injected here. A default ``order_by("-pk")`` on
        # the base manager leaks into ``GROUP BY`` for ``.values().annotate()``
        # aggregations (Django appends ordering columns to the GROUP BY), which
        # silently returns one row per pk instead of per group. The "-pk" newest-
        # first default is applied in the presentation layers that need a stable
        # order instead (admin changelist ``ordering`` and the API list view).
        return scope_queryset(self.model, super().get_queryset())
