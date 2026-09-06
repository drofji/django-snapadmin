"""
snapadmin/sharding

Optional, declarative multi-shard/read-replica database routing (#SHARD1).

Entirely inert unless a project sets
``SNAPADMIN_SHARDING = {"ENABLED": True, ...}`` — with the setting absent or
``ENABLED: False``, nothing in this subpackage runs and Django behaves exactly
as it does without it (a single ``default`` database, no extra
``DATABASE_ROUTERS`` entry, no query overhead).

Submodules:

- :mod:`snapadmin.sharding.registration` — parses ``SNAPADMIN_SHARDING``
  (either an auto-distributed flat ``DATABASES`` list or an explicit
  ``SHARDS`` mapping), injects the resulting DSNs into
  ``django.conf.settings.DATABASES`` and registers the router. Called once
  from ``SnapAdminConfig.ready()``.
- :mod:`snapadmin.sharding.router` — ``SnapAdminRouter``, the
  ``DATABASE_ROUTERS`` entry that actually picks a database per query.
- :mod:`snapadmin.sharding.health` — a cached, socket-based reachability
  probe backing the router's failover/fallback decisions.
- :mod:`snapadmin.sharding.state` / :mod:`snapadmin.sharding.decorators` —
  ``contextvars``-based (async-safe) request-scoped overrides:
  ``snap_master_only`` / ``snap_target``.
- :mod:`snapadmin.sharding.ids` — ``uuid7()``, an opt-in distributed primary
  key helper for a model spread across shards.

This is a second, more general concern than :mod:`snapadmin.db` (a single
read-replica alias for SnapAdmin's own auto-generated read views) — the two
are independent and do not conflict.
"""
