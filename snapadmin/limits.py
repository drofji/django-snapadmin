"""
snapadmin/limits.py

A reusable, cache-backed quota primitive.

``SnapAnonRateThrottle`` / ``SnapUserRateThrottle`` (:mod:`snapadmin.api.views`)
give a project global, DRF-level throttling — one rate for every anonymous
caller, one for every authenticated caller. What projects keep rebuilding on
top of that is something narrower: a **per-tenant or per-token quota across
several time windows at once** (a cap per second *and* per minute *and* per
day), a **concurrency cap** (no more than N of this key's operations in
flight at once), and a **cooldown** after an upstream service answers 429 —
including for calls the project makes *outbound* to a third party, which DRF's
request-scoped throttles have no way to express at all. This module is that
primitive, with no opinion about what ``key`` means, so it serves both an
inbound API guard and an outbound client alike::

    from snapadmin.limits import reserve

    with reserve(f"tenant:{tenant_id}", windows={60: 100, 3600: 1000}, concurrency=5) as slot:
        if not slot.allowed:
            return too_many_requests(retry_after=slot.retry_after)
        call_the_upstream_api()

Design notes
------------
* **Fixed windows, not a sliding log.** Each ``(period_seconds, limit)`` pair
  in ``windows`` keys a counter to the current ``period_seconds``-wide bucket
  (``now // period_seconds``) via the project's cache — the same
  increment-with-a-timeout idiom DRF's own ``SimpleRateThrottle`` uses, just
  generalised to arbitrary keys and any number of simultaneous windows. A
  fixed window can under- or over-count by up to ``limit`` right at a bucket
  boundary (the classic trade-off of this shape); a true sliding-window log
  would cost one cache entry per request instead of one per window, which is
  the wrong trade for a primitive meant to be cheap enough to call on every
  request.
* **Concurrency is a counted semaphore**, incremented on a successful
  :func:`reserve` and decremented when the returned :class:`Reservation` is
  released (``with reserve(...):`` releases automatically). The counter
  itself carries a timeout (:data:`CONCURRENCY_TIMEOUT_SECONDS`) so a holder
  that crashes before releasing does not leak its slot forever.
* **Cooldown is a separate, explicit signal** (:func:`cooldown`) — nothing in
  :func:`reserve` ever sets one on its own, because only the caller knows an
  upstream actually answered 429. :func:`reserve` checks it first and fails
  fast, before touching either a window or the concurrency counter.
* **Honest about the cache it runs on.** Every counter here is exactly as
  reliable as the configured cache backend and *nothing* more:

  * **Counters are per-process unless ``CACHES`` points at a shared backend**
    (Redis, Memcached, a database cache — anything reachable from every
    worker). The default ``LocMemCache`` is private to one process, so four
    Gunicorn workers on ``LocMemCache`` each enforce the configured limit
    independently — the *effective* ceiling for the key is four times what
    was configured, not the number in ``windows``. Point ``SNAPADMIN_LIMITS_CACHE_ALIAS``
    at a shared cache before trusting this in a multi-process deployment.
  * **A cache that evicts a counter under memory pressure fails open, not
    closed.** An evicted window counter is indistinguishable from one that
    never existed, so the next :func:`reserve` call sees a fresh bucket and
    allows the request. This is the same trade-off any cache-backed rate
    limiter makes (including DRF's own throttles); the alternative — treating
    a missing counter as "limit reached" — would turn ordinary cache pressure
    into an outage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from django.core.cache import BaseCache, caches

from snapadmin.conf import get_setting
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: How long a concurrency slot survives with no matching release before it
#: expires on its own — the safety valve against a crashed holder leaking a
#: slot forever. Overridable per call via ``reserve(..., concurrency_timeout=...)``.
CONCURRENCY_TIMEOUT_SECONDS = 300

#: Cache-key prefix, namespacing this module's keys away from anything else a
#: project stores in the same cache.
_PREFIX = "snapadmin:limits"


def _cache_alias() -> str:
    return str(get_setting("SNAPADMIN_LIMITS_CACHE_ALIAS", "default"))


def _cache() -> BaseCache:
    return caches[_cache_alias()]


def _window_cache_key(key: str, period_seconds: int) -> str:
    bucket = int(time.time() // period_seconds)
    return f"{_PREFIX}:window:{period_seconds}:{bucket}:{key}"


def _concurrency_cache_key(key: str) -> str:
    return f"{_PREFIX}:concurrency:{key}"


def _cooldown_cache_key(key: str) -> str:
    return f"{_PREFIX}:cooldown:{key}"


def _increment(cache: BaseCache, cache_key: str, *, timeout: float) -> int:
    """Atomically increment ``cache_key``, creating it at 0 first if absent.

    Mirrors DRF's ``SimpleRateThrottle.cache.incr`` idiom: ``incr()`` is the
    atomic path on a backend that supports it, and ``add()`` only runs on the
    (rare, and racy for one request in a tight window) path where the key does
    not exist yet — a lost race there costs at most one under-count, never a
    crash, which is the same trade-off DRF itself accepts.
    """
    try:
        return cache.incr(cache_key)
    except ValueError:
        cache.add(cache_key, 0, timeout)
        try:
            return cache.incr(cache_key)
        except ValueError:
            # Evicted between the add() and the incr() (a cache under enough
            # pressure to drop a key it just wrote) — start this caller's
            # count at 1 rather than raising. Fails open, per the module's
            # documented cache-pressure trade-off.
            cache.set(cache_key, 1, timeout)
            return 1


def _decrement(cache: BaseCache, cache_key: str) -> None:
    try:
        cache.decr(cache_key)
    except ValueError:
        pass  # Already expired/evicted — nothing to release.


@dataclass
class Reservation:
    """The outcome of one :func:`reserve` call.

    ``allowed`` is the only field a caller must check. ``reason`` and
    ``retry_after`` exist to build a useful response (a ``429`` with
    ``Retry-After``, a backoff before retrying an outbound call) — both are
    ``None`` when ``allowed`` is true. Used as a context manager so a held
    concurrency slot is always released, even if the wrapped work raises::

        with reserve(key, concurrency=5) as slot:
            if slot.allowed:
                do_the_work()
    """

    allowed: bool
    reason: Optional[str] = None
    retry_after: Optional[float] = None
    _cache: Optional[BaseCache] = field(default=None, repr=False, compare=False)
    _concurrency_key: Optional[str] = field(default=None, repr=False, compare=False)
    _released: bool = field(default=False, repr=False, compare=False)

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def release(self) -> None:
        """Free this reservation's concurrency slot, if it holds one.

        Idempotent — calling it twice (explicitly, then again via ``__exit__``)
        decrements only once.
        """
        if self._released or self._cache is None or self._concurrency_key is None:
            return
        _decrement(self._cache, self._concurrency_key)
        self._released = True


def in_cooldown(key: str, *, cache_alias: Optional[str] = None) -> bool:
    """Whether ``key`` is currently in a cooldown set by :func:`cooldown`."""
    cache = caches[cache_alias] if cache_alias else _cache()
    return bool(cache.get(_cooldown_cache_key(key)))


def cooldown(key: str, seconds: float, *, cache_alias: Optional[str] = None) -> None:
    """Block every :func:`reserve` for ``key`` for the next ``seconds``.

    For the case :func:`reserve` cannot see on its own: an upstream call this
    key represents just answered ``429`` (or any other "back off entirely for
    a while" signal). Nothing in this module sets a cooldown automatically —
    only the caller that made the failing call knows it happened.
    """
    cache = caches[cache_alias] if cache_alias else _cache()
    cache.set(_cooldown_cache_key(key), True, seconds)


def reserve(
    key: str,
    windows: Optional[dict[int, int]] = None,
    concurrency: Optional[int] = None,
    *,
    cache_alias: Optional[str] = None,
    concurrency_timeout: float = CONCURRENCY_TIMEOUT_SECONDS,
) -> Reservation:
    """Ask for one operation's worth of quota under ``key``.

    ``windows`` is ``{period_seconds: max_count}`` — e.g.
    ``{1: 5, 60: 100, 3600: 1000}`` caps ``key`` at 5/second *and* 100/minute
    *and* 1000/hour simultaneously; every window must clear for the
    reservation to succeed. ``concurrency``, if given, additionally caps how
    many reservations for ``key`` may be held at once — release the returned
    :class:`Reservation` (or use it as a context manager) when the work
    finishes so the slot frees up.

    Checks run cheapest-first: a cooldown blocks immediately with no window
    or concurrency counters touched; the first window that is already over
    its limit stops the rest from being incremented too (a caller who is
    already rate-limited on the per-second window should not also burn a
    count against their per-day budget for the same rejected call).
    """
    cache = caches[cache_alias] if cache_alias else _cache()

    if in_cooldown(key, cache_alias=cache_alias):
        return Reservation(allowed=False, reason="cooldown", retry_after=None)

    for period_seconds, limit in (windows or {}).items():
        cache_key = _window_cache_key(key, period_seconds)
        count = _increment(cache, cache_key, timeout=period_seconds)
        if count > limit:
            elapsed = time.time() % period_seconds
            return Reservation(
                allowed=False, reason="rate_limited", retry_after=period_seconds - elapsed,
            )

    if concurrency:
        concurrency_key = _concurrency_cache_key(key)
        count = _increment(cache, concurrency_key, timeout=concurrency_timeout)
        if count > concurrency:
            _decrement(cache, concurrency_key)
            return Reservation(allowed=False, reason="concurrency", retry_after=None)
        return Reservation(allowed=True, _cache=cache, _concurrency_key=concurrency_key)

    return Reservation(allowed=True)
