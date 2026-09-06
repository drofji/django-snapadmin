"""
tests/test_sharding_health.py — #SHARD1c

``snapadmin.sharding.health.is_alive`` never opens a real socket in this
suite — ``socket.create_connection`` is monkeypatched per test so the
result (and the cache/logging around it) is deterministic and fast.
"""

import pytest
from django.test import override_settings

from snapadmin.sharding import health


@pytest.fixture(autouse=True)
def _clear_health_cache():
    health.reset_cache()
    yield
    health.reset_cache()


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestIsAlive:
    @override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
    def test_unknown_alias_is_not_alive(self):
        assert health.is_alive("does_not_exist") is False

    @override_settings(DATABASES={
        "sqlite_shard": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    })
    def test_no_host_is_always_alive(self, monkeypatch):
        called = []
        monkeypatch.setattr(health.socket, "create_connection", lambda *a, **k: called.append(1))
        assert health.is_alive("sqlite_shard") is True
        assert called == []  # never even attempted a socket connect

    @override_settings(
        SNAPADMIN_SHARDING={"ENABLED": True, "HA_SETTINGS": {"HEALTH_CHECK_TIMEOUT": 0.25}},
        DATABASES={
            "pg_shard": {
                "ENGINE": "django.db.backends.postgresql", "HOST": "db1", "PORT": "5432",
                "NAME": "x",
            },
        },
    )
    def test_reachable_host_is_alive(self, monkeypatch):
        seen = {}

        def fake_create_connection(address, timeout):
            seen["address"] = address
            seen["timeout"] = timeout
            return _FakeSocket()

        monkeypatch.setattr(health.socket, "create_connection", fake_create_connection)
        assert health.is_alive("pg_shard") is True
        assert seen["address"] == ("db1", 5432)
        assert seen["timeout"] == 0.25

    @override_settings(DATABASES={
        "pg_shard": {"ENGINE": "django.db.backends.postgresql", "HOST": "db1", "NAME": "x"},
    })
    def test_missing_port_defaults_from_engine(self, monkeypatch):
        seen = {}

        def fake_create_connection(address, timeout):
            seen["address"] = address
            return _FakeSocket()

        monkeypatch.setattr(health.socket, "create_connection", fake_create_connection)
        health.is_alive("pg_shard")
        assert seen["address"] == ("db1", 5432)

    @override_settings(DATABASES={
        "mysql_shard": {"ENGINE": "django.db.backends.mysql", "HOST": "db2", "NAME": "x"},
    })
    def test_mysql_default_port(self, monkeypatch):
        seen = {}

        def fake_create_connection(address, timeout):
            seen["address"] = address
            return _FakeSocket()

        monkeypatch.setattr(health.socket, "create_connection", fake_create_connection)
        health.is_alive("mysql_shard")
        assert seen["address"] == ("db2", 3306)

    @override_settings(DATABASES={
        "down_shard": {"ENGINE": "django.db.backends.postgresql", "HOST": "db3", "PORT": "5432", "NAME": "x"},
    })
    def test_unreachable_host_is_not_alive(self, monkeypatch):
        def raise_os_error(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(health.socket, "create_connection", raise_os_error)
        assert health.is_alive("down_shard") is False

    @override_settings(DATABASES={
        "flaky": {"ENGINE": "django.db.backends.postgresql", "HOST": "db4", "PORT": "5432", "NAME": "x"},
    })
    def test_result_is_cached_within_the_ttl(self, monkeypatch):
        calls = []

        def fake_create_connection(address, timeout):
            calls.append(address)
            return _FakeSocket()

        monkeypatch.setattr(health.socket, "create_connection", fake_create_connection)
        assert health.is_alive("flaky") is True
        assert health.is_alive("flaky") is True
        assert len(calls) == 1  # second call served from cache, no new probe

    @override_settings(DATABASES={
        "flaky": {"ENGINE": "django.db.backends.postgresql", "HOST": "db4", "PORT": "5432", "NAME": "x"},
    })
    def test_ttl_zero_re_probes_every_call(self, monkeypatch):
        monkeypatch.setattr(health, "CACHE_TTL_SECONDS", 0.0)
        calls = []

        def fake_create_connection(address, timeout):
            calls.append(address)
            return _FakeSocket()

        monkeypatch.setattr(health.socket, "create_connection", fake_create_connection)
        health.is_alive("flaky")
        health.is_alive("flaky")
        assert len(calls) == 2

    def test_reset_cache_clears_everything(self, monkeypatch):
        with override_settings(DATABASES={
            "sqlite_shard": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
        }):
            health.is_alive("sqlite_shard")
        assert health._cache
        health.reset_cache()
        assert health._cache == {}

    def test_invalid_health_check_timeout_falls_back_to_default(self):
        with override_settings(SNAPADMIN_SHARDING={"ENABLED": True, "HA_SETTINGS": {"HEALTH_CHECK_TIMEOUT": "oops"}}):
            assert health._health_check_timeout() == 1.0
