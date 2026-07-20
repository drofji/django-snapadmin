"""
Report SnapAdmin's configuration, connected services and health in one place.

    python manage.py snapadmin_info                   # full report
    python manage.py snapadmin_info --json            # machine-readable (monitoring/CI)
    python manage.py snapadmin_info --section version # one section (repeatable)
    python manage.py snapadmin_info --brief           # top-level values only
    python manage.py snapadmin_info --health-check    # probes only; non-zero exit on failure

Each section is produced by a collector in :mod:`snapadmin.diagnostics`; new sections plug in as
new modules there. Secrets (passwords, keys, token values) are never printed.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from snapadmin.diagnostics import collect, get_collectors
from snapadmin.diagnostics.render import render_report


class Command(BaseCommand):
    help = "Show SnapAdmin configuration, connected services and health in one report."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit the raw report as JSON (for monitoring/CI).",
        )
        parser.add_argument(
            "--section",
            action="append",
            dest="sections",
            metavar="NAME",
            help="Limit to this section (repeatable).",
        )
        parser.add_argument(
            "--brief",
            action="store_true",
            help="Show top-level values only, without nested detail.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Include extra per-section detail.",
        )
        parser.add_argument(
            "--health-check",
            action="store_true",
            dest="health_check",
            help="Run only connection probes; exit non-zero if any fails.",
        )

    def handle(self, *args, **options):
        sections = options.get("sections")
        if sections:
            known = {collector.name for collector in get_collectors()}
            unknown = [name for name in sections if name not in known]
            if unknown:
                raise CommandError(
                    f"Unknown section(s): {', '.join(unknown)}. "
                    f"Available: {', '.join(sorted(known))}."
                )

        results = collect(
            sections=sections,
            verbose=options["verbose"],
            health_only=options["health_check"],
        )

        if options["as_json"]:
            payload = {collector.name: data for collector, data in results}
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        else:
            self.stdout.write(render_report(results, brief=options["brief"]))

        if options["health_check"]:
            failed = [collector.name for collector, data in results if data.get("ok") is False]
            if failed:
                raise CommandError(f"Health check failed: {', '.join(failed)}.")
            if not options["as_json"]:
                self.stdout.write(self.style.SUCCESS("Health check passed."))
