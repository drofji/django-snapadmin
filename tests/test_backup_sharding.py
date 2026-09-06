"""
tests/test_backup_sharding.py — #SHARD1h

``manage.py snapadmin_db_backup`` (via :func:`snapadmin.backup.build_backup_bundle`)
must back up every shard's *primary* when ``SNAPADMIN_SHARDING`` is enabled —
never a replica, mirroring ``snap_migrate``'s (#SHARD1f) alias enumeration
exactly. An existing single-database project (sharding off, the default)
keeps today's exact ``"db"`` part name, filename shape and retention
grouping — pinned here as a regression, not just assumed from
``test_backup.py``'s own (unchanged) suite.

The DSNs inside ``SNAPADMIN_SHARDING['SHARDS']`` below are never read by
anything backup-related (only ``snapadmin.sharding.registration.parse_dsn``
reads a DSN, and nothing here calls it) — what matters is that
``settings.DATABASES`` actually carries a real entry at the alias each shard
name computes to (``snapadmin_shard_1_primary`` etc.), which is what
``create_db_dump``/``create_encrypted_db_dump`` actually open.
"""

from dataclasses import replace
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from snapadmin import backup as backup_module
from snapadmin.backup import (
    BACKUP_PREFIX,
    build_backup_bundle,
    create_db_dump,
    create_encrypted_db_dump,
    get_backup_config,
    run_backup,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:Overriding setting DATABASES can lead to unexpected behavior"
)

TWO_SHARDS_SETTING = {
    "ENABLED": True,
    "SHARDS": {
        "shard_1": {"PRIMARY": "postgres://u:p@irrelevant-1:5432/db"},
        "shard_2": {"PRIMARY": "postgres://u:p@irrelevant-2:5432/db"},
    },
}


@pytest.fixture
def two_shard_databases(tmp_path):
    """Three real on-disk SQLite files: default, and each shard's primary."""
    default_file = tmp_path / "default.sqlite3"
    default_file.write_bytes(b"default-payload")
    shard1_file = tmp_path / "shard1.sqlite3"
    shard1_file.write_bytes(b"shard-1-payload")
    shard2_file = tmp_path / "shard2.sqlite3"
    shard2_file.write_bytes(b"shard-2-payload")

    databases = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(default_file)},
        "snapadmin_shard_1_primary": {
            "ENGINE": "django.db.backends.sqlite3", "NAME": str(shard1_file),
        },
        "snapadmin_shard_2_primary": {
            "ENGINE": "django.db.backends.sqlite3", "NAME": str(shard2_file),
        },
    }
    with override_settings(DATABASES=databases):
        yield {"default": default_file, "shard_1": shard1_file, "shard_2": shard2_file}


@pytest.fixture
def out_dir(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    return directory


# ── _shard_primary_targets ──────────────────────────────────────────────────

class TestShardPrimaryTargets:
    def test_disabled_is_empty(self):
        assert backup_module._shard_primary_targets() == []

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": False})
    def test_explicitly_disabled_is_empty(self):
        assert backup_module._shard_primary_targets() == []

    @override_settings(SNAPADMIN_SHARDING={"ENABLED": True})
    def test_misconfigured_is_empty_not_raising(self):
        assert backup_module._shard_primary_targets() == []

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS_SETTING)
    def test_returns_name_alias_pairs_sorted_by_shard_name(self):
        assert backup_module._shard_primary_targets() == [
            ("shard_1", "snapadmin_shard_1_primary"),
            ("shard_2", "snapadmin_shard_2_primary"),
        ]


# ── create_db_dump / create_encrypted_db_dump: alias + label ───────────────

