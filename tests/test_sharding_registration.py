"""
tests/test_sharding_registration.py — #SHARD1a

``snapadmin.sharding.registration`` parses ``SNAPADMIN_SHARDING`` (both the
auto-distributed ``DATABASES`` list and the explicit ``SHARDS`` mapping),
parses DSNs with stdlib ``urllib.parse`` only, and injects the result into
``django.conf.settings.DATABASES`` / ``DATABASE_ROUTERS`` — a no-op unless
``ENABLED`` is set, and degrading (never raising) on misconfiguration.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from snapadmin.sharding import registration


# ── parse_dsn ────────────────────────────────────────────────────────────────

class TestParseDsn:
    def test_postgres_scheme(self):
        result = registration.parse_dsn("postgres://alice:s3cret@db1:5432/mydb")
        assert result == {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "mydb",
            "USER": "alice",
            "PASSWORD": "s3cret",
            "HOST": "db1",
            "PORT": "5432",
        }

    def test_postgresql_scheme_is_an_alias(self):
        result = registration.parse_dsn("postgresql://alice:s3cret@db1:5432/mydb")
        assert result["ENGINE"] == "django.db.backends.postgresql"

    def test_mysql_scheme(self):
        result = registration.parse_dsn("mysql://bob:pw@db2:3306/other")
        assert result["ENGINE"] == "django.db.backends.mysql"

    def test_no_credentials_or_port(self):
        result = registration.parse_dsn("postgres://db1/mydb")
        assert result["USER"] == ""
        assert result["PASSWORD"] == ""
        assert result["PORT"] == ""
        assert result["HOST"] == "db1"

    def test_percent_encoded_password_is_decoded(self):
        result = registration.parse_dsn("postgres://alice:p%40ss@db1:5432/mydb")
        assert result["PASSWORD"] == "p@ss"

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ImproperlyConfigured, match="unsupported database scheme"):
            registration.parse_dsn("redis://db1:6379/0")

    def test_missing_host_raises(self):
        with pytest.raises(ImproperlyConfigured, match="missing a host or database name"):
            registration.parse_dsn("postgres:///mydb")

    def test_missing_database_name_raises(self):
        with pytest.raises(ImproperlyConfigured, match="missing a host or database name"):
            registration.parse_dsn("postgres://db1:5432/")

    def test_unparseable_dsn_raises(self):
        with pytest.raises(ImproperlyConfigured, match="could not parse DSN"):
            registration.parse_dsn("postgres://db1:notanumber/mydb")

    def test_error_message_never_leaks_the_password(self):
        with pytest.raises(ImproperlyConfigured) as excinfo:
            registration.parse_dsn("redis://alice:s3cret@db1:6379/0")
        assert "s3cret" not in str(excinfo.value)
        assert "***" in str(excinfo.value)


# ── redact_dsn ───────────────────────────────────────────────────────────────

class TestRedactDsn:
    def test_masks_password(self):
        assert registration.redact_dsn("postgres://alice:s3cret@db1:5432/mydb") == (
            "postgres://alice:***@db1:5432/mydb"
        )

    def test_no_password_left_unchanged(self):
        dsn = "postgres://alice@db1:5432/mydb"
        assert registration.redact_dsn(dsn) == dsn

    def test_unparseable_dsn_returns_placeholder(self):
        # urlsplit() itself raises ValueError on a malformed IPv6 host (a bad
        # port, by contrast, only raises lazily on .port access — see
        # TestParseDsn.test_unparseable_dsn_raises for that case).
        assert registration.redact_dsn("postgres://[::1/mydb") == "<unparseable DSN>"


# ── get_shards: explicit SHARDS mapping (Mode B) ──────────────────────────────

class TestExplicitShards:
    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {
            "shard_1": {
                "PRIMARY": "postgres://u:p@s1-master:5432/db1",
                "REPLICAS": ["postgres://u:p@s1-replica-1:5432/db1"],
                "RANGE": (0, 1000000),
            },
            "shard_2": {
                "PRIMARY": "postgres://u:p@s2-master:5432/db2",
            },
        },
    })
    def test_shapes_and_aliases(self):
        shards = registration.get_shards()
        assert set(shards) == {"shard_1", "shard_2"}

        shard_1 = shards["shard_1"]
        assert shard_1.primary_dsn == "postgres://u:p@s1-master:5432/db1"
        assert shard_1.replica_dsns == ("postgres://u:p@s1-replica-1:5432/db1",)
        assert shard_1.value_range == (0, 1000000)
        assert shard_1.primary_alias == "snapadmin_shard_1_primary"
        assert shard_1.replica_aliases == ("snapadmin_shard_1_replica_0",)

        shard_2 = shards["shard_2"]
        assert shard_2.replica_dsns == ()
        assert shard_2.value_range is None

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {"only": {"PRIMARY": "postgres://u:p@h:5432/db"}},
        "DATABASES": ["postgres://u:p@other:5432/db"],
    })
    def test_explicit_shards_win_over_flat_databases_list(self):
        assert set(registration.get_shards()) == {"only"}

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True, "SHARDS": {"broken": {}}})
    def test_missing_primary_raises(self):
        with pytest.raises(ImproperlyConfigured, match="needs a 'PRIMARY' DSN"):
            registration.get_shards()

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {"s": {"PRIMARY": "postgres://u:p@h:5432/db", "RANGE": (1,)}},
    })
    def test_range_not_a_pair_raises(self):
        with pytest.raises(ImproperlyConfigured, match="RANGE.*pair of integers"):
            registration.get_shards()

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {"s": {"PRIMARY": "postgres://u:p@h:5432/db", "RANGE": (100, 1)}},
    })
    def test_range_low_greater_than_high_raises(self):
        with pytest.raises(ImproperlyConfigured, match="low is greater than high"):
            registration.get_shards()


# ── get_shards: auto-distributed flat DATABASES list (Mode A) ────────────────

class TestAutoShards:
    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDING_ENABLED": False,
        "MIRRORING_ENABLED": False,
        "DATABASES": ["postgres://u:p@a:5432/db", "postgres://u:p@b:5432/db"],
    })
    def test_sharding_off_mirroring_off_is_one_shard_primary_only(self):
        shards = registration.get_shards()
        assert set(shards) == {"shard_1"}
        assert shards["shard_1"].primary_dsn == "postgres://u:p@a:5432/db"
        assert shards["shard_1"].replica_dsns == ()

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDING_ENABLED": False,
        "MIRRORING_ENABLED": True,
        "DATABASES": [
            "postgres://u:p@a:5432/db",
            "postgres://u:p@b:5432/db",
            "postgres://u:p@c:5432/db",
        ],
    })
    def test_sharding_off_mirroring_on_is_pure_replication(self):
        shards = registration.get_shards()
        assert set(shards) == {"shard_1"}
        assert shards["shard_1"].primary_dsn == "postgres://u:p@a:5432/db"
        assert shards["shard_1"].replica_dsns == (
            "postgres://u:p@b:5432/db", "postgres://u:p@c:5432/db",
        )

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDING_ENABLED": True,
        "MIRRORING_ENABLED": False,
        "DATABASES": ["postgres://u:p@a:5432/db", "postgres://u:p@b:5432/db"],
    })
    def test_sharding_on_mirroring_off_every_dsn_is_its_own_shard(self):
        shards = registration.get_shards()
        assert set(shards) == {"shard_1", "shard_2"}
        assert shards["shard_1"].replica_dsns == ()
        assert shards["shard_2"].replica_dsns == ()
        assert shards["shard_2"].primary_dsn == "postgres://u:p@b:5432/db"

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDING_ENABLED": True,
        "MIRRORING_ENABLED": True,
        "REPLICAS_PER_SHARD": 1,
        "DATABASES": [
            "postgres://u:p@a1:5432/db", "postgres://u:p@a2:5432/db",
            "postgres://u:p@b1:5432/db", "postgres://u:p@b2:5432/db",
        ],
    })
    def test_sharding_and_mirroring_on_slices_into_groups(self):
        shards = registration.get_shards()
        assert set(shards) == {"shard_1", "shard_2"}
        assert shards["shard_1"].primary_dsn == "postgres://u:p@a1:5432/db"
        assert shards["shard_1"].replica_dsns == ("postgres://u:p@a2:5432/db",)
        assert shards["shard_2"].primary_dsn == "postgres://u:p@b1:5432/db"
        assert shards["shard_2"].replica_dsns == ("postgres://u:p@b2:5432/db",)

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDING_ENABLED": True,
        "MIRRORING_ENABLED": True,
        "REPLICAS_PER_SHARD": 1,
        "DATABASES": ["postgres://u:p@a:5432/db", "postgres://u:p@b:5432/db", "postgres://u:p@c:5432/db"],
    })
    def test_uneven_length_raises(self):
        with pytest.raises(ImproperlyConfigured, match="does not divide evenly"):
            registration.get_shards()

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True})
    def test_no_shards_and_no_databases_raises(self):
        with pytest.raises(ImproperlyConfigured, match="neither 'SHARDS' nor 'DATABASES'"):
            registration.get_shards()


# ── is_sharding_enabled / get_sharding_config ─────────────────────────────────

class TestIsShardingEnabled:
    def test_unset_is_disabled(self):
        assert registration.is_sharding_enabled() is False
        assert registration.get_sharding_config() == {}

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": False})
    def test_explicit_false_is_disabled(self):
        assert registration.is_sharding_enabled() is False

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True, "DATABASES": ["postgres://u:p@a:5432/db"]})
    def test_explicit_true_is_enabled(self):
        assert registration.is_sharding_enabled() is True


# ── iter_primary_aliases ───────────────────────────────────────────────────────

class TestIterPrimaryAliases:
    def test_disabled_is_empty(self):
        assert registration.iter_primary_aliases() == []

    @override_settings(SNAPADMIN_SHARDING={
        "ENABLED": True,
        "SHARDS": {
            "shard_2": {"PRIMARY": "postgres://u:p@b:5432/db"},
            "shard_1": {"PRIMARY": "postgres://u:p@a:5432/db"},
        },
    })
    def test_sorted_by_shard_name(self):
        assert registration.iter_primary_aliases() == [
            "snapadmin_shard_1_primary", "snapadmin_shard_2_primary",
        ]

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True})
    def test_misconfigured_is_empty_not_raising(self):
        assert registration.iter_primary_aliases() == []


# ── configure_sharding ────────────────────────────────────────────────────────

class TestConfigureSharding:
    def test_disabled_leaves_databases_and_routers_untouched(self):
        with override_settings(
            SNAPADMIN_SHARDING={"ENABLED": False},
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            DATABASE_ROUTERS=[],
        ):
            from django.conf import settings as live_settings
            registration.configure_sharding()
            assert list(live_settings.DATABASES) == ["default"]
            assert live_settings.DATABASE_ROUTERS == []

    def test_unset_is_a_no_op(self):
        with override_settings(
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            DATABASE_ROUTERS=[],
        ):
            from django.conf import settings as live_settings
            registration.configure_sharding()
            assert list(live_settings.DATABASES) == ["default"]

    def test_enabled_injects_aliases_and_router(self):
        with override_settings(
            SNAPADMIN_SHARDING={
                "ENABLED": True,
                "SHARDS": {
                    "shard_1": {
                        "PRIMARY": "postgres://u:p@s1-master:5432/db1",
                        "REPLICAS": ["postgres://u:p@s1-replica-1:5432/db1"],
                    },
                },
            },
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            DATABASE_ROUTERS=[],
        ):
            from django.conf import settings as live_settings
            registration.configure_sharding()

            assert live_settings.DATABASES["snapadmin_shard_1_primary"]["HOST"] == "s1-master"
            assert live_settings.DATABASES["snapadmin_shard_1_replica_0"]["HOST"] == "s1-replica-1"
            assert live_settings.DATABASE_ROUTERS == [registration.SHARDING_ROUTER_PATH]

    def test_idempotent_does_not_duplicate_router_entry(self):
        with override_settings(
            SNAPADMIN_SHARDING={
                "ENABLED": True,
                "SHARDS": {"shard_1": {"PRIMARY": "postgres://u:p@a:5432/db"}},
            },
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            DATABASE_ROUTERS=[],
        ):
            from django.conf import settings as live_settings
            registration.configure_sharding()
            registration.configure_sharding()
            assert live_settings.DATABASE_ROUTERS == [registration.SHARDING_ROUTER_PATH]

    def test_misconfigured_degrades_without_raising(self):
        with override_settings(
            SNAPADMIN_SHARDING={
                "ENABLED": True,
                "SHARDS": {"shard_1": {"PRIMARY": "redis://a:6379/0"}},
            },
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            DATABASE_ROUTERS=[],
        ):
            from django.conf import settings as live_settings
            registration.configure_sharding()  # must not raise
            assert list(live_settings.DATABASES) == ["default"]
            assert live_settings.DATABASE_ROUTERS == []
