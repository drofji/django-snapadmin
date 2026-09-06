"""
tests/test_sharding_state.py — #SHARD1b/#SHARD1d

``snapadmin.sharding.state`` is the low-level ``contextvars`` machinery
behind the public ``snap_master_only`` / ``snap_target`` API — this file pins
the getter/setter/reset primitives directly; the public decorator/context-
manager surface built on top of them is #SHARD1d's own test file.
"""

import asyncio

from snapadmin.sharding import state


class TestMasterForced:
    def test_defaults_to_false(self):
        assert state.is_master_forced() is False

    def test_set_and_reset_round_trips(self):
        assert state.is_master_forced() is False
        token = state.set_master_forced(True)
        assert state.is_master_forced() is True
        state.reset_master_forced(token)
        assert state.is_master_forced() is False


class TestForcedTarget:
    def test_defaults_to_none(self):
        assert state.get_forced_target() is None

    def test_set_and_reset_round_trips(self):
        token = state.set_forced_target("shard_2", True)
        assert state.get_forced_target() == ("shard_2", True)
        state.reset_forced_target(token)
        assert state.get_forced_target() is None

    def test_replica_flag_is_carried(self):
        token = state.set_forced_target("shard_1", False)
        try:
            assert state.get_forced_target() == ("shard_1", False)
        finally:
            state.reset_forced_target(token)


class TestAsyncIsolation:
    def test_a_value_set_in_one_task_does_not_leak_into_a_concurrent_one(self):
        """contextvars are copied per-task — one coroutine's override must not
        be visible to another running concurrently on the same event loop."""

        async def scenario():
            async def forced_master():
                token = state.set_master_forced(True)
                try:
                    await asyncio.sleep(0.01)
                    return state.is_master_forced()
                finally:
                    state.reset_master_forced(token)

            async def plain():
                await asyncio.sleep(0.005)
                return state.is_master_forced()

            return await asyncio.gather(forced_master(), plain())

        forced_result, plain_result = asyncio.run(scenario())
        assert forced_result is True
        assert plain_result is False
        assert state.is_master_forced() is False
