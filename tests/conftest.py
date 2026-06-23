
import pytest
from decimal import Decimal

@pytest.fixture
def product(db):
    from demo.models import Product
    return Product.objects.create(name="Test Laptop Stand", price=Decimal("49.99"), available=True)

@pytest.fixture
def product_unavailable(db):
    from demo.models import Product
    return Product.objects.create(name="Out of Stock", price=Decimal("10.00"), available=False)

@pytest.fixture
def many_products(db):
    from demo.models import Product
    return [Product.objects.create(name=f"Product {i}", price=Decimal(i)) for i in range(30)]

@pytest.fixture
def customer(db):
    from demo.models import Customer
    return Customer.objects.create(first_name="Alice", last_name="Smith", email="alice@example.com", origin="status_a", active=True)

@pytest.fixture
def customer_inactive(db):
    from demo.models import Customer
    return Customer.objects.create(first_name="Bob", last_name="Jones", email="bob@example.com", origin="status_b", active=False)

@pytest.fixture
def order(db, customer):
    from demo.models import Order
    return Order.objects.create(customer=customer, total=Decimal("99.99"))

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
    return User.objects.create_user(username="regular", password="password")
