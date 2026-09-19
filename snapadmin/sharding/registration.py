"""
snapadmin/sharding/registration.py

Parses ``SNAPADMIN_SHARDING`` and, when enabled, injects the resulting DSNs
into ``django.conf.settings.DATABASES`` and registers
:class:`snapadmin.sharding.router.SnapAdminRouter` in
``settings.DATABASE_ROUTERS``. :func:`configure_sharding` is called once from
``SnapAdminConfig.ready()`` (``snapadmin/apps.py``) — call it yourself only
from a test that wants to re-run registration against patched settings.

Two configuration shapes resolve to the same internal :class:`ShardConfig`
list — see ``docs/index.html#sharding`` for the full settings reference:

- **Mode A (auto)** — a flat ``DATABASES`` list of DSNs, sliced into shards
  automatically per ``SHARDING_ENABLED`` / ``MIRRORING_ENABLED`` /
  ``REPLICAS_PER_SHARD``.
- **Mode B (explicit)** — a ``SHARDS`` mapping naming exactly which DSN is
  which shard's primary/replica. Wins outright over ``DATABASES`` when both
  are set.

A DSN is parsed with ``urllib.parse`` only — no new dependency — so this
subpackage adds no runtime dependency to the base install.

Misconfiguration (an unparseable DSN, a ``DATABASES`` list that does not
slice evenly, a shard with no ``PRIMARY``) degrades to sharding staying
effectively off rather than crashing ``django.setup()`` on every deploy,
matching every other optional integration ``SnapAdminConfig.ready()`` wires
up (see that module's docstring): :func:`configure_sharding` logs
``logger.error(...)`` and returns without touching ``DATABASES``. The
matching system checks in ``snapadmin/checks.py``
(``snapadmin.E013``-``snapadmin.E016``) surface the same problem loudly
through ``manage.py check``/CI instead of only a quiet log line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from snapadmin.conf import get_setting
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: Dotted path appended to ``settings.DATABASE_ROUTERS`` once sharding is enabled.
SHARDING_ROUTER_PATH = "snapadmin.sharding.router.SnapAdminRouter"

#: DSN scheme -> Django ``ENGINE``. Only the backends sharding actually supports.
_ENGINE_BY_SCHEME: dict[str, str] = {
    "postgres": "django.db.backends.postgresql",
    "postgresql": "django.db.backends.postgresql",
    "mysql": "django.db.backends.mysql",
}

#: Valid ``SNAPADMIN_SHARDING['STRATEGY']`` values.
STRATEGIES: tuple[str, ...] = ("modulo", "hash", "range", "custom")

#: Valid ``SNAPADMIN_SHARDING['REPLICA_SELECTION']`` values.
REPLICA_SELECTIONS: tuple[str, ...] = ("random", "round_robin", "first_available")


@dataclass(frozen=True)
class ShardConfig:
    """One shard's resolved primary/replica DSNs and their ``DATABASES`` aliases."""

    name: str
    primary_dsn: str
    replica_dsns: tuple[str, ...] = ()
    value_range: tuple[int, int] | None = None

    @property
    def primary_alias(self) -> str:
        return f"snapadmin_{self.name}_primary"

    @property
    def replica_aliases(self) -> tuple[str, ...]:
        return tuple(f"snapadmin_{self.name}_replica_{i}" for i in range(len(self.replica_dsns)))


def get_sharding_config() -> dict[str, Any]:
    """The raw ``SNAPADMIN_SHARDING`` dict, or ``{}`` if unset."""
    return get_setting("SNAPADMIN_SHARDING", None) or {}


def is_sharding_enabled() -> bool:
    """Whether ``SNAPADMIN_SHARDING`` is configured and turned on."""
    return bool(get_sharding_config().get("ENABLED"))


def redact_dsn(dsn: str) -> str:
    """``dsn`` with any password blanked out — safe to log or raise in an error message."""
    try:
        parsed = urlsplit(dsn)
    except ValueError:
        return "<unparseable DSN>"
    if not parsed.password:
        return dsn
    netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
    return parsed._replace(netloc=netloc).geturl()


