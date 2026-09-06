"""
tests/test_sharding_router.py — #SHARD1b

``SnapAdminRouter`` picks a database per query once ``SNAPADMIN_SHARDING`` is
enabled. Health checks are monkeypatched here (``snapadmin.sharding.health``
has its own dedicated, socket-level test file) so shard resolution/failover/
replica-selection logic is tested deterministically and without touching a
real socket.

Test "models" are plain classes, not real Django models — ``SnapAdminRouter``
only ever calls ``get_model_meta``/``getattr`` on whatever it is handed, so a
lightweight stand-in exercises the same code path without the overhead of a
real model + migration.

The ``PRIMARY``/``REPLICAS`` values in each ``SHARDS`` config below are
arbitrary DSN-shaped placeholders — the router never parses them (that is
``registration.parse_dsn``'s job). What the router actually returns is the
**alias** :class:`~snapadmin.sharding.registration.ShardConfig` computes from
the shard's *name* (``snapadmin_<shard>_primary`` /
``snapadmin_<shard>_replica_<i>``), which is what every assertion here checks.
"""

import pytest
from django.test import override_settings

from snapadmin.sharding import router as router_module
from snapadmin.sharding import state
from snapadmin.sharding.router import ShardResolutionError, ShardUnavailable, SnapAdminRouter

SP1 = "snapadmin_shard_1_primary"
SP2 = "snapadmin_shard_2_primary"
SP3 = "snapadmin_shard_3_primary"
SR1_0 = "snapadmin_shard_1_replica_0"
SR1_1 = "snapadmin_shard_1_replica_1"
SR1_2 = "snapadmin_shard_1_replica_2"
SR2_0 = "snapadmin_shard_2_replica_0"


class Unsharded:
    pass


class ByUserIdField:
    shard_key = "user_id"

    def __init__(self, user_id):
        self.user_id = user_id


class ByGlobalDefault:
    shard_key = True

    def __init__(self, id):
        self.id = id


class ByEmailField:
    shard_key = "email"

    def __init__(self, email):
        self.email = email


def _router():
    return SnapAdminRouter()


@pytest.fixture(autouse=True)
def _always_alive(monkeypatch):
    """Default every health check to alive; individual tests override per-alias."""
    monkeypatch.setattr(router_module.health, "is_alive", lambda alias: True)
    router_module._round_robin_positions.clear()
    yield
    router_module._round_robin_positions.clear()


# ── opt-in gate ────────────────────────────────────────────────────────────

class TestOptIn:
    @override_settings(SNAPADMIN_SHARDING={"ENABLED": False})
    def test_disabled_returns_none_for_write_and_read(self):
        r = _router()
        instance = ByUserIdField(user_id=1)
        assert r.db_for_write(ByUserIdField, instance=instance) is None
        assert r.db_for_read(ByUserIdField, instance=instance) is None

    def test_unset_returns_none(self):
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(1)) is None

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "dsn1"}, "shard_2": {"PRIMARY": "dsn2"}},
    })
    def test_model_without_shard_key_is_untouched(self):
        r = _router()
        assert r.db_for_write(Unsharded, instance=Unsharded()) is None
        assert r.db_for_read(Unsharded, instance=Unsharded()) is None

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "dsn1"}, "shard_2": {"PRIMARY": "dsn2"}},
    })
    def test_opted_in_model_with_no_resolvable_value_is_untouched(self):
        # No `instance` hint and no same-named hint — the router cannot know
        # which shard this call belongs to.
        r = _router()
        assert r.db_for_write(ByUserIdField) is None


# ── strategy resolution ──────────────────────────────────────────────────

