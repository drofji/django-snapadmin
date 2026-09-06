"""
snapadmin/management/commands/snap_migrate.py

Run ``migrate`` against every ``SNAPADMIN_SHARDING`` shard's primary database
— the sharded counterpart of ``manage.py migrate``. Never touches a replica:
replication propagates schema at the DB layer, so migrating one directly
would fight the replication stream (``SnapAdminRouter.allow_migrate`` already
refuses this at the router level too — this command simply never asks it to).

    python manage.py snap_migrate              # every shard's primary, one after another
    python manage.py snap_migrate --parallel   # all shards' primaries at once (ThreadPoolExecutor)

A clear, explicit no-op — never silent — when ``SNAPADMIN_SHARDING`` is
unset/disabled: there is nothing sharded to migrate, use the plain
``manage.py migrate`` instead. One shard failing does not stop the others —
every shard's outcome is reported, and the command exits non-zero only after
all of them have run.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from snapadmin.logging_config import get_logger
from snapadmin.sharding.registration import is_sharding_enabled, iter_primary_aliases

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Run migrate against every SNAPADMIN_SHARDING shard's primary database, never a replica."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--parallel",
            action="store_true",
            help="Migrate every shard's primary at once (ThreadPoolExecutor) instead of one at a time.",
        )

    def _migrate_one(self, alias: str, verbosity: int) -> tuple[bool, str]:
        buffer = io.StringIO()
        try:
            call_command("migrate", database=alias, verbosity=verbosity, stdout=buffer, stderr=buffer)
        except Exception as exc:
            logger.error("snap_migrate_shard_failed", db_alias=alias, error=str(exc))
            return False, str(exc)
        return True, buffer.getvalue()

    def _migrate_sequential(self, aliases: list[str], verbosity: int) -> dict[str, tuple[bool, str]]:
        return {alias: self._migrate_one(alias, verbosity) for alias in aliases}

    def _migrate_parallel(self, aliases: list[str], verbosity: int) -> dict[str, tuple[bool, str]]:
        results: dict[str, tuple[bool, str]] = {}
        with ThreadPoolExecutor(max_workers=len(aliases)) as executor:
            future_to_alias = {
                executor.submit(self._migrate_one, alias, verbosity): alias for alias in aliases
            }
            for future in as_completed(future_to_alias):
                alias = future_to_alias[future]
                results[alias] = future.result()
        return results

    def handle(self, *args: Any, **options: Any) -> None:
        if not is_sharding_enabled():
            self.stdout.write(
                "SNAPADMIN_SHARDING is not enabled — nothing to migrate. Use `manage.py migrate`."
            )
            return

        aliases = iter_primary_aliases()
        if not aliases:
            raise CommandError(
                "SNAPADMIN_SHARDING is enabled but resolves to zero shards — run `manage.py check` "
                "to see why."
            )

        verbosity = options["verbosity"]
        migrate = self._migrate_parallel if options["parallel"] else self._migrate_sequential
        results = migrate(aliases, verbosity)

        failed_aliases = []
        for alias in aliases:  # report in a deterministic order, not completion order
            ok, message = results[alias]
            if ok:
                if message:
                    self.stdout.write(message.rstrip("\n"))
                self.stdout.write(self.style.SUCCESS(f"{alias}: migrated"))
            else:
                failed_aliases.append(alias)
                self.stdout.write(self.style.ERROR(f"{alias}: FAILED — {message}"))

        if failed_aliases:
            raise CommandError(
                f"snap_migrate failed for {len(failed_aliases)} of {len(aliases)} shard(s): "
                f"{', '.join(failed_aliases)}."
            )