def parse_dsn(dsn: str) -> dict[str, str]:
    """Parse one ``scheme://user:pass@host:port/name`` DSN into a Django ``DATABASES`` entry.

    Uses only ``urllib.parse`` (stdlib) — no new dependency.

    :raises ImproperlyConfigured: the DSN cannot be parsed, its scheme is not
        one of ``postgres``/``postgresql``/``mysql``, or it is missing a host
        or database name. The message never includes the DSN's password (see
        :func:`redact_dsn`).
    """
    try:
        parsed = urlsplit(dsn)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except ValueError as exc:
        # urlsplit() itself raises on a malformed IPv6 host; a non-numeric
        # port only raises once .port is actually accessed, above — both are
        # "could not parse this DSN", not two different failure modes.
        raise ImproperlyConfigured(
            f"SNAPADMIN_SHARDING: could not parse DSN {redact_dsn(dsn)!r}: {exc}"
        ) from exc

    engine = _ENGINE_BY_SCHEME.get(scheme)
    if engine is None:
        raise ImproperlyConfigured(
            f"SNAPADMIN_SHARDING: unsupported database scheme {scheme!r} in "
            f"{redact_dsn(dsn)!r} — use one of {sorted(_ENGINE_BY_SCHEME)}."
        )
    if not hostname or not parsed.path.lstrip("/"):
        raise ImproperlyConfigured(
            f"SNAPADMIN_SHARDING: DSN {redact_dsn(dsn)!r} is missing a host or database name."
        )

    return {
        "ENGINE": engine,
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(username) if username else "",
        "PASSWORD": unquote(password) if password else "",
        "HOST": hostname,
        "PORT": str(port) if port else "",
    }


def _validate_range(shard_name: str, value_range: Any) -> tuple[int, int] | None:
    if value_range is None:
        return None
    try:
        low, high = value_range
        low, high = int(low), int(high)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(
            f"SNAPADMIN_SHARDING['SHARDS'][{shard_name!r}]['RANGE'] must be a "
            "(low, high) pair of integers."
        ) from exc
    if low > high:
        raise ImproperlyConfigured(
            f"SNAPADMIN_SHARDING['SHARDS'][{shard_name!r}]['RANGE'] = ({low}, {high}) "
            "— low is greater than high."
        )
    return (low, high)


def _build_explicit_shards(shards_raw: dict[str, Any]) -> dict[str, ShardConfig]:
    """Mode B — an explicit ``SHARDS`` mapping naming each shard's DSNs directly."""
    shards: dict[str, ShardConfig] = {}
    for raw_name, spec in shards_raw.items():
        name = str(raw_name)
        if not isinstance(spec, dict) or not spec.get("PRIMARY"):
            raise ImproperlyConfigured(
                f"SNAPADMIN_SHARDING['SHARDS'][{name!r}] needs a 'PRIMARY' DSN."
            )
        replicas = tuple(spec.get("REPLICAS") or [])
        value_range = _validate_range(name, spec.get("RANGE"))
        shards[name] = ShardConfig(
            name=name,
            primary_dsn=spec["PRIMARY"],
            replica_dsns=replicas,
            value_range=value_range,
        )
    return shards


