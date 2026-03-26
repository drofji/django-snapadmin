"""
demo/management/commands/seed_demo.py

Management command to populate the database with demo data.

Usage:
    python manage.py seed_demo              # Seed with default 50 objects each
    python manage.py seed_demo --count 20   # Custom count
    python manage.py seed_demo --flush      # Wipe existing demo data first
    python manage.py seed_demo --no-index   # Skip Elasticsearch indexing

This command is also invoked automatically after the first ``migrate`` run
when SNAPADMIN_AUTO_SEED=True is set in the environment.
"""

import random
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from demo.models import Customer, Order, Product, SearchLog


# ─── Sample data pools ───────────────────────────────────────────────────────

PRODUCT_NAMES = [
    "Premium Laptop Stand", "Ergonomic Mouse", "Mechanical Keyboard",
    "USB-C Hub (7-in-1)", "4K Webcam", "Noise-Cancelling Headphones",
    "Standing Desk Mat", "Monitor Light Bar", "Cable Management Kit",
    "Wireless Charger Pad", "Smart LED Desk Lamp", "Privacy Screen Filter",
    "Portable SSD 1TB", "Blue-Light Glasses", "Laptop Sleeve (15-in)",
    "HDMI 2.1 Cable 2m", "USB Type-C Dock", "Wrist Rest Pad",
    "Laptop Cooling Pad", "Mini Bluetooth Speaker",
]

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


