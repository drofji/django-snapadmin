"""
Roll back to a pre-restore snapshot taken automatically by ``snapadmin_restore``.

    python manage.py snapadmin_rollback --list
    python manage.py snapadmin_rollback [<snapshot-id>] [--identity PATH] [--confirm]

With no ``<snapshot-id>``, the most recent snapshot is used (and named). Dry-run is the
default, exactly like ``snapadmin_restore``: without ``--confirm``, this prints what
would happen and touches nothing.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from snapadmin.backup import get_backup_config
from snapadmin.restore import (
    ResolvedSource,
    RestoreError,
    identity_required_message,
    perform_restore,
    plan_restore,
)
from snapadmin.snapshot import (
    SnapshotError,
    latest_snapshot_id,
    list_snapshots,
    load_snapshot_manifest,
)


class Command(BaseCommand):
    help = "Roll back to a pre-restore snapshot (dry-run by default; pass --confirm to actually roll back)."

    def add_arguments(self, parser):
        parser.add_argument(
            "snapshot_id", nargs="?",
            help="Snapshot to roll back to. Defaults to the most recent one.",
        )
        parser.add_argument(
            "--list", action="store_true",
            help="List available snapshots without rolling back anything.",
        )
        parser.add_argument(
            "--identity", default="",
            help="Path to the AGE identity (private key) file, if the snapshot is encrypted.",
        )
        parser.add_argument(
            "--confirm", action="store_true",
            help="Actually perform the rollback. Without this, only a plan is printed.",
        )

    def handle(self, *args, **options):
        config = get_backup_config()

        if options["list"]:
            self._handle_list(config)
            return

        snapshot_id = options["snapshot_id"] or latest_snapshot_id(config)
        if not snapshot_id:
            raise CommandError("No snapshots found — nothing to roll back to.")
        if not options["snapshot_id"]:
            self.stdout.write(f"Using most recent snapshot: {snapshot_id}")

        try:
            run_dir, manifest = load_snapshot_manifest(snapshot_id, config)
        except SnapshotError as exc:
            raise CommandError(str(exc)) from exc

        # A snapshot's part filenames are relative to run_dir, not the cwd or
        # the configured local backup dir — resolve each to an absolute path
        # so perform_restore()'s local-fetch branch (Path(name).is_file())
        # finds it directly, no snapshot-specific fetch logic needed.
        resolved_manifest = dict(manifest)
        resolved_manifest["parts"] = {
            part: {**entry, "filename": str(run_dir / entry["filename"])}
            for part, entry in manifest.get("parts", {}).items()
        }
        resolved = ResolvedSource(destination=None, manifest_path=run_dir, manifest=resolved_manifest)
        parts = sorted(manifest.get("parts", {}))

        if manifest.get("encrypted") and not options["identity"]:
            self.stdout.write(self.style.WARNING(identity_required_message(manifest)))

        for line in plan_restore(resolved, parts):
            self.stdout.write(line)

        if not options["confirm"]:
            self.stdout.write(self.style.WARNING(
                "\nDry run — nothing was changed. Pass --confirm to perform this rollback."
            ))
            return

        # A snapshot's parts already live in run_dir; perform_restore()'s
        # fetch step resolves them as local paths (destination=None), so a
        # rollback goes through the exact same fetch/checksum/decrypt/apply
        # pipeline a real restore does — no separate code path to trust.
        try:
            results = perform_restore(resolved, parts, config, identity_file=options["identity"])
        except RestoreError as exc:
            raise CommandError(str(exc)) from exc

        for part, result in results.items():
            self.stdout.write(self.style.SUCCESS(f"{part}: {result}"))
        self.stdout.write(self.style.SUCCESS(f"Rollback to {snapshot_id} complete."))

    def _handle_list(self, config):
        snapshots = list_snapshots(config)
        if not snapshots:
            self.stdout.write("No snapshots found.")
            return
        for snap in snapshots:
            parts = ",".join(snap["parts"])
            encrypted = " (encrypted)" if snap["encrypted"] else ""
            self.stdout.write(f"{snap['id']}  parts={parts}{encrypted}")
