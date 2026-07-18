"""
Bulk-reindex SnapModels into Elasticsearch, with live progress and resume.

    python manage.py snapadmin_reindex                        # every ES-enabled SnapModel
    python manage.py snapadmin_reindex --model demo.Product   # one model
    python manage.py snapadmin_reindex --chunk-size 1000      # docs per bulk request
    python manage.py snapadmin_reindex --tune                 # relax refresh/replicas for the load
    python manage.py snapadmin_reindex --parallel 4           # fan out with parallel_bulk
    python manage.py snapadmin_reindex --resume               # continue a crashed run from its checkpoint

Each model's run is tracked on a ``SnapReindexJob`` row: progress is printed as
it goes, a crash leaves a resumable checkpoint (``--resume`` continues from the
last indexed pk instead of restarting the table), and the run is cancellable by
setting the job's status to ``cancelled``.
"""

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from snapadmin.models import SnapModel, reindexable_snapmodels
from snapadmin.reindexing import DEFAULT_CHUNK_SIZE, run_reindex_job, start_reindex


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
            default=DEFAULT_CHUNK_SIZE,
            help=f"Documents per bulk request (default: {DEFAULT_CHUNK_SIZE}).",
        )
        parser.add_argument(
            "--parallel",
            type=int,
            default=0,
            help="Index each chunk with this many parallel_bulk threads (default: 0 = serial bulk).",
        )
        parser.add_argument(
            "--tune",
            action="store_true",
            help="Disable the index refresh and drop replicas to 0 for the load, restored afterwards.",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Continue the most recent unfinished/failed job for the model from its checkpoint.",
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

        if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
            for model in models:
                label = f"{model._meta.app_label}.{model.__name__}"
                self.stdout.write(f"{label}: skipped (Elasticsearch not available)")
            return

        failed = False
        for model in models:
            label = f"{model._meta.app_label}.{model.__name__}"

            def _progress(job, _label=label):
                eta = job.eta_seconds
                eta_str = f" ETA {eta}s" if eta else ""
                self.stdout.write(
                    f"  {_label}: {job.processed_rows}/{job.total_rows} "
                    f"({job.progress_percent}%){eta_str}"
                )

            job = start_reindex(model, resume=options["resume"])
            summary = run_reindex_job(
                job,
                chunk_size=options["chunk_size"],
                parallel=options["parallel"],
                tune=options["tune"],
                on_progress=_progress,
            )

            if summary.get("skipped"):
                self.stdout.write(f"{label}: skipped ({summary['reason']})")
            elif summary.get("cancelled"):
                self.stdout.write(self.style.WARNING(
                    f"{label}: cancelled at {summary['indexed']} rows"
                ))
            elif isinstance(summary.get("errors"), list):
                failed = True
                self.stdout.write(self.style.ERROR(
                    f"{label}: failed after {summary.get('indexed', 0)} rows — {summary['errors'][0]}"
                ))
            else:
                errors = summary.get("errors", 0)
                suffix = f", {errors} rejected" if errors else ""
                self.stdout.write(self.style.SUCCESS(
                    f"{label}: {summary['indexed']} indexed{suffix}"
                ))

        if failed:
            raise CommandError("Reindex finished with errors (see above).")
