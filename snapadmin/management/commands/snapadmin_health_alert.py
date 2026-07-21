"""
Probe SnapAdmin subsystem health and email the recipients when one is down.

Cron-friendly alternative to the ``snapadmin.send_health_alert`` Celery task for
deployments without a Celery worker:

    */5 * * * *  cd /app && python demo/manage.py snapadmin_health_alert

Runs the same health probes as ``snapadmin_info --health-check`` (database,
Elasticsearch, REST API, GraphQL — each skipped when its feature toggle is off, so
a disabled subsystem is never a false alarm) and, when one reports a failure,
emails ``SNAPADMIN_HEALTH_ALERT_EMAILS`` (falling back to
``SNAPADMIN_ERROR_ALERT_EMAILS``).
A cache-based cooldown limits a persistent outage to one email per
``SNAPADMIN_HEALTH_ALERT_COOLDOWN_MINUTES``. The command **exits non-zero whenever
a probe is failing**, so it doubles as a monitoring health gate; ``--force``
bypasses the cooldown so an alert is (re)sent immediately.
"""

from django.core.management.base import BaseCommand, CommandError

from snapadmin.health import send_health_alert


class Command(BaseCommand):
    help = "Email an alert when a SnapAdmin health probe (DB, Elasticsearch, REST API, GraphQL) is down."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send the alert even if the cooldown window has not elapsed.",
        )

    def handle(self, *args, **options):
        summary = send_health_alert(force=options["force"])
        failing = summary["failing"]
        checked = summary["checked"]

        if summary["sent"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Health alert sent: {failing} of {checked} probe(s) down "
                    f"({summary['failing_names']}) to {summary['recipients']} recipient(s)."
                )
            )
        elif summary["reason"] == "healthy":
            self.stdout.write(
                self.style.SUCCESS(f"All {checked} health probe(s) OK — no alert sent.")
            )
        else:
            self.stdout.write(
                f"Alert not sent ({summary['reason']}): {failing} of {checked} probe(s) down."
            )

        if failing:
            raise CommandError(f"{failing} health probe(s) failing.")
