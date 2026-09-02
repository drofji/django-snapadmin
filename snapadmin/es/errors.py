"""
snapadmin/es/errors.py

Exceptions raised by the Elasticsearch integration.
"""

from __future__ import annotations


class SnapEsUnavailable(Exception):
    """Raised by an ES query method when Elasticsearch cannot answer and the
    caller has opted out of the database fallback (``db_fallback=False`` or
    ``SNAPADMIN_ES_DB_FALLBACK=False``).

    Signals that a DUAL model's Elasticsearch backend is disabled or erroring,
    so the query would otherwise have run its (potentially unscalable) database
    equivalent — a full-table ``GROUP BY`` or an unbounded ``.iterator()``. The
    original Elasticsearch error, when there was one, is chained as ``__cause__``.
    ES_ONLY models never raise this (they have no database to fall back to), and
    DB_ONLY models never raise it (the database is their primary store).
    """