def _build_auto_shards(raw: dict[str, Any]) -> dict[str, ShardConfig]:
    """Mode A — a flat ``DATABASES`` list, auto-sliced into shards/replicas."""
    databases = list(raw.get("DATABASES") or [])
    if not databases:
        raise ImproperlyConfigured(
            "SNAPADMIN_SHARDING['ENABLED'] is True but neither 'SHARDS' nor "
            "'DATABASES' is configured."
        )

    sharding_enabled = bool(raw.get("SHARDING_ENABLED", True))
    mirroring_enabled = bool(raw.get("MIRRORING_ENABLED", False))
    replicas_per_shard = int(raw.get("REPLICAS_PER_SHARD", 0)) if mirroring_enabled else 0

    if not sharding_enabled:
        # Exactly one shard: pure replication ("mirroring"), no partitioning.
        primary, *rest = databases
        replicas = tuple(rest) if mirroring_enabled else ()
        return {"shard_1": ShardConfig(name="shard_1", primary_dsn=primary, replica_dsns=replicas)}

    group_size = 1 + replicas_per_shard
    if len(databases) % group_size:
        raise ImproperlyConfigured(
            f"SNAPADMIN_SHARDING['DATABASES'] has {len(databases)} entries, which does "
            f"not divide evenly into groups of {group_size} (1 primary + "
            f"{replicas_per_shard} replica(s) per shard, from REPLICAS_PER_SHARD). "
            "Add/remove DSNs, or set an explicit 'SHARDS' mapping instead."
        )

    shards: dict[str, ShardConfig] = {}
    for index, start in enumerate(range(0, len(databases), group_size), start=1):
        group = databases[start : start + group_size]
        name = f"shard_{index}"
        shards[name] = ShardConfig(name=name, primary_dsn=group[0], replica_dsns=tuple(group[1:]))
    return shards


def get_shards() -> dict[str, ShardConfig]:
    """Every configured shard, resolved from either configuration mode.

    A non-empty ``SHARDS`` mapping wins outright (Mode B, explicit).
    Otherwise a flat ``DATABASES`` list is auto-sliced per
    ``SHARDING_ENABLED`` / ``MIRRORING_ENABLED`` / ``REPLICAS_PER_SHARD``
    (Mode A).

    :raises ImproperlyConfigured: the configured shape cannot be resolved
        into shards at all (see :func:`_build_explicit_shards` /
        :func:`_build_auto_shards`). A caller that only needs a best-effort
        read (a system check) should catch this itself.
    """
    raw = get_sharding_config()
    explicit = raw.get("SHARDS") or {}
    if explicit:
        return _build_explicit_shards(explicit)
    return _build_auto_shards(raw)


def iter_primary_aliases() -> list[str]:
    """Every shard's primary ``DATABASES`` alias, sorted by shard name.

    Used by ``manage.py snap_migrate`` — migrations only ever target a
    primary, never a replica (replication propagates schema at the DB layer).
    Returns an empty list when sharding is off or misconfigured.
    """
    if not is_sharding_enabled():
        return []
    try:
        shards = get_shards()
    except ImproperlyConfigured:
        return []
    return [shard.primary_alias for _, shard in sorted(shards.items())]


def configure_sharding() -> None:
    """Inject every shard's DSNs into ``settings.DATABASES`` and register the
    sharding router — a no-op unless ``SNAPADMIN_SHARDING['ENABLED']`` is ``True``.

    Idempotent: safe to call more than once (autoreload re-runs
    ``AppConfig.ready()``) — every alias is simply overwritten with the same
    value, and the router path is only appended to ``DATABASE_ROUTERS`` if it
    is not already present.

    Degrades rather than raising on misconfiguration — see the module
    docstring for why.
    """
    if not is_sharding_enabled():
        return

    try:
        shards = get_shards()
        aliases: dict[str, dict[str, str]] = {}
        for shard in shards.values():
            aliases[shard.primary_alias] = parse_dsn(shard.primary_dsn)
            for alias, dsn in zip(shard.replica_aliases, shard.replica_dsns, strict=True):
                aliases[alias] = parse_dsn(dsn)
    except ImproperlyConfigured as exc:
        logger.error("snap_sharding_misconfigured", error=str(exc))
        return

    settings.DATABASES.update(aliases)
    if SHARDING_ROUTER_PATH not in settings.DATABASE_ROUTERS:
        settings.DATABASE_ROUTERS = [*settings.DATABASE_ROUTERS, SHARDING_ROUTER_PATH]

    logger.info(
        "snap_sharding_configured",
        shards=sorted(shards),
        strategy=get_sharding_config().get("STRATEGY", "modulo"),
        aliases=sorted(aliases),
    )
