"""
Run 3-2-1 database backups from the CLI / cron.

    python manage.py snapadmin_db_backup                      # only destinations that are due
    python manage.py snapadmin_db_backup --force              # all configured destinations now
    python manage.py snapadmin_db_backup --destination remote # one destination, right now

``db_backup`` (no prefix) still works as a deprecated alias — see
:mod:`snapadmin.management.aliases`.
"""

from django.core.management.base import BaseCommand, CommandError

from snapadmin.backup import (
    DESTINATIONS,
    BackupError,
    _active_destinations,
    get_backup_config,
    run_backup,
    run_due_backups,
)


class Command(BaseCommand):
    help = "Back up the database to the configured 3-2-1 destinations (local / network / remote FTP / SFTP)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--destination",
            choices=DESTINATIONS,
            help="Back up to this destination immediately, ignoring its schedule.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Back up to every configured destination now, ignoring schedules.",
        )

    def handle(self, *args, **options):
        config = get_backup_config()
        try:
            if options["destination"]:
                summary = run_backup([options["destination"]], config=config)
            elif options["force"]:
                summary = run_backup(_active_destinations(config), config=config)
            else:
                summary = run_due_backups()
        except BackupError as exc:
            # Mirrors snapadmin_reindex.py's CommandError-on-failure pattern —
            # every destination failed, or the dump itself could not be
            # built, must reach cron as a clean non-zero exit, not a raw
            # traceback (see run_backup()'s docstring in backup.py).
            raise CommandError(str(exc)) from exc

        if not summary["ran"]:
            self.stdout.write(f"No backup performed ({summary['reason']}).")
            return

        failed = False
        for destination, result in summary["results"].items():
            if result.startswith("error:"):
                failed = True
                self.stdout.write(self.style.ERROR(f"{destination}: {result}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"{destination}: {result}"))
        if failed:
            raise CommandError(f"Some backup destinations failed (dump: {summary['dump']}).")
        self.stdout.write(self.style.SUCCESS(f"Backup complete: {summary['dump']}"))
