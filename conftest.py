"""
conftest.py  –  Shared pytest fixtures for the SnapAdmin test suite.
"""

from decimal import Decimal
import pytest
from django.contrib.auth.models import User


# ── Users ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        username="testadmin", email="testadmin@example.com", password="testpassword123"
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="testuser", email="testuser@example.com", password="testpassword123"
    )


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="staffuser", email="staff@example.com", password="testpassword123", is_staff=True
    )


# ── Tokens ────────────────────────────────────────────────────────────────────

@pytest.fixture
def api_token(db, admin_user):
    from snapadmin.models import APIToken
    return APIToken.create_for_user(
        user=admin_user, token_name="Test Token", allowed_models=[], expires_in_days=None
    )


@pytest.fixture
def restricted_token(db, admin_user):
    from snapadmin.models import APIToken
    return APIToken.create_for_user(
        user=admin_user, token_name="Restricted Token", allowed_models=["demo.Product"]
    )


@pytest.fixture
def expired_token(db, admin_user):
    from datetime import timedelta
    from django.utils import timezone
    from snapadmin.models import APIToken
    return APIToken.objects.create(
        user=admin_user,
        token_name="Expired Token",
        expiration_date=timezone.now() - timedelta(hours=1),
    )


@pytest.fixture
def inactive_token(db, admin_user):
    from snapadmin.models import APIToken
    token = APIToken.create_for_user(user=admin_user, token_name="Inactive Token")
    token.is_active = False
    token.save()
    return token


# ── Clients ───────────────────────────────────────────────────────────────────

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


# ── Demo objects ──────────────────────────────────────────────────────────────

@pytest.fixture
def product(db):
    from demo.models import Product
    return Product.objects.create(name="Test Laptop Stand", price=Decimal("49.99"), available=True)


@pytest.fixture
def product_unavailable(db):
    from demo.models import Product
    return Product.objects.create(name="Out-of-Stock Item", price=Decimal("9.99"), available=False)


@pytest.fixture
def many_products(db):
    from demo.models import Product
    objs = [
        Product(name=f"Product {i}", price=Decimal(f"{i}.99"), available=(i % 2 == 0))
        for i in range(1, 31)
    ]
    return Product.objects.bulk_create(objs)


@pytest.fixture
def customer(db):
    from demo.models import Customer
    return Customer.objects.create(
        first_name="Alice", last_name="Smith",
        email="alice@example.com", origin="status_a", active=True
    )


@pytest.fixture
def customer_inactive(db):
    from demo.models import Customer
    return Customer.objects.create(
        first_name="Bob", last_name="Jones",
        email="bob@example.com", origin="status_b", active=False
    )


@pytest.fixture
def order(db, customer):
    from demo.models import Order
    return Order.objects.create(customer=customer, total=Decimal("99.99"))
