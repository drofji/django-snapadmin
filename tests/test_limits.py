"""
Tests for the cache-backed quota primitive in :mod:`snapadmin.limits` (#LIM2a).

``reserve()`` is exercised directly against Django's cache API — no database
needed. A fake clock (monkeypatching ``time.time``) drives window rollover
deterministically instead of sleeping.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache as default_cache
from django.test import override_settings


@pytest.fixture(autouse=True)
def _clear_cache():
    default_cache.clear()
    yield
    default_cache.clear()


class TestSingleWindow:
    def test_allows_up_to_the_limit(self):
        from snapadmin.limits import reserve
        for _ in range(3):
            result = reserve("k1", windows={60: 3})
            assert result.allowed is True

    def test_blocks_past_the_limit(self):
        from snapadmin.limits import reserve
        for _ in range(3):
            reserve("k2", windows={60: 3})
        result = reserve("k2", windows={60: 3})
        assert result.allowed is False
        assert result.reason == "rate_limited"
        assert result.retry_after is not None and result.retry_after > 0

    def test_different_keys_do_not_share_a_counter(self):
        from snapadmin.limits import reserve
        for _ in range(3):
            reserve("k3a", windows={60: 3})
        result = reserve("k3b", windows={60: 3})
        assert result.allowed is True


class TestWindowRollover:
    def test_a_new_bucket_resets_the_count(self):
        from snapadmin.limits import reserve
        with patch("snapadmin.limits.time.time", return_value=1000.0):
            for _ in range(3):
                assert reserve("k4", windows={60: 3}).allowed is True
            assert reserve("k4", windows={60: 3}).allowed is False
        # Jump to the next 60s bucket — a fresh window, fresh budget.
        with patch("snapadmin.limits.time.time", return_value=1061.0):
            assert reserve("k4", windows={60: 3}).allowed is True

    def test_retry_after_reflects_time_left_in_the_bucket(self):
        from snapadmin.limits import reserve
        # 1010 % 60 == 50s into the current 60s bucket, so 10s remain in it.
        with patch("snapadmin.limits.time.time", return_value=1010.0):
            for _ in range(2):
                reserve("k5", windows={60: 2})
            result = reserve("k5", windows={60: 2})
        assert result.retry_after == pytest.approx(10.0, abs=0.01)


class TestMultipleWindows:
    def test_every_window_must_clear(self):
        from snapadmin.limits import reserve
        # Per-second cap of 1 trips immediately even though the per-minute
        # cap of 100 has plenty of headroom.
        with patch("snapadmin.limits.time.time", return_value=2000.0):
            assert reserve("k6", windows={1: 1, 60: 100}).allowed is True
            result = reserve("k6", windows={1: 1, 60: 100})
        assert result.allowed is False
        assert result.reason == "rate_limited"

    def test_a_blocked_window_does_not_burn_the_others(self):
        from snapadmin.limits import reserve
        with patch("snapadmin.limits.time.time", return_value=3000.0):
            reserve("k7", windows={1: 1, 3600: 100})  # consumes the 1/sec budget
            reserve("k7", windows={1: 1, 3600: 100})  # blocked on the 1s window
            # Jump forward one second: the 1s window is fresh again, and the
            # hourly counter must still read exactly 1 (the earlier blocked
            # call must not have incremented it).
        with patch("snapadmin.limits.time.time", return_value=3001.0):
            for _ in range(99):
                assert reserve("k7", windows={1: 10000, 3600: 100}).allowed is True
            assert reserve("k7", windows={1: 10000, 3600: 100}).allowed is False


class TestConcurrency:
    def test_blocks_beyond_the_cap(self):
        from snapadmin.limits import reserve
        held = [reserve("k8", concurrency=2) for _ in range(2)]
        assert all(r.allowed for r in held)
        blocked = reserve("k8", concurrency=2)
        assert blocked.allowed is False
        assert blocked.reason == "concurrency"

    def test_release_frees_a_slot(self):
        from snapadmin.limits import reserve
        first = reserve("k9", concurrency=1)
        assert first.allowed is True
        assert reserve("k9", concurrency=1).allowed is False
        first.release()
        assert reserve("k9", concurrency=1).allowed is True

    def test_context_manager_releases_on_exit(self):
        from snapadmin.limits import reserve
        with reserve("k10", concurrency=1) as slot:
            assert slot.allowed is True
            assert reserve("k10", concurrency=1).allowed is False
        assert reserve("k10", concurrency=1).allowed is True

    def test_context_manager_releases_even_if_the_body_raises(self):
        from snapadmin.limits import reserve
        with pytest.raises(RuntimeError):
            with reserve("k11", concurrency=1):
                raise RuntimeError("boom")
        assert reserve("k11", concurrency=1).allowed is True

    def test_release_is_idempotent(self):
        from snapadmin.limits import reserve
        slot = reserve("k12", concurrency=1)
        slot.release()
        slot.release()  # must not double-decrement (which would let in 2 more, not 1)
        assert reserve("k12", concurrency=1).allowed is True
        assert reserve("k12", concurrency=1).allowed is False

    def test_a_rejected_reservation_holds_no_slot(self):
        # Blocked reservations must not need releasing — and must not count
        # against the cap themselves.
        from snapadmin.limits import reserve
        reserve("k13", concurrency=1)
        blocked = reserve("k13", concurrency=1)
        assert blocked.allowed is False
        blocked.release()  # a no-op; must not free the real holder's slot
        assert reserve("k13", concurrency=1).allowed is False

    def test_windows_and_concurrency_together(self):
        from snapadmin.limits import reserve
        with patch("snapadmin.limits.time.time", return_value=5000.0):
            first = reserve("k14", windows={60: 10}, concurrency=1)
            assert first.allowed is True
            # Concurrency cap trips even though the window has headroom.
            assert reserve("k14", windows={60: 10}, concurrency=1).allowed is False
            first.release()
            assert reserve("k14", windows={60: 10}, concurrency=1).allowed is True


class TestCooldown:
    def test_reserve_is_blocked_during_cooldown(self):
        from snapadmin.limits import cooldown, reserve
        cooldown("k15", 60)
        result = reserve("k15", windows={60: 100})
        assert result.allowed is False
        assert result.reason == "cooldown"

    def test_cooldown_does_not_touch_window_or_concurrency_counters(self):
        from snapadmin.limits import cooldown, in_cooldown, reserve
        cooldown("k16", 60)
        reserve("k16", windows={60: 1}, concurrency=1)
        # Once the cooldown itself is cleared, the full budget is still there —
        # proof the blocked attempt never touched either counter.
        default_cache.delete("snapadmin:limits:cooldown:k16")
        assert in_cooldown("k16") is False
        assert reserve("k16", windows={60: 1}, concurrency=1).allowed is True

    def test_in_cooldown_reports_state(self):
        from snapadmin.limits import cooldown, in_cooldown
        assert in_cooldown("k17") is False
        cooldown("k17", 30)
        assert in_cooldown("k17") is True

    def test_reserve_never_sets_a_cooldown_itself(self):
        from snapadmin.limits import in_cooldown, reserve
        for _ in range(5):
            reserve("k18", windows={60: 1})
        assert in_cooldown("k18") is False


class TestCacheEviction:
    def test_a_manually_evicted_window_counter_fails_open(self):
        """An evicted counter must look like "never asked before", not raise."""
        from snapadmin.limits import _window_cache_key, reserve
        with patch("snapadmin.limits.time.time", return_value=9000.0):
            for _ in range(3):
                reserve("k19", windows={60: 3})
            assert reserve("k19", windows={60: 3}).allowed is False
            default_cache.delete(_window_cache_key("k19", 60))
            result = reserve("k19", windows={60: 3})
        assert result.allowed is True

    def test_a_manually_evicted_concurrency_counter_fails_open(self):
        from snapadmin.limits import _concurrency_cache_key, reserve
        reserve("k20", concurrency=1)
        assert reserve("k20", concurrency=1).allowed is False
        default_cache.delete(_concurrency_cache_key("k20"))
        assert reserve("k20", concurrency=1).allowed is True

    @override_settings(CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "snapadmin-limits-eviction-test",
            "OPTIONS": {"MAX_ENTRIES": 5, "CULL_FREQUENCY": 2},
        },
    })
    def test_reserve_survives_real_cache_pressure_without_raising(self):
        """Fill a tiny LocMemCache past its MAX_ENTRIES so it culls entries
        under real memory pressure, and confirm reserve() never raises —
        an evicted counter degrades to "allowed", it never crashes the caller.
        """
        from snapadmin.limits import reserve
        for i in range(50):
            result = reserve(f"pressure-{i}", windows={60: 3}, concurrency=2)
            assert result.allowed is True
        # The very first keys' counters have almost certainly been culled by
        # now; reserving under them again must still behave, not raise.
        for i in range(3):
            result = reserve("pressure-0", windows={60: 3}, concurrency=2)
            assert isinstance(result.allowed, bool)


class TestIncrementDecrementHelpers:
    """Direct tests of the two cache-pressure fallback branches in the helpers
    ``reserve()`` builds on — a double eviction between add() and incr(), and
    decrementing an already-expired counter — both too tightly timed to
    reproduce reliably through the real cache in a race."""

    def test_increment_falls_back_to_set_when_add_is_also_evicted(self):
        from snapadmin.limits import _increment
        cache = MagicMock()
        cache.incr.side_effect = ValueError("no such key")
        result = _increment(cache, "some-key", timeout=60)
        assert result == 1
        cache.set.assert_called_once_with("some-key", 1, 60)

    def test_decrement_swallows_an_already_expired_key(self):
        from snapadmin.limits import _decrement
        cache = MagicMock()
        cache.decr.side_effect = ValueError("no such key")
        _decrement(cache, "some-key")  # must not raise


class TestReserveNoLimits:
    def test_no_windows_and_no_concurrency_always_allows(self):
        from snapadmin.limits import reserve
        for _ in range(10):
            assert reserve("k21").allowed is True

    def test_empty_windows_dict_always_allows(self):
        from snapadmin.limits import reserve
        for _ in range(10):
            assert reserve("k22", windows={}).allowed is True


class TestCacheAlias:
    @override_settings(CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "snapadmin_limits": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "snapadmin-limits-alias-test",
        },
    })
    def test_setting_selects_a_named_cache(self, settings):
        from snapadmin.limits import reserve
        settings.SNAPADMIN_LIMITS_CACHE_ALIAS = "snapadmin_limits"
        for _ in range(2):
            assert reserve("k23", windows={60: 2}).allowed is True
        assert reserve("k23", windows={60: 2}).allowed is False
        # The default cache never saw this key at all — its budget is fresh.
        assert reserve("k23", windows={60: 2}, cache_alias="default").allowed is True

    def test_explicit_cache_alias_argument_overrides_the_setting(self, settings):
        from snapadmin.limits import reserve
        with override_settings(CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "other": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "snapadmin-limits-explicit-alias-test",
            },
        }):
            for _ in range(2):
                assert reserve("k24", windows={60: 2}, cache_alias="other").allowed is True
            assert reserve("k24", windows={60: 2}, cache_alias="other").allowed is False
            # The default alias's own budget for the same key is untouched.
            assert reserve("k24", windows={60: 2}).allowed is True


class TestConcurrencyTimeout:
    def test_a_slot_expires_on_its_own_after_the_timeout(self):
        # A crashed holder must not leak its slot forever — a short-lived
        # cache timeout on the concurrency counter is the safety valve.
        import time as real_time

        from snapadmin.limits import reserve
        reserve("k25", concurrency=1, concurrency_timeout=0.05)
        assert reserve("k25", concurrency=1, concurrency_timeout=0.05).allowed is False
        real_time.sleep(0.15)
        assert reserve("k25", concurrency=1, concurrency_timeout=0.05).allowed is True