class TestCreateDbDumpAlias:
    def test_default_alias_is_unchanged(self, out_dir, two_shard_databases):
        dump = create_db_dump(out_dir)
        assert dump.name.startswith(BACKUP_PREFIX)
        assert "shard" not in dump.name
        with __import__("gzip").open(dump, "rb") as f:
            assert f.read() == b"default-payload"

    def test_alias_and_label_select_the_shard_and_qualify_the_name(self, out_dir, two_shard_databases):
        dump = create_db_dump(out_dir, alias="snapadmin_shard_1_primary", label="shard_1")
        assert dump.name.startswith(f"{BACKUP_PREFIX}shard_1-")
        with __import__("gzip").open(dump, "rb") as f:
            assert f.read() == b"shard-1-payload"

    def test_two_shards_never_collide_on_the_same_timestamp(self, out_dir, two_shard_databases):
        dump1 = create_db_dump(out_dir, alias="snapadmin_shard_1_primary", label="shard_1")
        dump2 = create_db_dump(out_dir, alias="snapadmin_shard_2_primary", label="shard_2")
        assert dump1 != dump2
        assert dump1.exists() and dump2.exists()


class TestCreateEncryptedDbDumpAlias:
    def test_alias_and_label_are_threaded_through(self, out_dir, two_shard_databases, monkeypatch):
        # Encryption itself is covered exhaustively in test_backup.py; here we
        # only need to confirm the alias/label plumbing reaches the right
        # database and filename, so stub the actual AGE call.
        monkeypatch.setattr(
            backup_module.crypto, "encrypt_stream",
            lambda reader, writer, recipients, **kw: writer.write(reader.read()),
        )
        config = replace(get_backup_config(), age_recipients=["age1fakerecipient"])
        dump = create_encrypted_db_dump(
            out_dir, config, alias="snapadmin_shard_2_primary", label="shard_2",
        )
        assert dump.name.startswith(f"{BACKUP_PREFIX}shard_2-")
        assert dump.name.endswith(".sqlite3.gz.age")


# ── build_backup_bundle: sharding on vs off ─────────────────────────────────

class TestBuildBackupBundleSharding:
    def test_sharding_off_keeps_todays_exact_shape(self, out_dir, two_shard_databases):
        """Regression: an un-sharded project's bundle is untouched by this feature."""
        config = get_backup_config()
        parts = build_backup_bundle(out_dir, config)
        assert set(parts) == {"db", "manifest"}
        assert parts["db"].name.startswith(BACKUP_PREFIX)
        assert "shard" not in parts["db"].name

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS_SETTING)
    def test_sharding_on_produces_one_db_part_per_shard(self, out_dir, two_shard_databases):
        config = get_backup_config()
        parts = build_backup_bundle(out_dir, config)

        assert set(parts) == {"db.shard_1", "db.shard_2", "manifest"}
        assert parts["db.shard_1"].name.startswith(f"{BACKUP_PREFIX}shard_1-")
        assert parts["db.shard_2"].name.startswith(f"{BACKUP_PREFIX}shard_2-")

        import gzip
        assert gzip.open(parts["db.shard_1"], "rb").read() == b"shard-1-payload"
        assert gzip.open(parts["db.shard_2"], "rb").read() == b"shard-2-payload"

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS_SETTING)
    def test_manifest_checksums_every_shard_independently(self, out_dir, two_shard_databases):
        from snapadmin.backup import sha256_file

        config = get_backup_config()
        parts = build_backup_bundle(out_dir, config)

        import json
        manifest = json.loads(parts["manifest"].read_text())
        assert set(manifest["parts"]) == {"db.shard_1", "db.shard_2"}
        assert manifest["parts"]["db.shard_1"]["sha256"] == sha256_file(parts["db.shard_1"])
        assert manifest["parts"]["db.shard_2"]["sha256"] == sha256_file(parts["db.shard_2"])
        # Distinct content -> distinct checksums, not a copy-paste of one shard.
        assert manifest["parts"]["db.shard_1"]["sha256"] != manifest["parts"]["db.shard_2"]["sha256"]

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS_SETTING)
    def test_never_touches_a_replica_alias(self, out_dir, two_shard_databases, monkeypatch):
        seen_aliases = []
        original = create_db_dump

        def spy(target_dir, *, alias="default", label=None):
            seen_aliases.append(alias)
            return original(target_dir, alias=alias, label=label)

        monkeypatch.setattr(backup_module, "create_db_dump", spy)
        build_backup_bundle(out_dir, get_backup_config())

        assert seen_aliases == ["snapadmin_shard_1_primary", "snapadmin_shard_2_primary"]
        assert not any("replica" in alias for alias in seen_aliases)


