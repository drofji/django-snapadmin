"""
Send the grouped SnapAdmin error digest email.

Cron-friendly alternative to the ``snapadmin.send_error_digest`` Celery task
for deployments without a Celery worker:

    0 8 * * *  cd /app && python manage.py send_error_digest
"""

from django.core.management.base import BaseCommand

from snapadmin.monitoring import send_error_digest


class Command(BaseCommand):
    help = "Send the grouped error digest email (last 24 hours by default)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Report window in hours (default: 24).",
        )

    def handle(self, *args, **options):
        summary = send_error_digest(hours=options["hours"])
        if summary["sent"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Digest sent: {summary['errors']} errors in {summary['groups']} "
                    f"groups; purged {summary['purged']} expired events."
                )
            )
        else:
            self.stdout.write(
                f"Digest not sent ({summary['reason']}): {summary['errors']} errors; "
                f"purged {summary['purged']} expired events."
            )
