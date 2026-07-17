"""
demo/management/commands/benchmark_list_view.py

Benchmark an admin changelist queryset with vs without SnapAdmin's auto
``list_select_related`` optimization, printing query count and wall time for
each. This is the "before/after" instrument for roadmap task #0 — run it after
``python demo/manage.py seed_large`` to capture real numbers on a populated table.

Usage:
    python demo/manage.py benchmark_list_view                  # Order model, all rows
    python demo/manage.py benchmark_list_view --limit 1000     # cap rows scanned
    python demo/manage.py benchmark_list_view --model order    # pick the model

The benchmark iterates the queryset and touches each row's FK (``order.customer``)
to surface the N+1 storm in the unoptimized case.
"""

import time

from django.apps import apps
from django.contrib import admin
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, reset_queries
from django.test.utils import CaptureQueriesContext


class Command(BaseCommand):
    help = "Benchmark admin list-view querysets with vs without list_select_related."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            default="order",
            help="Demo model to benchmark (default: order).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max rows to scan (default: all).",
        )

    def handle(self, *args, **options):
        model_name = options["model"].lower()
        limit = options["limit"]

        try:
            model = apps.get_model("demo", model_name)
        except LookupError:
            raise CommandError(f"No demo model named '{model_name}'.")

        model_admin = admin.site._registry.get(model)
        if model_admin is None:
            raise CommandError(f"{model.__name__} is not registered in the admin.")

        select_related = model_admin.list_select_related
        if not select_related:
            self.stdout.write(self.style.WARNING(
                f"   {model.__name__} has no list_select_related — "
                "FK access won't show an N+1 contrast."
            ))
            select_related = []

        total = model.objects.count()
        self.stdout.write(self.style.MIGRATE_HEADING("⏱  List-View Benchmark"))
        self.stdout.write(f"   Model           : {model.__name__}")
        self.stdout.write(f"   Rows in table   : {total:,}")
        self.stdout.write(f"   Scan limit      : {limit if limit is not None else 'all'}")
        self.stdout.write(f"   list_select_related : {select_related or 'none'}")
        self.stdout.write("")

        fk_fields = [f.name for f in model._meta.fields if f.is_relation]

        baseline = self._run(model, [], limit, fk_fields, "WITHOUT select_related")
        optimized = self._run(model, select_related, limit, fk_fields, "WITH    select_related")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("📊  Result"))
        self._report("WITHOUT", baseline)
        self._report("WITH   ", optimized)

        if baseline["queries"] and optimized["queries"]:
            q_factor = baseline["queries"] / optimized["queries"]
            self.stdout.write("")
            self.stdout.write(
                f"   Query reduction : {baseline['queries']:,} → "
                f"{optimized['queries']:,}  ({q_factor:.0f}× fewer)"
            )
        if optimized["seconds"]:
            t_factor = baseline["seconds"] / optimized["seconds"]
            self.stdout.write(
                f"   Speedup         : {t_factor:.1f}× faster wall time"
            )

    def _run(self, model, select_related, limit, fk_fields, label):
        self.stdout.write(f"   Running: {label}…")
        reset_queries()
        qs = model.objects.all()
        if select_related:
            qs = qs.select_related(*select_related)
        if limit is not None:
            qs = qs[:limit]

        start = time.perf_counter()
        with CaptureQueriesContext(connection) as ctx:
            for obj in qs:
                # Touch every FK to trigger lazy loads in the unoptimized path.
                for name in fk_fields:
                    getattr(obj, name)
        seconds = time.perf_counter() - start
        return {"queries": len(ctx), "seconds": seconds}

    def _report(self, label, result):
        self.stdout.write(
            f"   {label} : {result['queries']:>8,} queries   "
            f"{result['seconds'] * 1000:>9.1f} ms"
        )
