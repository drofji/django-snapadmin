"""
snapadmin/sharding/state.py

``contextvars``-based request-scoped routing overrides — the low-level state
machinery behind the public ``snap_master_only`` / ``snap_target`` API in
:mod:`snapadmin.sharding.decorators`. :class:`~snapadmin.sharding.router.SnapAdminRouter`
consults this module before its normal shard/replica-selection logic runs.

``contextvars`` rather than ``threading.local`` deliberately: a
``ContextVar`` is copied into every new ``asyncio`` task automatically, so a
value set in one coroutine never leaks into a concurrently running one on the
same thread/event loop — ``threading.local`` gives no such guarantee under
async code, since many tasks can share one OS thread.
"""

from __future__ import annotations

import contextvars

#: ``True`` while a ``snap_master_only`` block is active — forces every read
#: *and* write in scope onto the primary, bypassing replica selection and
#: failover alike.
_force_master: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "snapadmin_sharding_force_master", default=False
)

#: ``(shard_name, use_replica)`` while a ``snap_target`` block is active, or
#: ``None`` outside one — forces every read/write in scope onto the named
#: shard: its primary when ``use_replica`` is ``False``, or through the
#: normal replica-selection logic for that shard when ``True``.
_force_target: contextvars.ContextVar[tuple[str, bool] | None] = contextvars.ContextVar(
    "snapadmin_sharding_force_target", default=None
)


def is_master_forced() -> bool:
    """Whether a ``snap_master_only`` block is currently active."""
    return _force_master.get()


def set_master_forced(value: bool) -> contextvars.Token:
    """Set the master-forced flag, returning a token for :func:`reset_master_forced`."""
    return _force_master.set(value)


def reset_master_forced(token: contextvars.Token) -> None:
    """Undo one :func:`set_master_forced` call."""
    _force_master.reset(token)


def get_forced_target() -> tuple[str, bool] | None:
    """The currently forced ``(shard_name, use_replica)``, or ``None``."""
    return _force_target.get()


def set_forced_target(shard: str, replica: bool) -> contextvars.Token:
    """Force routing onto ``shard``, returning a token for :func:`reset_forced_target`."""
    return _force_target.set((shard, replica))


def reset_forced_target(token: contextvars.Token) -> None:
    """Undo one :func:`set_forced_target` call."""
    _force_target.reset(token)
