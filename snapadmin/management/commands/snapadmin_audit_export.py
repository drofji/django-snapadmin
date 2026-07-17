"""
management/commands/snapadmin_audit_export.py

Export the SnapAdmin audit trail for an external SIEM.

Streams audit-log rows to stdout (or ``--output FILE``) as newline-delimited
JSON (default, ideal for log shippers) or CSV. Filter by time window, action,
app or model; optionally prune exported-and-aged rows in the same pass.

Usage::

    python manage.py snapadmin_audit_export
    python manage.py snapadmin_audit_export --format csv --output audit.csv
    python manage.py snapadmin_audit_export --since 2026-01-01 --action delete
    python manage.py snapadmin_audit_export --purge   # also delete rows past retention
    python manage.py snapadmin_audit_export --reveal-pii  # include raw PII values

By default, ``changes`` entries for a field listed in ``SNAPADMIN_MASKED_FIELDS``
are masked the same way the admin and REST API mask them — a SIEM/log-shipper
pipeline is a wider blast radius than the database itself, so PII should not
flow into it by default. Pass ``--reveal-pii`` to export the raw diff instead,
for an operator who explicitly needs it for an investigation.
"""

import csv
import json
import sys

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

FIELDS = [
    "id", "timestamp", "action", "actor_id", "actor_repr", "ip_address",
    "user_agent", "app_label", "model", "object_id", "object_repr", "changes",
]


class Command(BaseCommand):
    help = "Export the immutable audit trail (SnapadminAuditLog) as JSON lines or CSV for a SIEM"

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=["json", "csv"], default="json",
                            help="Output format (default: json — newline-delimited)")
        parser.add_argument("--output", default="-",
                            help="Output file path, or '-' for stdout (default)")
        parser.add_argument("--since", default=None,
                            help="Only rows at/after this ISO date or datetime")
        parser.add_argument("--until", default=None,
                            help="Only rows strictly before this ISO date or datetime")
        parser.add_argument("--action", choices=["create", "update", "delete"], default=None,
                            help="Filter by action")
        parser.add_argument("--app", default=None, help="Filter by app_label")
        parser.add_argument("--model", default=None, help="Filter by model name")
        parser.add_argument("--purge", action="store_true",
                            help="After exporting, delete rows older than SNAPADMIN_AUDIT_RETENTION_DAYS")
        parser.add_argument("--reveal-pii", action="store_true",
                            help="Export raw (unmasked) changes for SNAPADMIN_MASKED_FIELDS "
                                 "fields, instead of the default masked diff")

    def handle(self, *args, **options):
        from snapadmin.models import SnapadminAuditLog

        qs = SnapadminAuditLog.objects.all().order_by("timestamp")
        if options["since"]:
            qs = qs.filter(timestamp__gte=self._parse_when(options["since"], "--since"))
        if options["until"]:
            qs = qs.filter(timestamp__lt=self._parse_when(options["until"], "--until"))
        if options["action"]:
            qs = qs.filter(action=options["action"])
        if options["app"]:
            qs = qs.filter(app_label=options["app"])
        if options["model"]:
            qs = qs.filter(model=options["model"])

        reveal_pii = options["reveal_pii"]
        out = sys.stdout if options["output"] == "-" else open(options["output"], "w", newline="")
        try:
            count = (
                self._write_csv(qs, out, reveal_pii) if options["format"] == "csv"
                else self._write_json(qs, out, reveal_pii)
            )
        finally:
            if out is not sys.stdout:
                out.close()

        self.stderr.write(self.style.SUCCESS(f"Exported {count} audit row(s)."))

        if options["purge"]:
            deleted = self._purge()
            self.stderr.write(self.style.SUCCESS(f"Purged {deleted} row(s) past retention."))

    def _parse_when(self, value: str, flag: str):
        # parse_datetime accepts both ISO dates ("2026-01-01" → midnight) and
        # full ISO datetimes; naive values are made aware in the current TZ.
        dt = parse_datetime(value)
        if dt is None:
            raise CommandError(f"{flag}: could not parse '{value}' as an ISO date/datetime")
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    @staticmethod
    def _row(entry, reveal_pii: bool) -> dict:
        changes = entry.changes
        if not reveal_pii:
            from snapadmin.masking import mask_changes
            changes = mask_changes(entry.app_label, entry.model, changes)
        return {
            "id": entry.id,
            "timestamp": entry.timestamp.isoformat(),
            "action": entry.action,
            "actor_id": entry.actor_id,
            "actor_repr": entry.actor_repr,
            "ip_address": entry.ip_address,
            "user_agent": entry.user_agent,
            "app_label": entry.app_label,
            "model": entry.model,
            "object_id": entry.object_id,
            "object_repr": entry.object_repr,
            "changes": changes,
        }

    def _write_json(self, qs, out, reveal_pii: bool) -> int:
        count = 0
        for entry in qs.iterator():
            out.write(json.dumps(self._row(entry, reveal_pii), default=str) + "\n")
            count += 1
        return count

    def _write_csv(self, qs, out, reveal_pii: bool) -> int:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        count = 0
        for entry in qs.iterator():
            row = self._row(entry, reveal_pii)
            row["changes"] = json.dumps(row["changes"], default=str) if row["changes"] else ""
            writer.writerow(row)
            count += 1
        return count

    @staticmethod
    def _purge() -> int:
        from django.conf import settings
        from snapadmin.models import SnapadminAuditLog

        days = int(getattr(settings, "SNAPADMIN_AUDIT_RETENTION_DAYS", 365))
        if days <= 0:
            return 0
        from datetime import timedelta
        cutoff = timezone.now() - timedelta(days=days)
        # QuerySet.delete() bypasses the per-instance immutability guard by
        # design — retention pruning is the one sanctioned way to remove rows.
        deleted, _ = SnapadminAuditLog.objects.filter(timestamp__lt=cutoff).delete()
        return deleted
