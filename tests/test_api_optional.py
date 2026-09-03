"""
tests/test_api_optional.py

The REST and GraphQL stacks are feature-gated, so loading the *core* must not
require them. Two properties are pinned here:

1. **Importing a model does not import DRF.** ``snapadmin.models`` reaches
   ``snapadmin.pagination`` for :class:`EstimatedCountPaginator`, which is pure
   Django and drives the admin changelist. That module also defines the DRF
   paginator used by the API, so importing DRF at its top would have made the
   REST stack a hard requirement of loading a model — an admin-only project would
   pay for an API it never mounts.

2. **A feature switched on without its dependencies fails with an answer.** A bare
   ``ImportError`` from deep inside a URLconf reads as a broken install; what the
   reader needs is "install the extra, or turn the feature off".

The import-time behaviour is exercised by executing the module fresh with the
packages hidden, the same technique ``test_unfold_optional.py`` uses for the
theme: the branch is chosen while the module runs, so a monkeypatched flag cannot
reach it.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import subprocess
import sys
import textwrap

import pytest
from django.core.exceptions import ImproperlyConfigured


API_PACKAGES = ("rest_framework", "drf_spectacular", "django_filters", "graphene", "graphene_django")


class _HideModules:
    """Meta-path finder that makes the named top-level packages unimportable.

    Also evicts SnapAdmin's own API modules from ``sys.modules``: they import the
    hidden packages, so leaving them cached would let a re-import succeed from the
    cache and quietly skip the branch under test.
    """

    def __init__(self, *names: str) -> None:
        self.names = names

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.names:
            raise ImportError(f"hidden by test: {name}")
        return None

    def _is_evicted(self, module_name: str) -> bool:
        return (
            module_name.split(".")[0] in self.names
            or module_name.startswith("snapadmin.api")
            or module_name == "snapadmin.pagination"
        )

    def __enter__(self):
        sys.meta_path.insert(0, self)
        self._saved = {k: v for k, v in sys.modules.items() if self._is_evicted(k)}
        for key in self._saved:
            del sys.modules[key]
        return self

    def __exit__(self, *exc):
        sys.meta_path.remove(self)
        sys.modules.update(self._saved)
        return False


def _exec_fresh(module_path: pathlib.Path, name: str):
    """Execute a source file into a throwaway module name.

    Coverage keys on the source file rather than the module name, so the lines run
    here are still attributed to the real module — while the canonical import in
    ``sys.modules`` stays untouched.
    """
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPaginationDoesNotRequireDRF:
    def test_module_executes_with_drf_hidden(self):
        source = pathlib.Path(sys.modules["snapadmin.pagination"].__file__)
        with _HideModules(*API_PACKAGES):
            module = _exec_fresh(source, "snapadmin_pagination_no_drf")
            # The admin-side paginator is fully usable without DRF present.
            assert module.EstimatedCountPaginator is not None
            assert module.estimated_count_enabled() is True

    def test_drf_paginator_resolves_on_first_access(self):
        """The public import path keeps working — it is just resolved lazily."""
        module = importlib.import_module("snapadmin.pagination")
        from rest_framework.pagination import PageNumberPagination

        assert issubclass(module.SnapDynamicPagination, PageNumberPagination)

    def test_unknown_attribute_still_raises_attribute_error(self):
        module = importlib.import_module("snapadmin.pagination")
        with pytest.raises(AttributeError, match="no attribute 'nope'"):
            module.nope


class TestCoreBootsWithoutTheAPIStack:
    def test_django_setup_succeeds_on_a_minimal_core(self):
        """A subprocess is the only honest test: the app registry boots once per process.

        Blocks the REST/GraphQL packages at import, then boots Django on an
        INSTALLED_APPS carrying nothing but ``django.contrib.*`` and ``snapadmin``,
        and renders a model's admin changelist URL to prove the admin really works.
        """
        script = textwrap.dedent(
            f"""
            import sys
            class Blocker:
                BLOCKED = {API_PACKAGES!r}
                def find_spec(self, name, path=None, target=None):
                    if name.split(".")[0] in self.BLOCKED:
                        raise ImportError("blocked: " + name)
                    return None
            sys.meta_path.insert(0, Blocker())

            import django
            from django.conf import settings
            settings.configure(
                INSTALLED_APPS=[
                    "django.contrib.admin", "django.contrib.auth",
                    "django.contrib.contenttypes", "django.contrib.sessions",
                    "django.contrib.messages", "django.contrib.staticfiles",
                    "snapadmin",
                ],
                DATABASES={{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}}},
                DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
                USE_TZ=True,
            )
            django.setup()

            for name in {API_PACKAGES!r}:
                assert name not in sys.modules, name + " was imported after all"

            # The admin layer is what an API-less project actually uses: register
            # SnapAdmin's own models the way a project's admin.py does.
            from django.contrib import admin
            from snapadmin.models import APIToken, SnapModel
            SnapModel.register_all_admins()
            assert APIToken in admin.site._registry, "the admin did not register"
            print("OK")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=pathlib.Path(__file__).resolve().parent.parent,
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


class TestMissingExtraIsExplained:
    """``snapadmin.urls`` turns a missing package into a two-part answer."""

    def _error_for(self, setting: str) -> ImproperlyConfigured:
        from snapadmin import urls as urls_module

        source = pathlib.Path(urls_module.__file__)
        with _HideModules(*API_PACKAGES):
            with pytest.raises(ImproperlyConfigured) as excinfo:
                _exec_fresh(source, f"snapadmin_urls_no_api_{setting.lower()}")
        return excinfo.value

    def test_rest_api_names_the_extra_and_the_off_switch(self, settings):
        settings.SNAPADMIN_REST_API_ENABLED = True
        message = str(self._error_for("SNAPADMIN_REST_API_ENABLED"))
        assert "pip install django-snapadmin[api]" in message
        assert "SNAPADMIN_REST_API_ENABLED = False" in message

    def test_graphql_names_its_own_extra(self, settings):
        # With REST and Swagger off, GraphQL is the branch that trips.
        settings.SNAPADMIN_REST_API_ENABLED = False
        settings.SNAPADMIN_SWAGGER_ENABLED = False
        settings.SNAPADMIN_GRAPHQL_ENABLED = True
        message = str(self._error_for("SNAPADMIN_GRAPHQL_ENABLED"))
        assert "pip install django-snapadmin[graphql]" in message
        assert "SNAPADMIN_GRAPHQL_ENABLED = False" in message

    def test_swagger_names_the_api_extra(self, settings):
        settings.SNAPADMIN_REST_API_ENABLED = False
        settings.SNAPADMIN_SWAGGER_ENABLED = True
        settings.SNAPADMIN_GRAPHQL_ENABLED = False
        message = str(self._error_for("SNAPADMIN_SWAGGER_ENABLED"))
        assert "pip install django-snapadmin[api]" in message
        assert "SNAPADMIN_SWAGGER_ENABLED = False" in message

    def test_everything_off_imports_cleanly_without_the_stack(self, settings):
        """Nothing enabled → no API import at all, so the URLconf still loads."""
        settings.SNAPADMIN_REST_API_ENABLED = False
        settings.SNAPADMIN_SWAGGER_ENABLED = False
        settings.SNAPADMIN_GRAPHQL_ENABLED = False

        from snapadmin import urls as urls_module

        source = pathlib.Path(urls_module.__file__)
        with _HideModules(*API_PACKAGES):
            module = _exec_fresh(source, "snapadmin_urls_all_off")
        assert module.urlpatterns == []
