"""
tests/test_url_prefix.py

SNAPADMIN_URL_PREFIX relocates the entire snapadmin URL surface (REST, Swagger,
GraphQL) under one extra path segment without changing any route *name*, so a
project that already owns the mount point can avoid collisions.

The setting is read at import time, so each case reloads ``snapadmin.urls``
through the ``snapadmin_urls_under`` fixture and resolves against that module
directly. The fixture's docstring explains the import-time trap and owns the
restore; see ``tests/conftest.py``. (#FIX2 section C, closed in #QA1b — the
reload-and-restore dance used to be copied into a ``finally`` in every test.)
"""

import pytest
from django.urls import Resolver404, resolve, reverse


def test_default_has_no_prefix(snapadmin_urls_under):
    urls = snapadmin_urls_under()

    assert reverse("api-health", urlconf=urls) == "/health/"
    assert resolve("/health/", urlconf=urls).url_name == "api-health"


def test_prefix_relocates_all_surfaces(snapadmin_urls_under):
    urls = snapadmin_urls_under(SNAPADMIN_URL_PREFIX="internal/")

    # REST, Swagger and GraphQL all move under the prefix...
    assert reverse("api-health", urlconf=urls) == "/internal/health/"
    assert reverse("swagger-ui", urlconf=urls) == "/internal/docs/"
    assert reverse("graphql", urlconf=urls) == "/internal/graphql/"
    assert reverse(
        "model-list", args=["demo", "Product"], urlconf=urls
    ) == "/internal/models/demo/Product/"

    # ...and resolve at the new location, keeping their names.
    assert resolve("/internal/health/", urlconf=urls).url_name == "api-health"

    # The old, un-prefixed paths no longer resolve.
    with pytest.raises(Resolver404):
        resolve("/health/", urlconf=urls)


@pytest.mark.parametrize("raw", ["internal", "/internal/", "internal/", "/internal"])
def test_prefix_is_normalised(raw, snapadmin_urls_under):
    # Leading/trailing slashes are normalised to a single "<seg>/" segment.
    urls = snapadmin_urls_under(SNAPADMIN_URL_PREFIX=raw)

    assert reverse("api-health", urlconf=urls) == "/internal/health/"


def test_empty_prefix_is_a_noop(snapadmin_urls_under):
    urls = snapadmin_urls_under(SNAPADMIN_URL_PREFIX="")

    assert reverse("api-health", urlconf=urls) == "/health/"


def test_the_user_api_is_not_mounted_when_it_is_off(snapadmin_urls_under):
    """#QA1d — both user-API routes (the viewset and the permission picker) stay
    unmounted with ``SNAPADMIN_USER_API_ENABLED = False``, the rest untouched."""
    from django.urls import NoReverseMatch

    on = snapadmin_urls_under(SNAPADMIN_USER_API_ENABLED=True)
    assert reverse("permission-list", urlconf=on) == "/permissions/"
    assert reverse("api-user-list", urlconf=on) == "/users/"

    off = snapadmin_urls_under(SNAPADMIN_USER_API_ENABLED=False)
    for name in ("permission-list", "api-user-list"):
        with pytest.raises(NoReverseMatch):
            reverse(name, urlconf=off)
    assert reverse("api-health", urlconf=off) == "/health/"
