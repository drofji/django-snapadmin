"""
management/commands/purge_expired_data.py

Run GDPR data retention cleanup manually.
Equivalent to calling the purge_expired_data Celery task synchronously.

Usage:
    python manage.py purge_expired_data [--dry-run]
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete records that exceed their model's data_retention_days limit (GDPR)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be deleted without actually deleting anything",
        )

    def handle(self, *args, **options):
        from django.apps import apps
        from django.utils import timezone
        from snapadmin.models import SnapModel

        dry_run: bool = options["dry_run"]
        now = timezone.now()
        total = 0

        for model in apps.get_models():
            if not (isinstance(model, type) and issubclass(model, SnapModel) and model is not SnapModel):
                continue

            retention_days = getattr(model, "data_retention_days", None)
            if not retention_days or retention_days <= 0:
                continue

            label = f"{model._meta.app_label}.{model.__name__}"

            try:
                count = model.purge_expired(now=now, dry_run=dry_run)
                if dry_run:
                    self.stdout.write(f"  DRY RUN {label}: {count} records would be deleted (older than {retention_days} days)")
                else:
                    self.stdout.write(self.style.SUCCESS(f"  DELETED {label}: {count} records (older than {retention_days} days)"))
                    total += count
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ERROR {label}: {exc}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nTotal deleted: {total}"))
        else:
            self.stdout.write("\nDry run complete - no data was deleted")
