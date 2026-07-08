"""
tests/test_url_prefix.py

SNAPADMIN_URL_PREFIX relocates the entire snapadmin URL surface (REST, Swagger,
GraphQL) under one extra path segment without changing any route *name*, so a
project that already owns the mount point can avoid collisions.

The setting is read at import time, so each case reloads ``snapadmin.urls`` under
an ``override_settings`` block and resolves/reverses against that module directly
(``urlconf=snap_urls``) to avoid touching the cached root resolver. A ``finally``
reload restores the default layout for the rest of the suite.
"""

import importlib

import pytest
from django.test import override_settings
from django.urls import clear_url_caches, resolve, reverse, Resolver404

import snapadmin.urls as snap_urls


def _reload(**settings_overrides):
    """Reload snapadmin.urls under the given settings and drop cached resolvers.

    ``get_resolver`` memoises per urlconf object; the module identity is stable
    across reloads, so the cache must be cleared for the new patterns to take.
    """
    with override_settings(**settings_overrides):
        importlib.reload(snap_urls)
    clear_url_caches()


def _reload_default():
    """Restore the un-prefixed urlconf so later tests see the historical layout."""
    importlib.reload(snap_urls)
    clear_url_caches()


def test_default_has_no_prefix():
    _reload_default()
    try:
        assert reverse("api-health", urlconf=snap_urls) == "/health/"
        assert resolve("/health/", urlconf=snap_urls).url_name == "api-health"
    finally:
        _reload_default()


def test_prefix_relocates_all_surfaces():
    _reload(SNAPADMIN_URL_PREFIX="internal/")
    try:
        # REST, Swagger and GraphQL all move under the prefix...
        assert reverse("api-health", urlconf=snap_urls) == "/internal/health/"
        assert reverse("swagger-ui", urlconf=snap_urls) == "/internal/docs/"
        assert reverse("graphql", urlconf=snap_urls) == "/internal/graphql/"
        assert reverse(
            "model-list", args=["demo", "Product"], urlconf=snap_urls
        ) == "/internal/models/demo/Product/"

        # ...and resolve at the new location, keeping their names.
        assert resolve("/internal/health/", urlconf=snap_urls).url_name == "api-health"

        # The old, un-prefixed paths no longer resolve.
        with pytest.raises(Resolver404):
            resolve("/health/", urlconf=snap_urls)
    finally:
        _reload_default()


@pytest.mark.parametrize("raw", ["internal", "/internal/", "internal/", "/internal"])
def test_prefix_is_normalised(raw):
    # Leading/trailing slashes are normalised to a single "<seg>/" segment.
    _reload(SNAPADMIN_URL_PREFIX=raw)
    try:
        assert reverse("api-health", urlconf=snap_urls) == "/internal/health/"
    finally:
        _reload_default()


def test_empty_prefix_is_a_noop():
    _reload(SNAPADMIN_URL_PREFIX="")
    try:
        assert reverse("api-health", urlconf=snap_urls) == "/health/"
    finally:
        _reload_default()
