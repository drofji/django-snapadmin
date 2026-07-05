"""
snapadmin/db.py

Read-replica routing for SnapAdmin's automatically generated read paths.

Heavy API list views, exports and analytical widgets can lock up the primary
write database. When a project runs one or more read replicas, point SnapAdmin
at one with a single setting::

    SNAPADMIN_ANALYTICS_DB_ALIAS = "read_replica"

Every auto-generated **read-only** queryset (list / retrieve) is then evaluated
against that alias via ``.using()``. Writes (POST/PUT/PATCH/DELETE) always stay
on ``default`` so replication lag can never drop or stale a mutation.

If the setting is empty, missing, or names an alias that is not present in
``DATABASES``, routing is a no-op and everything runs on ``default`` — safe by
construction for stock single-database installs.
"""

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS


def analytics_db_alias() -> str:
    """Return the DB alias read-only analytical queries should use.

    Resolves ``SNAPADMIN_ANALYTICS_DB_ALIAS`` and validates it against the
    configured ``DATABASES``. Falls back to :data:`DEFAULT_DB_ALIAS` when unset,
    empty, or pointing at an unknown alias.
    """
    alias = getattr(settings, "SNAPADMIN_ANALYTICS_DB_ALIAS", "") or ""
    if alias and alias in settings.DATABASES:
        return alias
    return DEFAULT_DB_ALIAS


def route_read(queryset):
    """Pin ``queryset`` to the analytics replica for read-only evaluation.

    A no-op when the resolved alias is ``default`` (the common case), so the
    caller need not special-case single-database projects.
    """
    alias = analytics_db_alias()
    if alias != DEFAULT_DB_ALIAS:
        return queryset.using(alias)
    return queryset
