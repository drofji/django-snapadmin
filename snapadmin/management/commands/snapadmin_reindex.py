"""
Bulk-reindex SnapModels into Elasticsearch.

    python manage.py snapadmin_reindex                       # every ES-enabled SnapModel
    python manage.py snapadmin_reindex --model demo.Product  # one model
    python manage.py snapadmin_reindex --chunk-size 1000
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from snapadmin.models import SnapModel, reindexable_snapmodels


class Command(BaseCommand):
    help = "Bulk-reindex all ES-enabled SnapModels (or one --model) into Elasticsearch."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            help="Reindex only this model, as app_label.ModelName (e.g. demo.Product).",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=500,
            help="Documents per bulk request (default: 500).",
        )

    def handle(self, *args, **options):
        if options["model"]:
            try:
                app_label, model_name = options["model"].split(".", 1)
                model = apps.get_model(app_label, model_name)
            except (ValueError, LookupError):
                raise CommandError(f"Unknown model: {options['model']} (use app_label.ModelName)")
            if not (isinstance(model, type) and issubclass(model, SnapModel)):
                raise CommandError(f"{options['model']} is not a SnapModel.")
            models = [model]
        else:
            models = reindexable_snapmodels()
            if not models:
                self.stdout.write("No ES-enabled SnapModels found — nothing to reindex.")
                return

        failed = False
        for model in models:
            label = f"{model._meta.app_label}.{model.__name__}"
            summary = model.es_reindex_all(chunk_size=options["chunk_size"])
            if summary.get("skipped"):
                self.stdout.write(f"{label}: skipped ({summary['reason']})")
            elif summary.get("errors"):
                failed = True
                self.stdout.write(self.style.ERROR(
                    f"{label}: {summary.get('indexed', 0)} indexed, "
                    f"{len(summary['errors'])} errors"
                ))
            else:
                self.stdout.write(self.style.SUCCESS(f"{label}: {summary['indexed']} indexed"))

        if failed:
            raise CommandError("Reindex finished with errors (see above).")