class TestStrategies:
    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}, "shard_2": {"PRIMARY": "d2"}, "shard_3": {"PRIMARY": "d3"}},
    })
    def test_modulo(self):
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(0)) == SP1
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(1)) == SP2
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(2)) == SP3
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(3)) == SP1

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "hash",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}, "shard_2": {"PRIMARY": "d2"}},
    })
    def test_hash_is_deterministic_and_uses_crc32(self):
        import zlib
        r = _router()
        value = "some-user-id"
        expected_index = zlib.crc32(value.encode()) % 2
        expected = SP1 if expected_index == 0 else SP2
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(value)) == expected
        # Same value always resolves to the same shard.
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(value)) == expected

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "range",
        "SHARDS": {
            "shard_1": {"PRIMARY": "d1", "RANGE": (0, 1000)},
            "shard_2": {"PRIMARY": "d2", "RANGE": (1001, 2000)},
        },
    })
    def test_range(self):
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(500)) == SP1
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(1500)) == SP2

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "range",
        "SHARDS": {
            "shard_1": {"PRIMARY": "d1"},  # no RANGE — must be skipped, not matched
            "shard_2": {"PRIMARY": "d2", "RANGE": (1001, 2000)},
        },
    })
    def test_range_skips_a_shard_with_no_range_configured(self):
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(1500)) == SP2

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "range",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "RANGE": (0, 1000)}},
    })
    def test_range_no_match_raises(self):
        r = _router()
        with pytest.raises(ShardResolutionError, match="No shard's RANGE covers"):
            r.db_for_write(ByUserIdField, instance=ByUserIdField(5000))

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "custom",
        "CUSTOM_ROUTER_FUNC": "tests.test_sharding_router._pick_by_parity",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}, "shard_2": {"PRIMARY": "d2"}},
    })
    def test_custom(self):
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(2)) == SP1
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(3)) == SP2

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "custom",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_custom_without_router_func_raises(self):
        r = _router()
        with pytest.raises(ShardResolutionError, match="CUSTOM_ROUTER_FUNC"):
            r.db_for_write(ByUserIdField, instance=ByUserIdField(1))

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "custom",
        "CUSTOM_ROUTER_FUNC": "tests.test_sharding_router._pick_unknown_shard",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_custom_returning_unknown_shard_raises(self):
        r = _router()
        with pytest.raises(ShardResolutionError, match="not a configured shard"):
            r.db_for_write(ByUserIdField, instance=ByUserIdField(1))

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "bogus",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_unknown_strategy_raises(self):
        r = _router()
        with pytest.raises(ShardResolutionError, match="Unknown SNAPADMIN_SHARDING STRATEGY"):
            r.db_for_write(ByUserIdField, instance=ByUserIdField(1))

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_shard_key_true_uses_global_default_field(self):
        r = _router()
        # ByGlobalDefault has shard_key = True -> falls back to
        # SNAPADMIN_SHARDING['SHARD_KEY'], which defaults to "id".
        assert r.db_for_write(ByGlobalDefault, instance=ByGlobalDefault(id=7)) == SP1


class TestNonNumericShardKey:
    """A `modulo`/`range` shard key must be int()-coercible; when it is not
    (a natural key like an email, or a UUID/char pk), the router must name
    the field and strategy and point at `hash` — never echo the value
    itself, since a shard key routinely *is* PII that ends up in logs and
    500 pages.
    """

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}, "shard_2": {"PRIMARY": "d2"}},
    })
    def test_modulo_with_non_numeric_value_raises_descriptive_error(self):
        r = _router()
        with pytest.raises(ShardResolutionError) as exc_info:
            r.db_for_write(ByEmailField, instance=ByEmailField("a@b.com"))
        message = str(exc_info.value)
        assert "email" in message
        assert "modulo" in message
        assert "hash" in message
        assert "a@b.com" not in message

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "range",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "RANGE": (0, 1000)}},
    })
    def test_range_with_non_numeric_value_raises_descriptive_error(self):
        r = _router()
        with pytest.raises(ShardResolutionError) as exc_info:
            r.db_for_write(ByEmailField, instance=ByEmailField("a@b.com"))
        message = str(exc_info.value)
        assert "email" in message
        assert "range" in message
        assert "hash" in message
        assert "a@b.com" not in message


def _pick_by_parity(value):
    return "shard_1" if value % 2 == 0 else "shard_2"


def _pick_unknown_shard(value):
    return "shard_does_not_exist"


# ── db_for_write: primary + failover ────────────────────────────────────────

class TestWriteFailover:
    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_healthy_primary_is_used(self):
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(0)) == SP1

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"AUTO_FAILOVER": False},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_failover_disabled_always_returns_primary_even_if_down(self, monkeypatch):
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: False)
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(0)) == SP1

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"AUTO_FAILOVER": True},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1", "r2"]}},
    })
    def test_failover_promotes_first_reachable_replica(self, monkeypatch):
        alive = {SP1: False, SR1_0: False, SR1_1: True}
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: alive[alias])
        r = _router()
        with pytest.warns(UserWarning, match="failing writes over to replica"):
            assert r.db_for_write(ByUserIdField, instance=ByUserIdField(0)) == SR1_1

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"AUTO_FAILOVER": True},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_nothing_reachable_still_returns_primary(self, monkeypatch):
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: False)
        r = _router()
        assert r.db_for_write(ByUserIdField, instance=ByUserIdField(0)) == SP1


# ── db_for_read: replica selection + fallback ───────────────────────────────

class TestReadSelection:
    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_no_replicas_configured_reads_from_primary(self):
        r = _router()
        assert r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) == SP1

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo", "REPLICA_SELECTION": "first_available",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1", "r2"]}},
    })
    def test_first_available_prefers_declared_order(self, monkeypatch):
        alive = {SR1_0: False, SR1_1: True}
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: alive[alias])
        r = _router()
        assert r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) == SR1_1

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo", "REPLICA_SELECTION": "random",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1", "r2"]}},
    })
    def test_random_picks_among_live_replicas(self, monkeypatch):
        monkeypatch.setattr(router_module.random, "choice", lambda pool: sorted(pool)[0])
        r = _router()
        assert r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) == SR1_0

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo", "REPLICA_SELECTION": "round_robin",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1", "r2", "r3"]}},
    })
    def test_round_robin_cycles_through_replicas(self):
        r = _router()
        picks = [r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) for _ in range(4)]
        assert picks == [SR1_0, SR1_1, SR1_2, SR1_0]

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo", "REPLICA_SELECTION": "round_robin",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1", "r2", "r3"]}},
    })
    def test_round_robin_skips_a_down_replica(self, monkeypatch):
        alive = {SR1_0: True, SR1_1: False, SR1_2: True}
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: alive[alias])
        r = _router()
        picks = [r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) for _ in range(3)]
        assert picks == [SR1_0, SR1_2, SR1_0]

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"FALLBACK_TO_PRIMARY": True},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_all_replicas_down_falls_back_to_primary(self, monkeypatch):
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: False)
        r = _router()
        with pytest.warns(UserWarning, match="falling back to primary"):
            assert r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) == SP1

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"FALLBACK_TO_PRIMARY": False},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_fallback_disabled_raises_instead(self, monkeypatch):
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: False)
        r = _router()
        with pytest.raises(ShardUnavailable, match="FALLBACK_TO_PRIMARY"):
            r.db_for_read(ByUserIdField, instance=ByUserIdField(0))


