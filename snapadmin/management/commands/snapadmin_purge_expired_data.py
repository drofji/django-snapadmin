"""
Run the GDPR data-retention cleanup manually.

Equivalent to calling the ``snapadmin.purge_expired_data`` Celery task synchronously, for
deployments with no worker:

    python manage.py snapadmin_purge_expired_data [--dry-run]

``purge_expired_data`` (no prefix) still works as a deprecated alias — see
:mod:`snapadmin.management.aliases`.
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
        from snapadmin.registry import get_model_meta, is_registered

        dry_run: bool = options["dry_run"]
        now = timezone.now()
        total = 0

        for model in apps.get_models():
            # ``purge_expired`` is SnapModel's own — a plain model registered with
            # @snap_model gets no retention purge, so it is skipped here.
            if not (is_registered(model) and hasattr(model, "purge_expired")):
                continue

            retention_days = get_model_meta(model, "data_retention_days", None)
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
