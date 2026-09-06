"""
tests/test_sharding_decorators.py — #SHARD1d

``snap_master_only`` / ``snap_target`` — the public context-manager/decorator
surface over ``snapadmin.sharding.state``'s contextvars. Every shape is
covered on both a plain sync function and an ``async def`` one: bare
``with``, ``@decorator()``/``@decorator`` (master-only only), and that state
is always cleared afterwards, including when the block raises.
"""

import asyncio

import pytest
from django.test import override_settings

from snapadmin.sharding import state
from snapadmin.sharding.decorators import snap_master_only, snap_target
from snapadmin.sharding.router import SnapAdminRouter


# ── snap_master_only — context manager ──────────────────────────────────────

class TestMasterOnlyContextManager:
    def test_forces_and_then_clears(self):
        assert state.is_master_forced() is False
        with snap_master_only():
            assert state.is_master_forced() is True
        assert state.is_master_forced() is False

    def test_clears_even_on_exception(self):
        with pytest.raises(ValueError):
            with snap_master_only():
                assert state.is_master_forced() is True
                raise ValueError("boom")
        assert state.is_master_forced() is False


# ── snap_master_only — decorator ────────────────────────────────────────────

class TestMasterOnlyDecorator:
    def test_called_form_on_sync_function(self):
        @snap_master_only()
        def inside():
            return state.is_master_forced()

        assert state.is_master_forced() is False
        assert inside() is True
        assert state.is_master_forced() is False

    def test_bare_form_on_sync_function(self):
        @snap_master_only
        def inside():
            return state.is_master_forced()

        assert inside() is True
        assert state.is_master_forced() is False

    def test_bare_and_called_forms_are_equivalent(self):
        @snap_master_only
        def bare():
            return state.is_master_forced()

        @snap_master_only()
        def called():
            return state.is_master_forced()

        assert bare() == called() is True

    def test_async_function(self):
        @snap_master_only()
        async def inside():
            return state.is_master_forced()

        assert asyncio.run(inside()) is True
        assert state.is_master_forced() is False

    def test_bare_async_function(self):
        @snap_master_only
        async def inside():
            return state.is_master_forced()

        assert asyncio.run(inside()) is True

    def test_wraps_preserves_function_identity(self):
        @snap_master_only()
        def named_function():
            """A docstring."""

        assert named_function.__name__ == "named_function"
        assert named_function.__doc__ == "A docstring."

    def test_each_call_is_independently_scoped(self):
        """Calling a decorated function twice must not leak state between calls
        — a fresh context is entered/exited each time."""
        seen = []

        @snap_master_only()
        def inside():
            seen.append(state.is_master_forced())

        inside()
        assert state.is_master_forced() is False
        inside()
        assert seen == [True, True]
        assert state.is_master_forced() is False

    def test_concurrent_async_calls_do_not_interfere(self):
        @snap_master_only()
        async def inside():
            await asyncio.sleep(0.01)
            return state.is_master_forced()

        async def plain():
            await asyncio.sleep(0.005)
            return state.is_master_forced()

        async def scenario():
            return await asyncio.gather(inside(), plain())

        forced_result, plain_result = asyncio.run(scenario())
        assert forced_result is True
        assert plain_result is False


# ── snap_target — context manager ───────────────────────────────────────────

class TestTargetContextManager:
    def test_forces_and_then_clears(self):
        assert state.get_forced_target() is None
        with snap_target(shard="shard_2"):
            assert state.get_forced_target() == ("shard_2", False)
        assert state.get_forced_target() is None

    def test_replica_flag_defaults_false(self):
        with snap_target(shard="shard_1") as ctx:
            assert isinstance(ctx, snap_target)
            assert state.get_forced_target() == ("shard_1", False)

    def test_replica_true_is_carried(self):
        with snap_target(shard="shard_1", replica=True):
            assert state.get_forced_target() == ("shard_1", True)

    def test_clears_even_on_exception(self):
        with pytest.raises(ValueError):
            with snap_target(shard="shard_1"):
                raise ValueError("boom")
        assert state.get_forced_target() is None


# ── snap_target — decorator ─────────────────────────────────────────────────

class TestTargetDecorator:
    def test_sync_function(self):
        @snap_target(shard="shard_2", replica=True)
        def inside():
            return state.get_forced_target()

        assert inside() == ("shard_2", True)
        assert state.get_forced_target() is None

    def test_async_function(self):
        @snap_target(shard="shard_2")
        async def inside():
            return state.get_forced_target()

        assert asyncio.run(inside()) == ("shard_2", False)
        assert state.get_forced_target() is None

    def test_each_call_gets_a_fresh_context(self):
        @snap_target(shard="shard_1")
        def inside():
            return state.get_forced_target()

        assert inside() == inside() == ("shard_1", False)
        assert state.get_forced_target() is None


# ── integration with SnapAdminRouter ────────────────────────────────────────

class TestRoutingIntegration:
    class ByUserId:
        shard_key = "user_id"

        def __init__(self, user_id):
            self.user_id = user_id

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "SHARDS": {
            "shard_1": {"PRIMARY": "d1"},
            "shard_2": {"PRIMARY": "d2"},
        },
    })
    def test_snap_target_overrides_normal_shard_resolution(self):
        router = SnapAdminRouter()
        instance = self.ByUserId(user_id=0)  # would normally resolve to shard_1

        @snap_target(shard="shard_2")
        def route():
            return router.db_for_write(type(instance), instance=instance)

        assert route() == "snapadmin_shard_2_primary"
        # Outside the decorator, normal resolution applies again.
        assert router.db_for_write(type(instance), instance=instance) == "snapadmin_shard_1_primary"

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True, "STRATEGY": "modulo",
        "HA_SETTINGS": {"AUTO_FAILOVER": True},
        "SHARDS": {"shard_1": {"PRIMARY": "d1", "REPLICAS": ["r1"]}},
    })
    def test_snap_master_only_forces_primary_even_with_failover_configured(self, monkeypatch):
        from snapadmin.sharding import router as router_module
        monkeypatch.setattr(router_module.health, "is_alive", lambda alias: alias.endswith("replica_0"))
        router = SnapAdminRouter()
        instance = self.ByUserId(user_id=0)

        @snap_master_only()
        def read():
            return router.db_for_read(type(instance), instance=instance)

        assert read() == "snapadmin_shard_1_primary"