# ── snap_master_only / snap_target forced state ─────────────────────────────

class TestForcedState:
    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"AUTO_FAILOVER": True},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_master_forced_bypasses_replica_selection_on_read(self):
        token = state.set_master_forced(True)
        try:
            r = _router()
            assert r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) == SP1
        finally:
            state.reset_master_forced(token)

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"AUTO_FAILOVER": True},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_master_forced_bypasses_failover_on_write(self, monkeypatch):
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: alias == SR1_0)
        token = state.set_master_forced(True)
        try:
            r = _router()
            # Primary is down and a replica is up, but snap_master_only must
            # still return the literal primary — no promotion at all.
            assert r.db_for_write(ByUserIdField, instance=ByUserIdField(0)) == SP1
        finally:
            state.reset_master_forced(token)

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {
            "shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]},
            "shard_2": {"PRIMARY": "d2", "REPLICAS": ["r2"]},
        },
    })
    def test_target_forces_a_specific_shard_regardless_of_the_key(self):
        token = state.set_forced_target("shard_2", True)
        try:
            r = _router()
            # ByUserIdField(0) would normally resolve to shard_1 (modulo 0) —
            # snap_target overrides that entirely.
            assert r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) == SR2_0
        finally:
            state.reset_forced_target(token)

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_target_with_replica_false_reads_primary(self):
        token = state.set_forced_target("shard_1", False)
        try:
            r = _router()
            assert r.db_for_read(ByUserIdField, instance=ByUserIdField(0)) == SP1
        finally:
            state.reset_forced_target(token)

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_target_always_writes_the_shards_primary(self):
        token = state.set_forced_target("shard_1", True)
        try:
            r = _router()
            assert r.db_for_write(ByUserIdField, instance=ByUserIdField(0)) == SP1
        finally:
            state.reset_forced_target(token)

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_target_naming_an_unknown_shard_raises(self):
        token = state.set_forced_target("no_such_shard", False)
        try:
            r = _router()
            with pytest.raises(ShardResolutionError, match="not a configured shard"):
                r.db_for_write(ByUserIdField, instance=ByUserIdField(0))
        finally:
            state.reset_forced_target(token)


# ── allow_relation / allow_migrate ──────────────────────────────────────────

class TestAllowRelation:
    @override_settings(SNAPADMIN_SHARDING={"ENABLED": False})
    def test_disabled_is_no_opinion(self):
        assert _router().allow_relation(object(), object()) is None

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_no_opinion_when_neither_model_opted_in(self):
        """`None` means "no opinion"; `True` actively suppresses Django's own
        cross-database relation check. Returning `True` for a pair the router
        never routed would silence that check project-wide the moment sharding
        is switched on — hiding an unrelated bug that puts two objects on
        different databases."""
        assert _router().allow_relation(Unsharded(), Unsharded()) is None

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_allows_when_a_sharded_model_is_involved(self):
        """Cross-shard relations stay the project's own concern to manage — a
        ForeignKey cannot enforce referential integrity across separate physical
        databases regardless of what this returns — so SnapAdmin does not refuse
        a relation a project may have deliberately designed to span shards."""
        assert _router().allow_relation(ByUserIdField(1), Unsharded()) is True
        assert _router().allow_relation(Unsharded(), ByUserIdField(1)) is True
        assert _router().allow_relation(ByUserIdField(1), ByEmailField("a@b.com")) is True


class TestAllowMigrate:
    @override_settings(SNAPADMIN_SHARDING={"ENABLED": False})
    def test_disabled_is_no_opinion(self):
        assert _router().allow_migrate("default", "demo") is None

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_replica_alias_is_refused(self):
        assert _router().allow_migrate(SR1_0, "demo") is False

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_primary_alias_is_no_opinion(self):
        assert _router().allow_migrate(SP1, "demo") is None

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {"shard_1": {"PRIMARY": "d1"}},
    })
    def test_unrelated_alias_is_no_opinion(self):
        assert _router().allow_migrate("default", "demo") is None

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True})
    def test_misconfigured_sharding_does_not_block_migration(self):
        # Neither SHARDS nor DATABASES is set — get_shards() raises internally;
        # allow_migrate() must degrade to "no opinion", not blow up manage.py
        # migrate for the whole project.
        assert _router().allow_migrate("default", "demo") is None