class Command(BaseCommand):
    """
    Populate the database with realistic demo data covering all SnapAdmin features.

    Seeded objects:
      • Products   — various prices, availability flags, for ES indexing
      • Customers  — different origins (badge demo) and active states
      • Orders     — linked to random customers, for FK/filter demo

    Also creates:
      • A default superuser (admin / admin) if none exists
      • An API demo token for immediate API testing
    """

    help = "Seed the database with demo data for SnapAdmin showcase."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=50,
            help="Number of each object type to create (default: 50).",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all existing demo data before seeding.",
        )
        parser.add_argument(
            "--no-index",
            action="store_true",
            help="Skip Elasticsearch indexing step.",
        )

    def handle(self, *args, **options):
        count    = options["count"]
        flush    = options["flush"]
        no_index = options["no_index"]

        self.stdout.write(self.style.MIGRATE_HEADING("🌱  SnapAdmin Demo Seeder"))
        self.stdout.write(f"   Objects per type : {count}")
        self.stdout.write(f"   Flush first      : {flush}")
        self.stdout.write(f"   ES indexing      : {'disabled' if no_index else 'enabled'}")
        self.stdout.write("")

        with transaction.atomic():
            if flush:
                self._flush()

            products  = self._seed_products(count)
            customers = self._seed_customers(count)
            search_logs = self._seed_search_logs(count)
            orders    = self._seed_orders(customers, products, count)
            admin     = self._ensure_superuser()
            token     = self._ensure_api_token(admin)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("✅  Seeding complete!"))
        self.stdout.write(f"   Products  : {len(products)}")
        self.stdout.write(f"   Customers : {len(customers)}")
        self.stdout.write(f"   Orders    : {len(orders)}")
        self.stdout.write(f"   SearchLogs: {len(search_logs)}")
        self.stdout.write("")
        self.stdout.write(f"   Admin URL : http://localhost:8000/admin/")
        self.stdout.write(f"   Username  : admin")
        self.stdout.write(f"   Password  : admin")
        self.stdout.write("")
        self.stdout.write(f"   API Token : {token.token_key}")
        self.stdout.write(f"   Swagger   : http://localhost:8000/api/docs/")
        self.stdout.write("")

        if not no_index:
            self._index_to_elasticsearch(products)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _flush(self):
        """Delete all demo data."""
        self.stdout.write("   Flushing existing demo data…")
        Order.objects.all().delete()
        Customer.objects.all().delete()
        Product.objects.all().delete()
        try:
            SearchLog.objects.all().delete()
        except Exception:
            # Table might not exist in DB for ES_ONLY
            pass
        self.stdout.write("   Done.")

    def _seed_products(self, count: int) -> list:
        """Create Product records with varied names, prices, and availability."""
        self.stdout.write("   Creating products…")
        products = []
        for i in range(count):
            name      = PRODUCT_NAMES[i % len(PRODUCT_NAMES)]
            if i >= len(PRODUCT_NAMES):
                name = f"{name} (v{i // len(PRODUCT_NAMES) + 1})"
            price     = Decimal(str(round(random.uniform(9.99, 499.99), 2)))
            available = random.random() > 0.2  # 80% available

            product = Product(name=name, price=price, available=available)
            products.append(product)

        return Product.objects.bulk_create(products, ignore_conflicts=True)

    def _seed_customers(self, count: int) -> list:
        """Create Customer records with varied origins and active states."""
        self.stdout.write("   Creating customers…")
        customers = []
        for i in range(count):
            first  = FIRST_NAMES[i % len(FIRST_NAMES)]
            last   = LAST_NAMES[i % len(LAST_NAMES)]
            suffix = f"{i // len(FIRST_NAMES) + 1}" if i >= len(FIRST_NAMES) else ""
            email  = f"{first.lower()}.{last.lower()}{suffix}@{random.choice(EMAIL_DOMAINS)}"
            origin = ORIGINS[i % len(ORIGINS)]
            active = random.random() > 0.1  # 90% active

            customers.append(Customer(
                first_name=first,
                last_name=last,
                email=email,
                origin=origin,
                active=active,
            ))

        return Customer.objects.bulk_create(customers, ignore_conflicts=True)

    def _seed_search_logs(self, count: int) -> list:
        """Create SearchLog records directly in Elasticsearch."""
        self.stdout.write("   Creating search logs (ES only)…")
        logs = []
        queries = ["laptop", "mouse", "keyboard", "monitor", "cable", "hub"]
        for i in range(count):
            query = random.choice(queries)
            count_res = random.randint(0, 100)
            log = SearchLog(query=query, results_count=count_res)
            log.save()  # This will save only to ES due to ES_ONLY mode
            logs.append(log)
        return logs

    def _seed_orders(self, customers: list, products: list, count: int) -> list:
        """Create Order records linking customers to random totals."""
        self.stdout.write("   Creating orders…")
        if not customers or not products:
            self.stdout.write(self.style.WARNING("   ⚠  No customers/products — skipping orders."))
            return []

        # Re-query to get PKs after bulk_create
        db_customers = list(Customer.objects.all()[:count])
        orders = []
        for i in range(count):
            customer = db_customers[i % len(db_customers)]
            total    = Decimal(str(round(random.uniform(19.99, 999.99), 2)))
            orders.append(Order(customer=customer, total=total))

        return Order.objects.bulk_create(orders)

    def _ensure_superuser(self) -> User:
        """Create a default superuser if none exists."""
        if not User.objects.filter(is_superuser=True).exists():
            self.stdout.write("   Creating default admin user…")
            user = User.objects.create_superuser(
                username="admin",
                email="admin@example.com",
                password="admin",
            )
            self.stdout.write(self.style.WARNING("   ⚠  Default admin/admin created — change in production!"))
            return user
        return User.objects.filter(is_superuser=True).first()

    def _ensure_api_token(self, user: User):
        """Create a demo API token for the admin user if it doesn't exist."""
        from snapadmin.models import APIToken
        token, created = APIToken.objects.get_or_create(
            user=user,
            token_name="Demo Token",
            defaults={"allowed_models": []},
        )
        if created:
            self.stdout.write("   Demo API token created.")
        return token

    def _index_to_elasticsearch(self, products: list):
        """
        Attempt to index products to Elasticsearch.
        Silently skips when ES is unavailable.
        """
        from django.conf import settings
        if not getattr(settings, 'ELASTICSEARCH_ENABLED', False):
            self.stdout.write(self.style.WARNING("   ⚠  Elasticsearch not available — skipping index."))
            return

        self.stdout.write("   Indexing products to Elasticsearch…")
        Product.es_reindex_all()
        self.stdout.write(f"   Indexed {Product.objects.count()} products.")
