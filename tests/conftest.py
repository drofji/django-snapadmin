
import pytest
from decimal import Decimal

@pytest.fixture
def product(db):
    from demo.apps.shop.models import Product
    return Product.objects.create(name="Test Laptop Stand", price=Decimal("49.99"), available=True)

@pytest.fixture
def product_unavailable(db):
    from demo.apps.shop.models import Product
    return Product.objects.create(name="Out of Stock", price=Decimal("10.00"), available=False)

@pytest.fixture
def many_products(db):
    from demo.apps.shop.models import Product
    return [Product.objects.create(name=f"Product {i}", price=Decimal(i)) for i in range(30)]

@pytest.fixture
def customer(db):
    from demo.apps.shop.models import Customer
    return Customer.objects.create(first_name="Alice", last_name="Smith", email="alice@example.com", origin="status_a", active=True)

@pytest.fixture
def customer_inactive(db):
    from demo.apps.shop.models import Customer
    return Customer.objects.create(first_name="Bob", last_name="Jones", email="bob@example.com", origin="status_b", active=False)

#: The tenant most of the pre-existing (non-tenancy-focused) test suite's
#: fixture users resolve to under demo/core/tenancy.py's email-domain
#: fallback (admin_user's email is "admin@example.com" — see pytest-django's
#: own admin_user fixture). Order (#FUT1) is the demo's tenant-scoped model;
#: a row created outside any bound tenant context is invisible to every
#: tenant by design (snapadmin.tenancy's default-deny), so every fixture
#: that creates one for a test that is not itself testing tenant isolation
#: must bind this tenant first. Tests that exercise cross-tenant isolation
#: itself use a different, explicit tenant instead — see test_tenancy_*.py.
DEFAULT_TEST_TENANT = "example.com"


@pytest.fixture
def order(db, customer):
    from demo.apps.shop.models import Order
    # A direct ORM .create() bypasses every SnapAdmin write-time guard,
    # tenant stamping included (only perform_create/save_model/_process_row
    # stamp it) — the tenant must be passed explicitly here, the same way a
    # fixture would pass any other required field a request-time guard would
    # otherwise fill in.
    return Order.objects.create(customer=customer, total=Decimal("99.99"), tenant_id=DEFAULT_TEST_TENANT)

@pytest.fixture
def api_token(db, admin_user):
    from snapadmin.models import APIToken
    return APIToken.create_for_user(admin_user, "Test Token")

@pytest.fixture
def inactive_token(db, admin_user):
    from snapadmin.models import APIToken
    t = APIToken.create_for_user(admin_user, "Inactive")
    t.is_active = False
    t.save()
    return t

@pytest.fixture
def expired_token(db, admin_user):
    from snapadmin.models import APIToken
    from django.utils import timezone
    from datetime import timedelta
    t = APIToken.create_for_user(admin_user, "Expired")
    t.expiration_date = timezone.now() - timedelta(days=1)
    t.save()
    return t

@pytest.fixture
def restricted_token(db, admin_user):
    from snapadmin.models import APIToken
    return APIToken.create_for_user(admin_user, "Restricted", allowed_models=["demo.Product"])

@pytest.fixture
def auth_client(api_token):
    from rest_framework.test import APIClient
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {api_token.token_key}")
    return client

@pytest.fixture
def anon_client():
    from rest_framework.test import APIClient
    return APIClient()

@pytest.fixture
def regular_user(db):
    from django.contrib.auth.models import User
    # email domain matches DEFAULT_TEST_TENANT — demo/core/tenancy.py's
    # resolver falls back to it, so a request authenticated as this user
    # resolves the same tenant the `order` fixture's row carries (#FUT1).
    return User.objects.create_user(
        username="regular", password="password", email=f"regular@{DEFAULT_TEST_TENANT}"
    )
