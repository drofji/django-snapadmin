"""
snapadmin/sharding/router.py

``SnapAdminRouter`` — the ``DATABASE_ROUTERS`` entry that picks a database
per query once ``SNAPADMIN_SHARDING`` is enabled. Registered automatically by
:func:`snapadmin.sharding.registration.configure_sharding`; never add it to
``DATABASE_ROUTERS`` yourself.

**A model must opt in.** A model is only ever shard-routed if it declares a
``shard_key`` — a class attribute (or, for a plain model, the matching
:func:`snapadmin.models.snap_model` keyword), resolved through
:func:`snapadmin.registry.get_model_meta` exactly like
``tenant_scoped`` (:mod:`snapadmin.tenancy`). ``shard_key`` is either the
name of the field to shard by (``shard_key = "user_id"``), or ``True`` to use
the project-wide default (``SNAPADMIN_SHARDING['SHARD_KEY']``, itself
defaulting to ``"id"``). Every other model — including Django's own
``auth``/``sessions``/``admin`` tables, and any project model that never sets
``shard_key`` — is untouched: both routing methods return ``None`` for it, so
Django resolves it onto ``'default'`` exactly as before. This mirrors the
package's "opt-in, never retrofitted onto an existing model" rule the whole
way through (see ``snapadmin.tenancy``'s module docstring for the same
argument made about tenant scoping).

Routing order, once a model has opted in and the shard key's *value* is
resolvable (from ``hints['instance']`` or a same-named hint — otherwise the
router has no opinion and returns ``None``, the same as an un-opted-in
model — a bare, keyless queryset cannot be routed by a router alone):

1. A forced target (``snap_target`` — see :mod:`snapadmin.sharding.state`)
   always wins.
2. ``snap_master_only`` forces the shard's primary, bypassing replica
   selection and failover alike.
3. ``db_for_write`` always targets the shard's primary — unless it is down
   and ``HA_SETTINGS['AUTO_FAILOVER']`` is ``True``, in which case the first
   reachable replica is promoted (logged and ``UserWarning``-flagged) so a
   write is never silently lost to "the primary happened to be down".
   **Off by default, and deliberately so:** this is only safe against a
   replica that can genuinely be promoted. A read-only standby rejects the
   write regardless (PostgreSQL raises ``cannot execute INSERT in a
   read-only transaction``, which reads as an application bug rather than a
   downed primary), and a replica that *does* accept writes — MySQL without
   ``super_read_only`` — diverges from the primary and loses those rows once
   replication resumes. Note the asymmetry with ``allow_migrate`` below,
   which refuses to migrate a replica outright: a database that cannot take
   a migration cannot take a write either unless it has been promoted first.
4. ``db_for_read`` picks a live replica per ``REPLICA_SELECTION`` (``random``
   / ``round_robin`` / ``first_available``); with every replica down (or none
   configured), it falls back to the primary — unless
   ``HA_SETTINGS['FALLBACK_TO_PRIMARY']`` is ``False``, in which case it
   raises :class:`ShardUnavailable` rather than silently loading the primary
   with traffic replicas exist specifically to shield it from.
"""

from __future__ import annotations

import random
import warnings
import zlib
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.utils.module_loading import import_string

from snapadmin.logging_config import get_logger
from snapadmin.registry import get_model_meta
from snapadmin.sharding import health, state
from snapadmin.sharding.registration import (
    ShardConfig,
    get_shards,
    get_sharding_config,
    is_sharding_enabled,
)

logger = get_logger(__name__)

#: shard_name -> index of the next alias to try in that shard's
#: ``replica_aliases`` for ``REPLICA_SELECTION == "round_robin"``.
_round_robin_positions: dict[str, int] = {}


class ShardResolutionError(Exception):
    """A query's shard key could not be resolved to a configured shard at all.

    Covers an unrecognised ``STRATEGY``, a ``range`` strategy value that no
    shard's ``RANGE`` covers, a ``CUSTOM_ROUTER_FUNC`` naming an unknown
    shard, a ``snap_target(shard=...)`` naming an unknown shard, and a
    ``modulo``/``range`` strategy handed a shard key value that is not
    ``int()``-coercible (shard by a string key with ``STRATEGY = "hash"``).
    """


class ShardUnavailable(Exception):
    """Every replica for a shard is down and ``FALLBACK_TO_PRIMARY`` is ``False``."""


def _shard_key_field(model: type[Model]) -> str | None:
    """The field name ``model`` shards by, or ``None`` if it never opted in."""
    declared = get_model_meta(model, "shard_key", None)
    if not declared:
        return None
    if declared is True:
        return str(get_sharding_config().get("SHARD_KEY", "id"))
    return str(declared)


