"""
snapadmin/pagination.py

Fast, timeout-proof pagination for very large admin tables (issue #5).

On a multi-million-row table the changelist's ``SELECT COUNT(*)`` is the single
most expensive query — it can dominate render time or time out entirely.
:class:`EstimatedCountPaginator` swaps that exact count for PostgreSQL's
``pg_class.reltuples`` planner estimate on **unfiltered** listings of large
tables, and falls back to an exact count everywhere it isn't safe:

* non-PostgreSQL databases,
* filtered querysets (``reltuples`` is whole-table, so a WHERE clause makes it
  wrong — an exact count is used instead),
* tables whose estimate is below ``SNAPADMIN_ESTIMATED_COUNT_THRESHOLD``
  (default 100 000) — small tables always show a precise total.

So small and filtered views are unaffected; only genuinely huge, unfiltered
listings trade an exact total for one that returns instantly. Wired into every
SnapAdmin-generated admin; global kill-switch ``SNAPADMIN_ESTIMATED_COUNT``.
"""

from django.conf import settings
from django.core.paginator import Paginator
from django.db import connections
from django.db.models import QuerySet
from django.utils.functional import cached_property

DEFAULT_THRESHOLD = 100_000


def estimated_count_enabled() -> bool:
    """Whether the fast-count optimisation is active (default True)."""
    return bool(getattr(settings, "SNAPADMIN_ESTIMATED_COUNT", True))


def _estimate_threshold() -> int:
    return int(getattr(settings, "SNAPADMIN_ESTIMATED_COUNT_THRESHOLD", DEFAULT_THRESHOLD))


def pg_estimated_count(queryset) -> int | None:
    """Return PostgreSQL's row estimate for ``queryset``'s table, or ``None``.

    ``None`` whenever an estimate isn't safe to use: not a PostgreSQL queryset,
    the query carries a WHERE clause (estimate is whole-table), or the planner
    has no positive estimate yet (freshly created / never-analysed table).
    """
    if not isinstance(queryset, QuerySet):
        return None
    conn = connections[queryset.db]
    if conn.vendor != "postgresql":
        return None
    # A WHERE clause makes the whole-table estimate wrong — fall back to exact.
    if queryset.query.where:
        return None
    table = queryset.model._meta.db_table
    with conn.cursor() as cursor:
        cursor.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = %s", [table])
        row = cursor.fetchone()
    if not row or row[0] is None or row[0] < 0:
        return None
    return int(row[0])


class EstimatedCountPaginator(Paginator):
    """A :class:`~django.core.paginator.Paginator` with a fast, estimated count.

    Uses :func:`pg_estimated_count` when enabled and the estimate meets the
    threshold; otherwise defers to Django's exact ``count``.
    """

    @cached_property
    def count(self) -> int:
        if estimated_count_enabled():
            estimate = pg_estimated_count(self.object_list)
            if estimate is not None and estimate >= _estimate_threshold():
                return estimate
        return super().count
