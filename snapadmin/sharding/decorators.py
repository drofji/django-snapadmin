"""
snapadmin/sharding/decorators.py

The public request-scoped routing overrides: ``snap_master_only`` and
``snap_target``. Both work identically as a context manager (``with
snap_master_only(): ...``) and as a decorator (``@snap_master_only()`` /
``@snap_target(shard="shard_2")``) on either a plain sync function or an
``async def`` one — the wrapper is chosen at decoration time by
``inspect.iscoroutinefunction``, so the caller never has to know which form
it is decorating a coroutine function requires.

Built on the ``contextvars`` primitives in :mod:`snapadmin.sharding.state`,
which is what makes both safe under ``asyncio``: a value set in one request's
coroutine is never visible to a concurrently running one on the same thread/
event loop. :class:`~snapadmin.sharding.router.SnapAdminRouter` reads this
state before its normal shard/replica-selection logic on every call.

Both classes create a **fresh instance per invocation** when used as a
decorator, so a decorated function called many times (or concurrently) never
shares one entered/exited context — each call gets its own
``__enter__``/``__exit__`` pair, exactly as if it had written the ``with``
block itself.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, TypeVar

from snapadmin.sharding import state

_F = TypeVar("_F", bound=Callable[..., Any])


class _ForcedRoutingContext(AbstractContextManager):
    """Shared decorator support for :class:`snap_master_only` / :class:`snap_target`.

    Subclasses implement ``__enter__``/``__exit__`` (the actual state change)
    and record their own constructor arguments as ``_init_args``/
    ``_init_kwargs`` so :meth:`__call__` can build a fresh instance per call.
    """

    _init_args: tuple[Any, ...] = ()
    _init_kwargs: dict[str, Any] = {}

    def _new(self) -> "_ForcedRoutingContext":
        return type(self)(*self._init_args, **self._init_kwargs)

    def __call__(self, func: _F) -> _F:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self._new():
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with self._new():
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]


class snap_master_only(_ForcedRoutingContext):
    """Force every read *and* write in scope onto each shard's primary.

    Ignores replicas and failover alike for the duration of the block/call —
    the one escape hatch for code that must see its own just-written data
    immediately (replication lag would otherwise make a read-after-write
    unreliable)::

        with snap_master_only():
            Order.objects.filter(id=order_id).update(status="paid")
            assert Order.objects.get(id=order_id).status == "paid"  # no lag

        @snap_master_only()
        def reconcile_payment(order_id):
            ...

        @snap_master_only()
        async def reconcile_payment_async(order_id):
            ...

    Also usable bare, without calling it first — ``@snap_master_only`` reads
    the same as ``@snap_master_only()``::

        @snap_master_only
        def reconcile_payment(order_id):
            ...
    """

    def __new__(cls, func: Callable[..., Any] | None = None) -> Any:
        if func is not None:
            # Bare `@snap_master_only` usage: build a real instance, use it
            # as a decorator immediately, and hand back the wrapped function
            # — never an instance of this class, so __init__ is not invoked
            # again for it.
            return cls()(func)
        return super().__new__(cls)

    def __enter__(self) -> "snap_master_only":
        self._token = state.set_master_forced(True)
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        state.reset_master_forced(self._token)
        return False


class snap_target(_ForcedRoutingContext):
    """Force every read/write in scope onto one named shard.

    ``replica=False`` (the default) always targets that shard's primary;
    ``replica=True`` routes reads through the shard's normal replica-
    selection logic (writes always go to the primary regardless, since a
    replica can never accept one)::

        with snap_target(shard="shard_2"):
            Order.objects.create(...)  # lands on shard_2, whatever its
                                        # shard key would normally resolve to

        @snap_target(shard="shard_2", replica=True)
        def read_from_shard_2_replica():
            return list(Order.objects.all())

    :param shard: the shard's configured name (a key of
        ``SNAPADMIN_SHARDING['SHARDS']``, or ``shard_1``/``shard_2``/… for
        the auto-distributed ``DATABASES`` list) — unknown names raise
        :class:`~snapadmin.sharding.router.ShardResolutionError` the moment
        a query actually runs, not when the decorator is applied.
    :param replica: whether a read may use the shard's replicas.
    """

    def __init__(self, *, shard: str, replica: bool = False) -> None:
        self._init_args = ()
        self._init_kwargs = {"shard": shard, "replica": replica}
        self._shard = shard
        self._replica = replica

    def __enter__(self) -> "snap_target":
        self._token = state.set_forced_target(self._shard, self._replica)
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        state.reset_forced_target(self._token)
        return False
