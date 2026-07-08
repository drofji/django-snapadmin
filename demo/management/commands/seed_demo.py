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
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from demo.models import AuditLog, Category, Customer, Order, Product, SearchLog, Tag


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

CATEGORIES = [
    ("Electronics", "electronics"),
    ("Accessories", "accessories"),
    ("Audio", "audio"),
    ("Storage", "storage"),
    ("Ergonomics", "ergonomics"),
]

TAGS = ["sale", "new", "featured", "bundle", "limited", "bestseller", "eco", "wireless", "usb-c", "pro"]

AUDIT_ACTIONS = [
    "user.login", "user.logout", "product.created", "product.updated",
    "order.placed", "order.cancelled", "customer.registered",
    "api_token.created", "api_token.revoked", "export.csv",
]


class Command(BaseCommand):
    """
    Populate the database with realistic demo data covering all SnapAdmin features.

    Seeded objects:
      • Categories  — 5 product categories
      • Tags        — 10 tags for filtering/grouping
      • Products    — linked to categories and tags, varied prices and availability
      • Customers   — different origins (badge demo) and active states
      • Orders      — linked to random customers, for FK/filter demo
      • AuditLogs   — action history with timestamps for GDPR retention demo

    Also creates:
      • A default superuser (admin / admin) if none exists
      • An API demo token for immediate API testing
    """

    help = "Seed the database with demo data for SnapAdmin showcase."

    def _write(self, msg):
        """Write ``msg`` to stdout, dropping characters the console can't encode.

        The status lines use decorative emoji (🌱 / ✅ / ⚠). Windows consoles
        commonly run cp1252, which cannot encode them, so an unguarded write
        aborts the whole command with ``UnicodeEncodeError``. Sanitising against
        the stream's own encoding keeps the emoji on UTF-8 terminals while
        degrading gracefully (to ``?``) elsewhere. ``StringIO`` (used in tests)
        has no ``encoding``, so it falls back to UTF-8 and prints them verbatim.
        """
        encoding = getattr(self.stdout, "encoding", None) or "utf-8"
        safe = msg.encode(encoding, errors="replace").decode(encoding)
        self.stdout.write(safe)

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

        self._write(self.style.MIGRATE_HEADING("🌱  SnapAdmin Demo Seeder"))
        self.stdout.write(f"   Objects per type : {count}")
        self.stdout.write(f"   Flush first      : {flush}")
        self.stdout.write(f"   ES indexing      : {'disabled' if no_index else 'enabled'}")
        self.stdout.write("")

        with transaction.atomic():
            if flush:
                self._flush()

            categories  = self._seed_categories()
            tags        = self._seed_tags()
            products    = self._seed_products(count, categories, tags)
            customers   = self._seed_customers(count)
            search_logs = self._seed_search_logs(count)
            orders      = self._seed_orders(customers, products, count)
            audit_logs  = self._seed_audit_logs(count)
            admin       = self._ensure_superuser()
            token       = self._ensure_api_token(admin) if admin else None

        self.stdout.write("")
        self._write(self.style.SUCCESS("✅  Seeding complete!"))
        self.stdout.write(f"   Categories : {len(categories)}")
        self.stdout.write(f"   Tags       : {len(tags)}")
        self.stdout.write(f"   Products   : {len(products)}")
        self.stdout.write(f"   Customers  : {len(customers)}")
        self.stdout.write(f"   Orders     : {len(orders)}")
        self.stdout.write(f"   SearchLogs : {len(search_logs)}")
        self.stdout.write(f"   AuditLogs  : {len(audit_logs)}")
        self.stdout.write("")
        self.stdout.write(f"   Admin URL : http://localhost:8000/admin/")
        self.stdout.write(f"   Username  : admin")
        self.stdout.write(f"   Password  : $SNAPADMIN_SEED_ADMIN_PASSWORD ('admin' if unset, DEBUG only)")
        self.stdout.write("")
        # token_key holds the raw key only on the run that creates it; on a re-run
        # the token already exists and only its (non-secret) prefix is available.
        if token is not None:
            token_display = token.token_key or f"{token.token_prefix}•••••••• (existing — reset the DB to mint a new one)"
            self.stdout.write(f"   API Token : {token_display}")
        self.stdout.write(f"   Swagger   : http://localhost:8000/api/docs/")
        self.stdout.write("")

        if not no_index:
            self._index_to_elasticsearch(products)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _flush(self):
        """Delete all demo data."""
        self.stdout.write("   Flushing existing demo data…")
        Order.objects.all().delete()
        AuditLog.objects.all().delete()
        Customer.objects.all().delete()
        Product.objects.all().delete()
        Category.objects.all().delete()
        Tag.objects.all().delete()
        try:
            SearchLog.objects.all().delete()
        except Exception:
            pass
        self.stdout.write("   Done.")

    def _seed_categories(self) -> list:
        """Create Category records if they don't already exist."""
        self.stdout.write("   Creating categories…")
        created = []
        for name, slug in CATEGORIES:
            cat, is_new = Category.objects.get_or_create(slug=slug, defaults={"name": name, "is_active": True})
            created.append(cat)
        return created

    def _seed_tags(self) -> list:
        """Create Tag records if they don't already exist."""
        self.stdout.write("   Creating tags…")
        created = []
        for name in TAGS:
            tag, _ = Tag.objects.get_or_create(name=name)
            created.append(tag)
        return created

    def _seed_products(self, count: int, categories: list, tags: list) -> list:
        """Create Product records linked to random categories and tags."""
        self.stdout.write("   Creating products…")
        products = []
        for i in range(count):
            name      = PRODUCT_NAMES[i % len(PRODUCT_NAMES)]
            if i >= len(PRODUCT_NAMES):
                name = f"{name} (v{i // len(PRODUCT_NAMES) + 1})"
            price     = Decimal(str(round(random.uniform(9.99, 499.99), 2)))
            available = random.random() > 0.2  # 80% available
            category  = random.choice(categories) if categories else None

            product = Product(name=name, price=price, available=available, category=category)
            products.append(product)

        created = Product.objects.bulk_create(products, ignore_conflicts=True)

        # Assign random tags after bulk_create (M2M requires PKs)
        if tags:
            db_products = list(Product.objects.order_by("-pk")[:count])
            for product in db_products:
                product.tags.set(random.sample(tags, k=random.randint(1, 3)))

        return created

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
            log.save()
            logs.append(log)
        return logs

    def _seed_orders(self, customers: list, products: list, count: int) -> list:
        """Create Order records linking customers to random totals."""
        self.stdout.write("   Creating orders…")
        if not customers or not products:
            self._write(self.style.WARNING("   ⚠  No customers/products — skipping orders."))
            return []

        db_customers = list(Customer.objects.all()[:count])
        orders = []
        for i in range(count):
            customer = db_customers[i % len(db_customers)]
            total    = Decimal(str(round(random.uniform(19.99, 999.99), 2)))
            orders.append(Order(customer=customer, total=total))

        return Order.objects.bulk_create(orders)

    def _seed_audit_logs(self, count: int) -> list:
        """Create AuditLog records spread over the last 180 days (shows GDPR retention boundary)."""
        self.stdout.write("   Creating audit logs…")
        logs = []
        now = timezone.now()
        emails = [f"user{i}@example.com" for i in range(10)]
        for i in range(count):
            # spread timestamps over 180 days so some fall within and some outside the 90-day retention window
            days_ago = random.randint(0, 180)
            logs.append(AuditLog(
                action=random.choice(AUDIT_ACTIONS),
                user_email=random.choice(emails),
            ))
        created = AuditLog.objects.bulk_create(logs)
        # Backdate created_at using direct update (auto_now_add bypasses normal assignment)
        for i, obj in enumerate(AuditLog.objects.order_by("-pk")[:count]):
            days_ago = random.randint(0, 180)
            AuditLog.objects.filter(pk=obj.pk).update(created_at=now - timedelta(days=days_ago))
        return created

    def _ensure_superuser(self) -> User | None:
        """Create a default superuser if none exists.

        The password comes from ``SNAPADMIN_SEED_ADMIN_PASSWORD``; the insecure
        ``admin/admin`` default is allowed only while ``DEBUG=True`` — with
        ``DEBUG=False`` the command refuses to mint a guessable superuser.
        """
        if not User.objects.filter(is_superuser=True).exists():
            import os
            from django.conf import settings

            password = os.getenv("SNAPADMIN_SEED_ADMIN_PASSWORD", "")
            if not password:
                if not settings.DEBUG:
                    self._write(self.style.ERROR(
                        "   ✖ Refusing to create a default admin with DEBUG=False — "
                        "set SNAPADMIN_SEED_ADMIN_PASSWORD and re-run."
                    ))
                    return None
                password = "admin"
                self._write(self.style.WARNING(
                    "   ⚠  Default admin/admin created (DEBUG only) — set "
                    "SNAPADMIN_SEED_ADMIN_PASSWORD for a real password!"
                ))
            self.stdout.write("   Creating default admin user…")
            return User.objects.create_superuser(
                username="admin",
                email="admin@example.com",
                password=password,
            )
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
        """Attempt to index products to Elasticsearch. Silently skips when ES is unavailable."""
        from demo.search import index_product, is_es_available
        if not is_es_available():
            self._write(self.style.WARNING("   ⚠  Elasticsearch not available — skipping index."))
            return

        self.stdout.write("   Indexing products to Elasticsearch…")
        db_products = Product.objects.all()
        for product in db_products:
            index_product(product)
        self.stdout.write(f"   Indexed {db_products.count()} products.")
