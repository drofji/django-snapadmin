"""
demo/management/commands/seed_large.py

Bulk-seed a large number of rows so the list-view optimizations from roadmap
task #0 can be benchmarked with real before/after timings.

Usage:
    python manage.py seed_large                       # 100,000 customers + orders
    python manage.py seed_large --count 250000        # custom row count
    python manage.py seed_large --batch-size 10000    # tune bulk_create batches
    python manage.py seed_large --flush               # wipe large-seed data first
    python manage.py seed_large --no-index            # skip Elasticsearch indexing

Seeds Customers and Orders (Order → Customer FK) specifically to exercise the
auto-derived ``list_select_related`` path on the Order admin list view. Pair it
with ``python manage.py benchmark_list_view`` to capture before/after numbers.

All inserts use ``bulk_create`` in batches so memory stays flat regardless of
``--count``.
"""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from demo.app.models import Customer, Order

FIRST_NAMES = [
    "Alice", "Bob", "Carol", "David", "Emma", "Frank", "Grace", "Henry",
    "Isabella", "James", "Karen", "Liam", "Mia", "Noah", "Olivia",
    "Patrick", "Quinn", "Rachel", "Samuel", "Tina",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
]

ORIGINS = ["status_a", "status_b", "status_c"]
EMAIL_DOMAINS = ["example.com", "demo.org", "test.net", "mail.io"]

DEFAULT_COUNT = 100_000
DEFAULT_BATCH_SIZE = 5_000


class Command(BaseCommand):
    help = "Bulk-seed a large Customer/Order dataset for list-view benchmarking."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=DEFAULT_COUNT,
            help=f"Number of customers and orders to create (default: {DEFAULT_COUNT}).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"bulk_create batch size (default: {DEFAULT_BATCH_SIZE}).",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing Orders and Customers before seeding.",
        )
        parser.add_argument(
            "--no-index",
            action="store_true",
            help="Skip Elasticsearch indexing (no-op here; kept for parity with seed_demo).",
        )

    def handle(self, *args, **options):
        count = options["count"]
        batch_size = options["batch_size"]
        flush = options["flush"]

        if count <= 0:
            raise CommandError("--count must be a positive integer.")
        if batch_size <= 0:
            raise CommandError("--batch-size must be a positive integer.")

        self.stdout.write(self.style.MIGRATE_HEADING("🌱  SnapAdmin Large-Dataset Seeder"))
        self.stdout.write(f"   Rows        : {count:,}")
        self.stdout.write(f"   Batch size  : {batch_size:,}")
        self.stdout.write(f"   Flush first : {flush}")
        self.stdout.write("")

        if flush:
            self._flush()

        self._seed_customers(count, batch_size)
        self._seed_orders(count, batch_size)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("✅  Large seed complete!"))
        self.stdout.write(f"   Customers : {Customer.objects.count():,}")
        self.stdout.write(f"   Orders    : {Order.objects.count():,}")
        self.stdout.write("")
        self.stdout.write("   Benchmark : python manage.py benchmark_list_view")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _flush(self):
        self.stdout.write("   Flushing existing Orders and Customers…")
        Order.objects.all().delete()
        Customer.objects.all().delete()
        self.stdout.write("   Done.")

    def _seed_customers(self, count: int, batch_size: int):
        self.stdout.write("   Creating customers…")
        created = 0
        while created < count:
            chunk = min(batch_size, count - created)
            batch = []
            for i in range(created, created + chunk):
                first = FIRST_NAMES[i % len(FIRST_NAMES)]
                last = LAST_NAMES[(i // len(FIRST_NAMES)) % len(LAST_NAMES)]
                email = f"{first.lower()}.{last.lower()}.{i}@{random.choice(EMAIL_DOMAINS)}"
                batch.append(Customer(
                    first_name=first,
                    last_name=last,
                    email=email,
                    origin=ORIGINS[i % len(ORIGINS)],
                    active=(i % 10) != 0,  # 90% active
                ))
            Customer.objects.bulk_create(batch, batch_size=batch_size)
            created += chunk
            self.stdout.write(f"      … {created:,}/{count:,} customers")

    def _seed_orders(self, count: int, batch_size: int):
        self.stdout.write("   Creating orders…")
        # Pull customer PKs once; orders point at them round-robin so every
        # Order row resolves a real FK (exercising list_select_related).
        customer_ids = list(Customer.objects.values_list("pk", flat=True))
        if not customer_ids:
            self.stdout.write(self.style.WARNING("   ⚠  No customers — skipping orders."))
            return

        created = 0
        while created < count:
            chunk = min(batch_size, count - created)
            batch = []
            for i in range(created, created + chunk):
                batch.append(Order(
                    customer_id=customer_ids[i % len(customer_ids)],
                    total=Decimal(str(round(random.uniform(19.99, 999.99), 2))),
                ))
            with transaction.atomic():
                Order.objects.bulk_create(batch, batch_size=batch_size)
            created += chunk
            self.stdout.write(f"      … {created:,}/{count:,} orders")
