"""
Showcase the generic ETL helper: refresh currency rates from an "external" feed.

    python demo/manage.py sync_exchange_rates

Re-running is idempotent — rows match on `code`, so a second run updates the
rates in place instead of duplicating (demonstrating
`bulk_create(update_conflicts=True)` via `snapadmin.etl.upsert_from_source`).
"""

import random

from django.core.management.base import BaseCommand

from demo.app.models import ExchangeRate
from snapadmin.etl import upsert_from_source

# Stand-in for a response streamed from an external rates provider.
_CURRENCIES = ["USD", "GBP", "JPY", "CHF", "CAD", "AUD", "SEK", "NOK", "PLN", "CZK"]


def _feed_rows():
    for code in _CURRENCIES:
        yield {"code": code, "base": "EUR", "rate": round(random.uniform(0.1, 200), 6)}


class Command(BaseCommand):
    help = "Sync demo currency exchange rates via the SnapAdmin ETL upsert helper."

    def handle(self, *args, **options):
        summary = upsert_from_source(
            ExchangeRate,
            _feed_rows(),
            unique_fields=["code"],
        )
        self.stdout.write(self.style.SUCCESS(
            f"Synced {summary['processed']} rates in {summary['batches']} batch(es); "
            f"reindex: {summary['reindex']}"
        ))
