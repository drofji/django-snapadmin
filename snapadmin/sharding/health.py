"""
snapadmin/sharding/health.py

A cheap, cached TCP reachability probe backing
:class:`~snapadmin.sharding.router.SnapAdminRouter`'s failover (primary down
-> promote a replica) and fallback (every replica down -> read from primary)
decisions.

Deliberately shallow: a successful socket connect proves the host is
reachable on the right port, not that Postgres/MySQL is actually accepting
queries or that the named database exists — a full connection attempt per
health check would be far more expensive than the query it is guarding, and
would itself contend with the connection pool it is meant to protect.
Results are cached for a short TTL so failover logic never opens a socket on
every single query.
"""

from __future__ import annotations

import socket
import time

from snapadmin.logging_config import get_logger
from snapadmin.sharding.registration import get_sharding_config

logger = get_logger(__name__)

#: How long a health-check result stays valid before being re-probed.
CACHE_TTL_SECONDS = 5.0

#: Fallback port per Django DB engine, used only when a DSN's own DATABASES
#: entry carries no explicit PORT.
_DEFAULT_PORT_BY_ENGINE = {
    "django.db.backends.postgresql": 5432,
    "django.db.backends.mysql": 3306,
}

#: alias -> (checked_at monotonic time, is_alive).
_cache: dict[str, tuple[float, bool]] = {}


def _health_check_timeout() -> float:
    ha_settings = get_sharding_config().get("HA_SETTINGS") or {}
    try:
        return float(ha_settings.get("HEALTH_CHECK_TIMEOUT", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _default_port(engine: str) -> int:
    return _DEFAULT_PORT_BY_ENGINE.get(engine, 0)


def _probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_alive(alias: str) -> bool:
    """Whether ``alias`` (a ``settings.DATABASES`` key) is currently reachable.

    Cached per alias for :data:`CACHE_TTL_SECONDS` so failover/fallback logic
    never pays a socket round-trip on every query. An alias with no ``HOST``
    (e.g. SQLite) is always considered alive — there is nothing to probe. An
    alias that is not in ``settings.DATABASES`` at all is never alive.
    """
    from django.conf import settings

    now = time.monotonic()
    cached = _cache.get(alias)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    db = settings.DATABASES.get(alias)
    if db is None:
        _cache[alias] = (now, False)
        return False

    host = db.get("HOST") or ""
    if not host:
        _cache[alias] = (now, True)
        return True

    port = int(db.get("PORT") or _default_port(db.get("ENGINE", "")))
    alive = _probe(host, port, _health_check_timeout())
    if not alive:
        logger.error("snap_shard_host_down", db_alias=alias, host=host, port=port)
    _cache[alias] = (now, alive)
    return alive


def reset_cache() -> None:
    """Clear every cached health result — for tests, or to force a re-probe."""
    _cache.clear()