def _shard_key_value(field_name: str, hints: dict[str, Any]) -> Any:
    """The shard key's actual value for this call, or ``None`` if unresolvable.

    Read from ``hints['instance']`` (what Django passes for a ``save()``/
    delete-style operation) first, then a same-named hint (for a caller that
    passes one explicitly) — otherwise there is nothing to route on, which is
    the normal case for a bare, keyless queryset.
    """
    instance = hints.get("instance")
    if instance is not None:
        return getattr(instance, field_name, None)
    return hints.get(field_name)


def _round_robin_pick(shard_name: str, all_aliases: tuple[str, ...], live: set[str]) -> str:
    # `live` is always a non-empty subset of `all_aliases` — the caller
    # (SnapAdminRouter._read_alias) only reaches this once it has confirmed
    # at least one alias is up — so this always finds a match for some
    # offset in [0, count).
    count = len(all_aliases)
    start = _round_robin_positions.get(shard_name, 0)
    offset = next(o for o in range(count) if all_aliases[(start + o) % count] in live)
    _round_robin_positions[shard_name] = (start + offset + 1) % count
    return all_aliases[(start + offset) % count]


class SnapAdminRouter:
    """``DATABASE_ROUTERS`` entry that routes an opted-in model's reads/writes."""

    def _shard_for(self, model: type[Model], hints: dict[str, Any]) -> ShardConfig | None:
        field_name = _shard_key_field(model)
        if field_name is None:
            return None
        value = _shard_key_value(field_name, hints)
        if value is None:
            return None
        return self._shard_for_value(value, field_name=field_name)

    def _shard_for_value(self, value: Any, *, field_name: str | None = None) -> ShardConfig:
        # get_shards() always yields at least one shard when it does not
        # raise (both _build_explicit_shards and _build_auto_shards are
        # constructed that way — see snapadmin.sharding.registration), so
        # there is no "zero shards" case to guard here.
        strategy = get_sharding_config().get("STRATEGY", "modulo")
        ordered = list(get_shards().values())

        if strategy == "modulo":
            try:
                return ordered[int(value) % len(ordered)]
            except (TypeError, ValueError) as exc:
                raise ShardResolutionError(
                    self._non_numeric_key_message(field_name, strategy, value)
                ) from exc
        if strategy == "hash":
            return ordered[zlib.crc32(str(value).encode()) % len(ordered)]
        if strategy == "range":
            for shard in ordered:
                if shard.value_range is None:
                    continue
                try:
                    in_range = shard.value_range[0] <= int(value) <= shard.value_range[1]
                except (TypeError, ValueError) as exc:
                    raise ShardResolutionError(
                        self._non_numeric_key_message(field_name, strategy, value)
                    ) from exc
                if in_range:
                    return shard
            raise ShardResolutionError(f"No shard's RANGE covers value {value!r}.")
        if strategy == "custom":
            return self._custom_shard(value)
        raise ShardResolutionError(f"Unknown SNAPADMIN_SHARDING STRATEGY {strategy!r}.")

    @staticmethod
    def _non_numeric_key_message(field_name: str | None, strategy: str, value: Any) -> str:
        # Deliberately omits `value` itself: a shard key is routinely a
        # natural key (an email address, a UUID) and this message reaches
        # logs and 500 pages, not just a developer's console.
        field_label = f"{field_name!r}" if field_name else "the shard key"
        return (
            f"Shard key {field_label} could not be coerced to int() for "
            f"SNAPADMIN_SHARDING STRATEGY {strategy!r} — its value is of type "
            f"{type(value).__name__!r}, not an integer. Set STRATEGY = 'hash' "
            "instead to shard by a non-integer key."
        )

    def _custom_shard(self, value: Any) -> ShardConfig:
        func_path = get_sharding_config().get("CUSTOM_ROUTER_FUNC")
        if not func_path:
            raise ShardResolutionError(
                "SNAPADMIN_SHARDING['STRATEGY'] == 'custom' but 'CUSTOM_ROUTER_FUNC' is not set."
            )
        shard_name = import_string(func_path)(value)
        shards = get_shards()
        if shard_name not in shards:
            raise ShardResolutionError(
                f"CUSTOM_ROUTER_FUNC {func_path!r} returned {shard_name!r}, which is not a "
                f"configured shard ({sorted(shards)})."
            )
        return shards[shard_name]

    def _require_shard(self, shard_name: str) -> ShardConfig:
        shards = get_shards()
        if shard_name not in shards:
            raise ShardResolutionError(
                f"snap_target() named shard {shard_name!r}, which is not a configured shard "
                f"({sorted(shards)})."
            )
        return shards[shard_name]

    def _write_alias(self, shard: ShardConfig) -> str:
        ha_settings = get_sharding_config().get("HA_SETTINGS") or {}
        auto_failover = bool(ha_settings.get("AUTO_FAILOVER", False))
        if not auto_failover or health.is_alive(shard.primary_alias):
            return shard.primary_alias

        for alias in shard.replica_aliases:
            if health.is_alive(alias):
                message = (
                    f"SnapAdmin: shard {shard.name!r} primary is down — "
                    f"failing writes over to replica {alias!r}."
                )
                logger.error(
                    "snap_shard_failover", shard=shard.name,
                    primary=shard.primary_alias, promoted=alias,
                )
                warnings.warn(message, UserWarning, stacklevel=3)
                return alias

        # Nothing reachable at all — surface the primary anyway, so Django's
        # own connection error is what the caller sees, not a silent success.
        return shard.primary_alias

    def _read_alias(self, shard: ShardConfig) -> str:
        if not shard.replica_aliases:
            return shard.primary_alias

        live = {alias for alias in shard.replica_aliases if health.is_alive(alias)}
        if not live:
            ha_settings = get_sharding_config().get("HA_SETTINGS") or {}
            if not bool(ha_settings.get("FALLBACK_TO_PRIMARY", True)):
                raise ShardUnavailable(
                    f"SnapAdmin: every replica for shard {shard.name!r} is down and "
                    "HA_SETTINGS['FALLBACK_TO_PRIMARY'] is False."
                )
            logger.error("snap_shard_all_replicas_down", shard=shard.name, fallback="primary")
            warnings.warn(
                f"SnapAdmin: every replica for shard {shard.name!r} is down — "
                "falling back to primary for reads.",
                UserWarning, stacklevel=3,
            )
            return shard.primary_alias

        selection = get_sharding_config().get("REPLICA_SELECTION", "round_robin")
        if selection == "random":
            return random.choice(list(live))
        if selection == "first_available":
            for alias in shard.replica_aliases:
                if alias in live:
                    return alias
        return _round_robin_pick(shard.name, shard.replica_aliases, live)

    def db_for_write(self, model: type[Model], **hints: Any) -> str | None:
        if not is_sharding_enabled():
            return None

        target = state.get_forced_target()
        if target is not None:
            shard_name, _use_replica = target
            # Writes always target the named shard's primary — a replica
            # can never accept a write.
            return self._require_shard(shard_name).primary_alias

        shard = self._shard_for(model, hints)
        if shard is None:
            return None
        if state.is_master_forced():
            return shard.primary_alias
        return self._write_alias(shard)

    def db_for_read(self, model: type[Model], **hints: Any) -> str | None:
        if not is_sharding_enabled():
            return None

        target = state.get_forced_target()
        if target is not None:
            shard_name, use_replica = target
            shard = self._require_shard(shard_name)
            return self._read_alias(shard) if use_replica else shard.primary_alias

        shard = self._shard_for(model, hints)
        if shard is None:
            return None
        if state.is_master_forced():
            return shard.primary_alias
        return self._read_alias(shard)

    def allow_relation(self, obj1: Model, obj2: Model, **hints: Any) -> bool | None:
        if not is_sharding_enabled():
            return None
        # Only speak for a pair this router could actually have routed. `True`
        # is not "no opinion" — it actively suppresses Django's own
        # cross-database relation check, so returning it for two models that
        # never declared `shard_key` would silence that check project-wide the
        # moment sharding is switched on, hiding an unrelated bug that puts two
        # objects on different databases. `None` leaves Django's default in
        # place for everyone else, matching how both routing methods already
        # bow out for an un-opted-in model.
        if (
            _shard_key_field(type(obj1)) is None
            and _shard_key_field(type(obj2)) is None
        ):
            return None
        # Cross-shard relations are the project's own concern to manage (a
        # ForeignKey does not enforce referential integrity across separate
        # physical databases regardless of what this returns) — SnapAdmin
        # takes no position on it rather than refusing a relation a project
        # may have deliberately designed to span shards.
        return True

    def allow_migrate(
        self, db: str, app_label: str, model_name: str | None = None, **hints: Any
    ) -> bool | None:
        if not is_sharding_enabled():
            return None
        try:
            shards = get_shards()
        except ImproperlyConfigured:
            # allow_migrate() is consulted for every app on every `manage.py`
            # invocation, sharded or not — a broken SNAPADMIN_SHARDING value
            # must not block migrations project-wide. check_sharding_config
            # (snapadmin.E013) already reports the same problem loudly.
            return None
        for shard in shards.values():
            if db in shard.replica_aliases:
                # Replication propagates schema at the DB layer — a replica
                # must never receive a migration directly.
                return False
        return None  # no opinion on a primary alias — Django's default applies
