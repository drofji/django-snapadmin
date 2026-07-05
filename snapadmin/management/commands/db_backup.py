"""
Run 3-2-1 database backups from the CLI / cron.

    python manage.py db_backup                      # only destinations that are due
    python manage.py db_backup --force              # all configured destinations now
    python manage.py db_backup --destination remote # one destination, right now
"""

from django.core.management.base import BaseCommand, CommandError

from snapadmin.backup import (
    DESTINATIONS,
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
        if options["destination"]:
            summary = run_backup([options["destination"]], config=config)
        elif options["force"]:
            summary = run_backup(_active_destinations(config), config=config)
        else:
            summary = run_due_backups()

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
