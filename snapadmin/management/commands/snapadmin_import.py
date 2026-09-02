"""
Import CSV/NDJSON rows into a SnapAdmin model, with a per-row NDJSON report.

    python manage.py snapadmin_import --model demo.Product --file products.csv
    python manage.py snapadmin_import --model demo.Product --file products.csv --format csv
    python manage.py snapadmin_import --model demo.Product --file p.csv \\
        --map '{"Product Name": "name"}' --natural-key name --on-conflict update
    python manage.py snapadmin_import --model demo.Product --file products.csv --resume

See :mod:`snapadmin.importing` for the full import contract (column mapping,
the natural-key duplicate rule, the ``on_conflict`` modes, the write-surface
guard). The command is the thin caller — :class:`~snapadmin.models.SnapImportJob`
and :func:`snapadmin.importing.run_import_job` are the engine, and running the
same command again with ``--resume`` continues a crashed run rather than
restarting the file.

The run's report — one NDJSON line per row plus a summary line — is written
through the same storage seam the export API downloads from
(``SNAPADMIN_EXPORT_STORAGE`` / :func:`snapadmin.exporting.export_dir`); its
path is printed at the end of a run.
"""

import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from snapadmin.importing import (
    SnapImportError, export_dir, import_chunk_size, run_import_job, start_import,
)
from snapadmin.registry import is_registered


class Command(BaseCommand):
    help = "Import CSV/NDJSON rows into a SnapAdmin model, with a per-row NDJSON report."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model", required=True,
            help="Target model, as app_label.ModelName (e.g. demo.Product).",
        )
        parser.add_argument("--file", required=True, help="Path to the CSV or NDJSON input file.")
        parser.add_argument(
            "--format", choices=["csv", "json"], default=None,
            help="Input format. Inferred from --file's extension when omitted "
                 "(.csv -> csv; .json/.ndjson/.jsonl -> json).",
        )
        parser.add_argument(
            "--map", default=None,
            help='Explicit header -> field-name overrides, as JSON: \'{"CSV Header": "field_name"}\'. '
                 "Wins over header-name matching wherever a header is named here.",
        )
        parser.add_argument(
            "--natural-key", default=None,
            help="Comma-separated field name(s) that identify a duplicate row. Defaults to the "
                 "model's first unique=True field, or the primary key if the file carries it.",
        )
        parser.add_argument(
            "--on-conflict", choices=["fail", "skip", "update"], default="fail",
            help="What to do on a duplicate-key hit (default: fail — reported per-row, never "
                 "a silent overwrite).",
        )
        parser.add_argument(
            "--chunk-size", type=int, default=None,
            help=f"Rows per checkpoint (default: SNAPADMIN_IMPORT_CHUNK_SIZE, "
                 f"currently {import_chunk_size()}).",
        )
        parser.add_argument(
            "--resume", action="store_true",
            help="Continue the most recent unfinished/failed job for this model and file "
                 "from its checkpoint.",
        )
        parser.add_argument(
            "--requested-by", default=None,
            help="Username to attribute this run to — required to import into a column "
                 "targeting a masked/PII field (a run with no requester has no PII access).",
        )
        parser.add_argument(
            "--tenant", default=None,
            help="The tenant every row this run creates is assigned to — required when "
                 "--model is tenant-scoped (snapadmin.tenancy); there is no request here "
                 "to resolve one from. Ignored for a model that is not tenant-scoped.",
        )

    def handle(self, *args, **options):
        try:
            app_label, model_name = options["model"].split(".", 1)
            model = apps.get_model(app_label, model_name)
        except (ValueError, LookupError):
            raise CommandError(f"Unknown model: {options['model']} (use app_label.ModelName)")
        if not is_registered(model):
            raise CommandError(f"{options['model']} is not a SnapAdmin model.")

        column_map = None
        if options["map"]:
            try:
                column_map = json.loads(options["map"])
            except json.JSONDecodeError as exc:
                raise CommandError(f"--map is not valid JSON: {exc}")
            if not isinstance(column_map, dict):
                raise CommandError("--map must be a JSON object, e.g. '{\"CSV Header\": \"field\"}'.")

        natural_key = None
        if options["natural_key"]:
            natural_key = [name.strip() for name in options["natural_key"].split(",") if name.strip()]

        requested_by = None
        if options["requested_by"]:
            User = get_user_model()
            try:
                requested_by = User.objects.get(**{User.USERNAME_FIELD: options["requested_by"]})
            except User.DoesNotExist:
                raise CommandError(f"No user named {options['requested_by']!r}.")

        try:
            job = start_import(
                model,
                file_path=options["file"],
                import_format=options["format"],
                column_map=column_map,
                natural_key=natural_key,
                on_conflict=options["on_conflict"],
                tenant=options["tenant"],
                requested_by=requested_by,
                resume=options["resume"],
            )
        except (SnapImportError, OSError) as exc:
            raise CommandError(str(exc))

        def _progress(job):
            self.stdout.write(f"  {job.processed_rows}/{job.total_rows} rows processed")
            self.stdout.flush()

        summary = run_import_job(
            job, file_path=options["file"],
            chunk_size=options["chunk_size"], on_progress=_progress,
        )

        report_path = f"{export_dir()}/{job.report_file_name}" if job.report_file_name else None

        if summary.get("skipped"):
            self.stdout.write(f"skipped ({summary['reason']})")
        elif summary.get("cancelled"):
            self.stdout.write(self.style.WARNING(
                f"cancelled — {summary['created']} created, {summary['updated']} updated, "
                f"{summary['skipped']} skipped, {summary['failed']} failed so far"
            ))
        elif "errors" in summary:
            raise CommandError(f"Import failed: {summary['errors'][0]}")
        else:
            unmapped = summary.get("unmapped_columns") or []
            suffix = f" ({len(unmapped)} unmapped column(s): {', '.join(unmapped)})" if unmapped else ""
            self.stdout.write(self.style.SUCCESS(
                f"{summary['created']} created, {summary['updated']} updated, "
                f"{summary['skipped']} skipped, {summary['failed']} failed{suffix}"
            ))
            if report_path:
                self.stdout.write(f"Report: {report_path}")
            if summary.get("failed"):
                raise CommandError(
                    f"{summary['failed']} row(s) failed — see the report for details."
                )
