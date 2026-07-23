"""
Showcase the generic ETL helpers: refresh currency rates from an "external" feed.

    python demo/manage.py sync_exchange_rates
    python demo/manage.py sync_exchange_rates --only 7 --prune

Re-running is idempotent — rows match on `code`, so a second run updates the
rates in place instead of duplicating (demonstrating
`bulk_create(update_conflicts=True)` via `snapadmin.etl.upsert_from_source`).

`--prune` then runs `snapadmin.etl.stale_sync` to delete any local currency the
feed no longer reports — the delete half of a recurring sync. Combine with
`--only N` to shrink the feed and watch the stale rows get removed (guarded by
`max_fraction` so a shrunken feed can't wipe the table by accident).

`--strategy` picks how stale rows are found:

  * `keyset` (default) diffs the local `code`s against the feed in memory, then
    raises `StaleSyncAbort` if too many are stale (the classic guard).
  * `last_seen` is DB-side and holds no key set: every synced row is stamped with
    `run_started` (the `last_seen` watermark column), then `stale_sync` deletes
    rows left below it. It runs with `on_exceed="skip"`, so an over-the-ceiling
    prune logs and continues (returns `aborted=True`) instead of raising — the
    posture an unattended job usually wants.
"""

import random

from django.core.management.base import BaseCommand
from django.utils import timezone

from demo.apps.shop.models import ExchangeRate
from snapadmin.etl import StaleSyncAbort, stale_sync, upsert_from_source

# Stand-in for a response streamed from an external rates provider.
_CURRENCIES = ["USD", "GBP", "JPY", "CHF", "CAD", "AUD", "SEK", "NOK", "PLN", "CZK"]


def _feed_rows(codes, run_started):
    for code in codes:
        yield {
            "code": code, "base": "EUR",
            "rate": round(random.uniform(0.1, 200), 6),
            # Watermark for the last_seen prune strategy — every reported row is
            # stamped with this run's start time so stale rows fall below it.
            "last_seen": run_started,
        }


class Command(BaseCommand):
    help = "Sync demo currency exchange rates via the SnapAdmin ETL upsert helper."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only", type=int, default=len(_CURRENCIES),
            help="Only report the first N currencies from the feed (to demo pruning).",
        )
        parser.add_argument(
            "--prune", action="store_true",
            help="After upserting, delete currencies the feed no longer reports (stale_sync).",
        )
        parser.add_argument(
            "--strategy", choices=["keyset", "last_seen"], default="keyset",
            help="How --prune finds stale rows: keyset (in-memory diff) or last_seen (DB-side watermark).",
        )

    def handle(self, *args, **options):
        run_started = timezone.now()
        codes = _CURRENCIES[: options["only"]]
        summary = upsert_from_source(
            ExchangeRate,
            _feed_rows(codes, run_started),
            unique_fields=["code"],
        )
        self.stdout.write(self.style.SUCCESS(
            f"Synced {summary['processed']} rates in {summary['batches']} batch(es); "
            f"reindex: {summary['reindex']}"
        ))

        if not options["prune"]:
            return

        # max_fraction=0.5 keeps this demo runnable with a shrunk feed; production
        # syncs should keep the default 0.1 guard.
        if options["strategy"] == "last_seen":
            pruned = stale_sync(
                ExchangeRate, strategy="last_seen", last_seen_field="last_seen",
                run_started=run_started, max_fraction=0.5, on_exceed="skip",
            )
            if pruned["aborted"]:
                self.stderr.write(self.style.WARNING(
                    f"Prune skipped (safety guard): {pruned['fraction']:.0%} of "
                    f"{pruned['total']} rows were stale."
                ))
                return
        else:
            try:
                pruned = stale_sync(
                    ExchangeRate, set(codes), key_field="code", max_fraction=0.5,
                )
            except StaleSyncAbort as exc:
                self.stderr.write(self.style.WARNING(f"Prune aborted (safety guard): {exc}"))
                return

        self.stdout.write(self.style.SUCCESS(
            f"Pruned {pruned['deleted']} stale currency row(s) "
            f"({pruned['fraction']:.0%} of {pruned['total']})."
        ))
