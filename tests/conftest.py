
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


@pytest.fixture(scope="session", autouse=True)
def assert_no_model_leaked_into_the_app_registry():
    """Fail the session if a test left a model class in Django's app registry.

    A model class declared in a test body is registered by ``ModelBase.__new__``
    the moment the ``class`` statement executes, and it stays registered for the
    rest of the process — ``apps.all_models[label]`` is global state that nothing
    unwinds. Two surfaces then see a model the demo never declared: SnapAdmin's
    own registry (``SnapModel.__init_subclass__`` registers every subclass) and
    anything iterating ``apps.get_models()``, such as the dashboard's model cards
    and the demo landing page's stats.

    That is invisible under the suite's alphabetical order and appears the moment
    the order changes, which is exactly the order-dependence the quality standard
    forbids (§14). The cure is ``django.test.utils.isolate_apps``, which both the
    app registry and SnapAdmin's ``WeakKeyDictionary`` registry are built to
    cooperate with — see ``snapadmin/registry.py``'s module docstring.

    Session-scoped and autouse so it holds for a single file, a class or the whole
    suite, and so a leak is reported against the run that caused it rather than
    against whichever unrelated test happened to observe the consequence.
    """
    from django.apps import apps

    declared_before = {label: set(models) for label, models in apps.all_models.items()}

    yield

    leaked = sorted(
        f"{label}.{name}"
        for label, models in apps.all_models.items()
        for name in set(models) - declared_before.get(label, set())
    )
    assert leaked == [], (
        "these model classes were declared by a test and left in Django's global "
        f"app registry: {leaked}. Declare throwaway models inside "
        '`with isolate_apps("<app_label>"):` so they are discarded with the block.'
    )


@pytest.fixture(scope="session", autouse=True)
def assert_admin_registrations_are_restored():
    """Fail the session if a test left the admin registry holding a different admin.

    Several tests unregister a model, re-register it under an override to prove a
    setting reaches the generated ``ModelAdmin``, and restore it in a ``finally``.
    The restore only restores anything if it runs **outside** the override: a
    ``register_admin()`` called while the override is still live rebuilds the
    admin from the overridden settings and leaves *that* in the registry for the
    rest of the session. The media lists are where it shows — a later test reads
    ``admin.site._registry[Model].Media.js`` and sees an asset list built for a
    configuration nobody asked for.

    Compares by content rather than by identity, since a correct restore
    legitimately produces a new, equal ``ModelAdmin`` instance.
    """
    from django.contrib import admin

    def media_snapshot():
        return {
            f"{model._meta.app_label}.{model._meta.model_name}": (
                tuple(getattr(model_admin.Media, "js", ())),
                tuple(getattr(model_admin.Media, "css", {}).get("all", ())),
            )
            for model, model_admin in admin.site._registry.items()
            if hasattr(model_admin, "Media")
        }

    registered_before = media_snapshot()

    yield

    registered_after = media_snapshot()
    changed = sorted(
        name
        for name in set(registered_before) & set(registered_after)
        if registered_before[name] != registered_after[name]
    )
    added = sorted(set(registered_after) - set(registered_before))
    removed = sorted(set(registered_before) - set(registered_after))
    assert (changed, added, removed) == ([], [], []), (
        f"the admin registry did not come back as it started: media changed for "
        f"{changed}, models added {added}, models removed {removed}. Restore a "
        "registration outside the override_settings block that changed it, not "
        "inside it."
    )


@pytest.fixture(autouse=True)
def restore_the_active_language():
    """Put the thread's active translation back after every test.

    ``LocaleMiddleware`` activates a language per request and never deactivates
    it, so a single ``client.get("/", HTTP_ACCEPT_LANGUAGE="ru")`` leaves "ru"
    active for everything that follows. Lazy translations then resolve
    differently — ``Category.is_active``'s ``verbose_name`` becomes "Активен" —
    and any later test that matches on an English label fails for a reason that
    has nothing to do with it (found by a shuffled run, #QA1b).

    Restoring is the cure rather than a guard: the leak comes from production
    middleware doing the right thing for a request, not from a test doing the
    wrong thing, so every test that issues a localised request would otherwise
    have to remember to unwind it.
    """
    from django.utils import translation

    language_before = translation.get_language()
    try:
        yield
    finally:
        translation.activate(language_before)


@pytest.fixture(autouse=True)
def mask_nothing_unless_a_test_says_otherwise(settings):
    """Start every test from "no PII masking configured".

    ``demo/core/settings.py`` dogfoods masking, so without this a package test
    inherits half its configuration from the demo. Tests named ``test_unset`` or
    ``test_unconfigured_model_untouched`` then assert unconfigured behaviour
    without ever unsetting anything, and a test that pins only
    ``SNAPADMIN_MASKED_FIELDS`` still reads the demo's
    ``SNAPADMIN_MASKING_RULES`` — which declare their own fields sensitive, so
    half a pin is not a pin. Changing which model the demo masks then turns
    tests red that have nothing to do with the change (#FIX1k: 26 of them, in
    five files).

    Pinning both here rather than at all 79 override sites gives the same
    end state — every masking test states its whole configuration — in one
    place, and it holds for tests written later too. A test that wants masking
    overrides these; an ``override_settings`` decorator or context manager runs
    after this fixture and unwinds before it, so its value wins while it is
    live.
    """
    settings.SNAPADMIN_MASKED_FIELDS = {}
    settings.SNAPADMIN_MASKING_RULES = {}


@pytest.fixture
def snapadmin_urls_under():
    """Reload ``snapadmin.urls`` under given settings, and always put it back.

    **The trap this exists to remove.** ``snapadmin/urls.py`` decides what is
    mounted in *import-time module constants*, and Django imports the root
    URLconf lazily — so whichever test first triggers a ``reverse()`` fixes
    those constants for every test that follows it. A test that renders a page
    with the API switches unset therefore imports ``snapadmin.urls`` with
    nothing mounted, and a later, entirely unrelated test asserting that Swagger
    reverses fails with ``NoReverseMatch``. It has cost two debugging sessions
    already (#FIX1a), because the failure never appears in the test that caused
    it.

    Two things are needed, and both are easy to forget: ``clear_url_caches()``
    after the reload (``get_resolver`` memoises per urlconf *object*, and the
    module identity survives a reload, so the cache would keep serving the old
    patterns), and a reload back to the default layout **even when the test
    fails** — which is why this is a fixture rather than a helper a test has to
    remember to call in a ``finally``.

    Resolve and reverse against the returned module (``urlconf=urls``) rather
    than the global root, so a leaked cached resolver cannot make the result
    depend on test order::

        def test_prefix(snapadmin_urls_under):
            urls = snapadmin_urls_under(SNAPADMIN_URL_PREFIX="internal/")
            assert reverse("api-health", urlconf=urls) == "/internal/health/"
    """
    import importlib

    from django.test import override_settings
    from django.urls import clear_url_caches

    import snapadmin.urls as snapadmin_urls

    def reload_under(**settings_overrides):
        with override_settings(**settings_overrides):
            importlib.reload(snapadmin_urls)
        clear_url_caches()
        return snapadmin_urls

    try:
        yield reload_under
    finally:
        importlib.reload(snapadmin_urls)
        clear_url_caches()
