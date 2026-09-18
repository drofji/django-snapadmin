"""
Run the GDPR data-retention cleanup manually.

Equivalent to calling the ``snapadmin.purge_expired_data`` Celery task synchronously, for
deployments with no worker:

    python manage.py snapadmin_purge_expired_data [--dry-run]

Purges, in order: every registered SnapModel's ``data_retention_days`` /
``data_retention_date_field`` (and any ``data_retention_files`` storage
objects), the ``SnapadminAuditLog`` table
against ``SNAPADMIN_AUDIT_RETENTION_DAYS``, and — when
``SNAPADMIN_EXPORT_RETENTION_DAYS`` is set — finished export/reindex job rows
and their published files.
"""

from django.core.management.base import BaseCommand


def _retention_rule_description(model, get_model_meta) -> str:
    """Why each of this model's rows is up for deletion, for the report line.

    A model can be swept by a model-wide age window, by a per-row deadline
    column, or by both; printing "older than None days" for the second of those
    would describe a rule nobody configured.
    """
    days = get_model_meta(model, "data_retention_days", None)
    date_field = get_model_meta(model, "data_retention_date_field", None)
    clauses = []
    if date_field:
        clauses.append(f"past their {date_field}")
    if days and days > 0:
        field = get_model_meta(model, "data_retention_field", "created_at")
        older = f"older than {days} days"
        clauses.append(f"{older} by {field}" if date_field else older)
    return ", or ".join(clauses)


class Command(BaseCommand):
    help = "Delete records past their model's retention rules (GDPR)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be deleted without actually deleting anything",
        )

    def handle(self, *args, **options):
        from django.apps import apps
        from django.utils import timezone
        from snapadmin.exporting import purge_expired_export_jobs
        from snapadmin.models import SnapadminAuditLog, _retention_configured
        from snapadmin.registry import get_model_meta, is_registered

        dry_run: bool = options["dry_run"]
        now = timezone.now()
        total = 0

        for model in apps.get_models():
            # ``purge_expired`` is SnapModel's own — a plain model registered with
            # @snap_model gets no retention purge, so it is skipped here.
            if not (is_registered(model) and hasattr(model, "purge_expired")):
                continue

            if not _retention_configured(model):
                continue

            label = f"{model._meta.app_label}.{model.__name__}"
            rule = _retention_rule_description(model, get_model_meta)

            try:
                count = model.purge_expired(now=now, dry_run=dry_run)
                if dry_run:
                    self.stdout.write(f"  DRY RUN {label}: {count} records would be deleted ({rule})")
                else:
                    self.stdout.write(self.style.SUCCESS(f"  DELETED {label}: {count} records ({rule})"))
                    total += count
                    # Due rows a PROTECT/RESTRICT foreign key still holds (#EXT2a):
                    # kept for the next run, reported so the run never quietly
                    # does less than it says.
                    skipped = getattr(count, "skipped_protected", 0)
                    if skipped:
                        self.stdout.write(self.style.WARNING(
                            f"  SKIPPED {label}: {skipped} records kept — still referenced "
                            "by a PROTECT/RESTRICT foreign key; retried on the next run"
                        ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ERROR {label}: {exc}"))

        # The audit log is append-only and deliberately outside the general
        # SnapAdmin registry (see SnapadminAuditLog's docstring), so it is not
        # swept by the loop above — purge it explicitly against its own
        # SNAPADMIN_AUDIT_RETENTION_DAYS instead.
        audit_retention_days = SnapadminAuditLog.data_retention_days()
        if audit_retention_days > 0:
            label = f"snapadmin.{SnapadminAuditLog.__name__}"
            try:
                count = SnapadminAuditLog.purge_expired(now=now, dry_run=dry_run)
                if dry_run:
                    self.stdout.write(f"  DRY RUN {label}: {count} records would be deleted (older than {audit_retention_days} days)")
                else:
                    self.stdout.write(self.style.SUCCESS(f"  DELETED {label}: {count} records (older than {audit_retention_days} days)"))
                    total += count
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ERROR {label}: {exc}"))

        # Export/reindex job rows and their files (#RET2b) — opt-in via
        # SNAPADMIN_EXPORT_RETENTION_DAYS, unlike the two sweeps above.
        export_purge = purge_expired_export_jobs(now=now, dry_run=dry_run)
        if export_purge["enabled"]:
            for job_label, count in export_purge["jobs_deleted"].items():
                label = f"snapadmin.{job_label}"
                if dry_run:
                    self.stdout.write(f"  DRY RUN {label}: {count} job(s) would be deleted")
                else:
                    self.stdout.write(self.style.SUCCESS(f"  DELETED {label}: {count} job(s)"))
                    total += count
            files_label = "DRY RUN" if dry_run else "DELETED"
            self.stdout.write(
                f"  {files_label} snapadmin.export_files: "
                f"{export_purge['files_deleted']} file(s), "
                f"{export_purge['orphan_files_deleted']} orphan file(s)"
            )
            for failure in export_purge["failed"]:
                self.stdout.write(self.style.ERROR(f"  ERROR snapadmin.export_jobs: {failure}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nTotal deleted: {total}"))
        else:
            self.stdout.write("\nDry run complete - no data was deleted")
