"""
snapadmin/es/queryset.py

``EsQuerySet`` — the mock QuerySet an ES_ONLY/DUAL model's Elasticsearch-backed
methods return, so callers written against a real Django QuerySet keep working.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async

from snapadmin.es.storage import EsStorageMode
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)


class EsQuerySet:
    """A lightweight mock QuerySet for Elasticsearch-only models."""

    def __init__(self, model, hits=None, filters=None):
        from django.db.models.sql.query import Query
        self.model = model
        self._hits = hits if hits is not None else []
        # Every field=value filter chained onto this queryset so far — kept
        # even though filter() already narrowed _hits, because get() below
        # bypasses _hits entirely (it fetches straight from ES by pk) and
        # must re-check this dict to avoid returning a hit outside the
        # already-applied filter (see get()'s docstring).
        self._filters = dict(filters) if filters else {}
        self.query = Query(model)  # Mock query for DRF
        self._result_cache = self._hits
        self._prefetch_related_lookups = []
        self._sticky_filter = False
        self._for_write = False
        self._prefetch_done = False
        self._known_related_objects = {}

    def __iter__(self):
        return iter(self._hits)

    def __len__(self):
        return len(self._hits)

    def __getitem__(self, k):
        if isinstance(k, slice):
            return self._clone(self._hits[k])
        return self._hits[k]

    def count(self):
        return len(self._hits)

    def delete(self):
        if self.model.es_storage_mode == EsStorageMode.ES_ONLY:
            try:
                es = self.model.get_es_client()
                for hit in self._hits:
                    es.delete(index=self.model.get_es_index_name(), id=hit.pk, ignore=[404])
            except Exception as exc:
                logger.warning(
                    "es_queryset_delete_failed",
                    model=self.model.__name__,
                    hit_count=len(self._hits),
                    error=str(exc),
                )
        return len(self._hits), {self.model._meta.label: len(self._hits)}

    def filter(self, *args, **kwargs):
        if not kwargs:
            return self

        new_hits = []
        for hit in self._hits:
            match = True
            for key, val in kwargs.items():
                # Handle simple filter: field=value
                if getattr(hit, key, None) != val:
                    match = False
                    break
            if match:
                new_hits.append(hit)
        return self._clone(new_hits, filters={**self._filters, **kwargs})

    def exclude(self, *args, **kwargs):
        return self

    def order_by(self, *field_names):
        return self

    def select_related(self, *fields):
        return self

    def prefetch_related(self, *lookups):
        return self

    def _clone(self, hits=None, filters=None):
        return EsQuerySet(
            self.model,
            hits if hits is not None else self._hits,
            filters if filters is not None else self._filters,
        )

    def using(self, alias):
        return self

    def none(self):
        return self._clone([])

    def all(self):
        return self

    def get(self, *args, **kwargs):
        """Fetch one document by pk directly from Elasticsearch.

        Bypasses ``_hits`` entirely (a direct ES lookup, not a scan of an
        already-fetched page), so any ``filter()`` chained onto this
        queryset before ``get()`` — most importantly a tenant-scoping filter
        (see ``snapadmin.tenancy.scope_queryset``) — is re-checked against
        the fetched document here via ``_filters``. Without this, a caller
        holding a *filtered* queryset could still ``.get(pk=...)`` a
        document the filter excluded, since ES itself was never asked about
        the filter at all.
        """
        pk = kwargs.get("pk") or kwargs.get("id")
        if pk:
            try:
                es = self.model.get_es_client()
                hit = es.get(index=self.model.get_es_index_name(), id=str(pk))
                data = hit["_source"]
                obj = self.model(**{k: v for k, v in data.items() if k != "id"})
                obj.pk = data.get("id")
            except Exception as exc:
                # A connection failure surfaces as DoesNotExist to the caller —
                # log the real cause so outages aren't mistaken for missing rows.
                logger.warning(
                    "es_get_failed",
                    model=self.model.__name__,
                    pk=pk,
                    error=str(exc),
                )
                raise self.model.DoesNotExist
            for key, val in self._filters.items():
                if getattr(obj, key, None) != val:
                    raise self.model.DoesNotExist
            return obj
        raise self.model.DoesNotExist

    def first(self):
        """The first hit, or ``None`` — matches ``QuerySet.first()``'s contract."""
        return self._hits[0] if self._hits else None

    def last(self):
        """The last hit, or ``None`` — matches ``QuerySet.last()``'s contract."""
        return self._hits[-1] if self._hits else None

    # ------------------------------------------------------------------
    # Async counterparts (#PROP1b). A real Django QuerySet gets aget/afirst/
    # alast for free from Django itself; EsQuerySet is a standalone mock (it
    # does not subclass QuerySet), so it needs its own thin async wrappers
    # over the sync methods above — the same pattern Django's own QuerySet
    # uses internally.
    # ------------------------------------------------------------------

    async def aget(self, *args, **kwargs):
        return await sync_to_async(self.get, thread_sensitive=True)(*args, **kwargs)

    async def afirst(self):
        return await sync_to_async(self.first, thread_sensitive=True)()

    async def alast(self):
        return await sync_to_async(self.last, thread_sensitive=True)()

    def exists(self) -> bool:
        return bool(self._hits)

    @property
    def ordered(self) -> bool:
        return True
