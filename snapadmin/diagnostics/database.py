"""
Database collector for ``snapadmin_info``.

Reports the default connection's engine/name/host/port/user (never the password), whether it is
reachable, its table count and — where the backend exposes it — the on-disk size. Registered as a
health probe so ``--health-check`` fails if the database cannot be reached.
"""

from __future__ import annotations

import os

from django.db import connections

from snapadmin.diagnostics.registry import register


def _db_size(conn) -> int | None:
    """Best-effort on-disk size in bytes for ``conn``'s database, or ``None``."""
    vendor = conn.vendor
    try:
        if vendor == "sqlite":
            path = conn.settings_dict.get("NAME")
            if path and path != ":memory:" and os.path.exists(path):
                return os.path.getsize(path)
            return None
        if vendor == "postgresql":
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_database_size(current_database())")
                return cursor.fetchone()[0]
        if vendor == "mysql":
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT SUM(data_length + index_length) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )
                return cursor.fetchone()[0]
    except Exception:
        return None
    return None


@register("database", title="Database", icon="🗄", order=20, health_probe=True)
def collect(*, verbose: bool) -> dict:
    """Collect the primary-database section."""
    conn = connections["default"]
    config = conn.settings_dict
    data: dict = {
        "engine": conn.vendor,
        "name": str(config.get("NAME") or ""),
        "host": config.get("HOST") or "localhost",
        "port": str(config.get("PORT") or ""),
        "user": config.get("USER") or "",
    }
    try:
        conn.ensure_connection()
        data["ok"] = True
        data["tables"] = len(conn.introspection.table_names())
        size = _db_size(conn)
        if size is not None:
            data["size_bytes"] = size
    except Exception as exc:
        data["ok"] = False
        data["error"] = str(exc)
    return data