# ── retention: each shard pruned independently ──────────────────────────────

class TestPerShardRetention:
    def test_part_prefix_for_recognises_a_shard_qualified_filename(self):
        assert (
            backup_module._part_prefix_for("snapadmin-db-shard_1-20260826-000000.sql.gz")
            == "snapadmin-db-shard_1-"
        )
        assert (
            backup_module._part_prefix_for("snapadmin-db-shard_2-20260826-000000.sql.gz")
            == "snapadmin-db-shard_2-"
        )
        # Unsharded shape is completely unaffected.
        assert (
            backup_module._part_prefix_for("snapadmin-db-20260826-000000.sql.gz")
            == BACKUP_PREFIX
        )

    def test_pruning_one_shard_never_touches_the_other(self, tmp_path):
        directory = tmp_path
        for i in range(5):
            (directory / f"snapadmin-db-shard_1-2026010{i}-000000.sql.gz").write_bytes(b"x")
            (directory / f"snapadmin-db-shard_2-2026010{i}-000000.sql.gz").write_bytes(b"x")

        backup_module._prune_directory(directory, keep=2, prefix="snapadmin-db-shard_1-")

        remaining = sorted(p.name for p in directory.iterdir())
        shard_1_remaining = [n for n in remaining if "shard_1" in n]
        shard_2_remaining = [n for n in remaining if "shard_2" in n]
        assert len(shard_1_remaining) == 2
        assert len(shard_2_remaining) == 5  # completely untouched


# ── run_backup: end-to-end summary shape with sharding on ──────────────────

class TestRunBackupSharding:
    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS_SETTING)
    def test_ships_every_shards_dump_to_the_destination(self, out_dir, two_shard_databases, tmp_path):
        local_dir = tmp_path / "local"
        config = replace(get_backup_config(), enabled=True, local_dir=local_dir, keep=3)
        result = run_backup(["local"], config=config)

        assert result["status"] == "ok"
        stored = sorted(p.name for p in local_dir.iterdir())
        assert any(n.startswith(f"{BACKUP_PREFIX}shard_1-") for n in stored)
        assert any(n.startswith(f"{BACKUP_PREFIX}shard_2-") for n in stored)

    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS_SETTING)
    def test_summary_dump_field_names_a_real_shard_part(self, out_dir, two_shard_databases, tmp_path):
        local_dir = tmp_path / "local"
        config = replace(get_backup_config(), enabled=True, local_dir=local_dir, keep=3)
        result = run_backup(["local"], config=config)

        assert result["dump"].startswith(f"{BACKUP_PREFIX}shard_1-")


class TestManageCommandIntegration:
    @override_settings(SNAPADMIN_SHARDING=TWO_SHARDS_SETTING)
    def test_snapadmin_db_backup_needs_no_changes_of_its_own(self, out_dir, two_shard_databases, tmp_path):
        """The command layer (snapadmin_db_backup.py) calls run_backup()/
        run_due_backups() unmodified — sharding support lives entirely in
        backup.py, so the command picks it up for free."""
        local_dir = tmp_path / "local"
        with override_settings(
            SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_LOCAL_DIR=str(local_dir),
        ):
            out = StringIO()
            call_command("snapadmin_db_backup", "--destination", "local", stdout=out)

        assert "Backup complete" in out.getvalue()
        stored = sorted(p.name for p in local_dir.iterdir())
        assert any(n.startswith(f"{BACKUP_PREFIX}shard_1-") for n in stored)
        assert any(n.startswith(f"{BACKUP_PREFIX}shard_2-") for n in stored)
