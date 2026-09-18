"""
Restore a backup bundle produced by ``snapadmin_db_backup`` (#BKP1c/d).

    python manage.py snapadmin_restore --list [--destination sftp]
    python manage.py snapadmin_restore <source> [--only db,media,env] [--skip ...]
        [--identity PATH] [--database ALIAS] [--confirm] [--no-snapshot]

``<source>`` names a manifest — a local path, or ``<destination>:<name>`` (e.g.
``sftp:snapadmin-manifest-20260826-020000.json``) to pull straight from a configured
destination. Dry-run is the default: without ``--confirm``, this prints exactly what
would happen and touches nothing. ``--database ALIAS`` restores only the ``db`` part
into another ``DATABASES`` alias — a restore drill — and prints the row count per table.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from snapadmin.backup import DESTINATIONS, get_backup_config
from snapadmin.restore import (
    RestoreError,
    identity_required_message,
    list_bundles,
    perform_restore,
    plan_restore,
    resolve_source,
    select_parts,
    table_row_counts,
)


class Command(BaseCommand):
    help = "Restore a backup bundle (dry-run by default; pass --confirm to actually restore)."

    def add_arguments(self, parser):
        parser.add_argument(
            "source", nargs="?",
            help="Manifest to restore: a local path, or <destination>:<name>.",
        )
        parser.add_argument(
            "--list", action="store_true",
            help="List available manifests without restoring anything.",
        )
        parser.add_argument(
            "--destination", choices=DESTINATIONS,
            help="With --list, enumerate this destination instead of the local directory.",
        )
        parser.add_argument(
            "--only",
            help="Comma-separated subset of db,media,env to restore.",
        )
        parser.add_argument(
            "--skip",
            help="Comma-separated subset of db,media,env to exclude.",
        )
        parser.add_argument(
            "--identity",
            default="",
            help="Path to the AGE identity (private key) file, for an encrypted bundle.",
        )
        parser.add_argument(
            "--confirm", action="store_true",
            help="Actually perform the restore. Without this, only a plan is printed.",
        )
        parser.add_argument(
            "--no-snapshot", action="store_true",
            help="Skip the automatic pre-restore snapshot. Not recommended.",
        )
        parser.add_argument(
            "--database", default="default",
            help="DATABASES alias to restore the db part into (a restore drill). Only db "
                 "is restored then; an alias pointing at the same database as 'default' "
                 "is refused.",
        )

    def handle(self, *args, **options):
        config = get_backup_config()

        if options["list"]:
            self._handle_list(config, options["destination"])
            return

        source = options["source"]
        if not source:
            raise CommandError("A <source> is required unless --list is given.")

        work_dir = Path(tempfile.mkdtemp(prefix="snapadmin-restore-cmd-"))
        try:
            self._handle_restore(source, work_dir, config, options)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _handle_restore(self, source, work_dir, config, options):
        try:
            resolved = resolve_source(source, work_dir, config)
        except RestoreError as exc:
            raise CommandError(str(exc)) from exc

        only = options["only"].split(",") if options["only"] else None
        skip = options["skip"].split(",") if options["skip"] else None
        try:
            parts = select_parts(resolved.manifest, only=only, skip=skip)
        except RestoreError as exc:
            raise CommandError(str(exc)) from exc

        database = options["database"]
        if database != "default":
            if only is not None and any(part != "db" for part in only):
                raise CommandError(
                    f"Restoring into {database!r}: only the 'db' part can go to another "
                    "database — media and env would land on the live system."
                )
            parts = [part for part in parts if part == "db"]

        if resolved.manifest.get("encrypted") and not options["identity"]:
            self.stdout.write(self.style.WARNING(identity_required_message(resolved.manifest)))

        for line in plan_restore(resolved, parts, database):
            self.stdout.write(line)

        if not options["confirm"]:
            self.stdout.write(self.style.WARNING(
                "\nDry run — nothing was changed. Pass --confirm to perform this restore."
            ))
            return

        before_restore = None
        if database != "default":
            # The snapshot covers the live 'default' database; it would protect
            # nothing a drill into another alias touches.
            self.stdout.write(self.style.WARNING(
                f"Pre-restore snapshot skipped: it covers 'default', and this restore "
                f"replaces {database!r} only."
            ))
        elif not options["no_snapshot"]:
            from snapadmin.snapshot import take_snapshot

            def before_restore(restoring_parts: list[str]) -> None:
                snapshot_id = take_snapshot(restoring_parts, config)
                self.stdout.write(self.style.SUCCESS(f"Pre-restore snapshot taken: {snapshot_id}"))
        else:
            self.stdout.write(self.style.WARNING(
                "--no-snapshot: proceeding WITHOUT a pre-restore safety net."
            ))

        try:
            results = perform_restore(
                resolved, parts, config,
                identity_file=options["identity"], before_restore=before_restore,
                database=database,
            )
        except RestoreError as exc:
            raise CommandError(str(exc)) from exc

        for part, result in results.items():
            self.stdout.write(self.style.SUCCESS(f"{part}: {result}"))
        if database != "default" and "db" in results:
            for table, count in table_row_counts(database).items():
                self.stdout.write(f"  {table}: {count}")
        self.stdout.write(self.style.SUCCESS("Restore complete."))

    def _handle_list(self, config, destination):
        try:
            names = list_bundles(destination, config)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        if not names:
            self.stdout.write("No backup bundles found.")
            return
        for name in names:
            self.stdout.write(name)
