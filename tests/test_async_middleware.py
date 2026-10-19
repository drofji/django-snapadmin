"""
tests/test_async_middleware.py — SnapAdmin's middleware runs in both modes (#EXT3m).

``SnapErrorMonitorMiddleware`` and ``SnapTenantMiddleware`` were plain sync
callables. Under ASGI, Django then switches the whole middleware chain to sync
mode, a thread hop per request — it works, and costs time. The suite had **zero**
async tests: WSGI hides a sync-only middleware completely.

The class guard scans the package for every middleware (a class whose
``__init__`` takes ``get_response``) and requires both capabilities.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from asgiref.sync import async_to_sync, iscoroutinefunction
from django.http import HttpResponse, HttpResponseServerError
from django.test import RequestFactory

from snapadmin import tenancy
from snapadmin.middleware import SnapErrorMonitorMiddleware
from snapadmin.models import ErrorEvent
from snapadmin.tenancy import SnapTenantMiddleware

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "snapadmin"


class TestEveryMiddlewareIsAsyncCapable:
    def test_the_class_guard(self):
        middleware = []
        for path in sorted(PACKAGE.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ClassDef):
                    continue
                init = next(
                    (item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "__init__"),
                    None,
                )
                if init and [arg.arg for arg in init.args.args[1:2]] == ["get_response"]:
                    middleware.append((path.relative_to(PACKAGE).as_posix(), node.name))

        assert len(middleware) >= 2, "the scan found too few middleware classes — it is not looking"
        import importlib

        sync_only = []
        for relative, name in middleware:
            module = importlib.import_module("snapadmin." + relative[:-3].replace("/", "."))
            cls = getattr(module, name)
            if not (getattr(cls, "sync_capable", False) and getattr(cls, "async_capable", False)):
                sync_only.append(f"{relative}:{name}")

        assert not sync_only, (
            f"review: {sync_only} declare no async support — under ASGI Django would switch the "
            "whole middleware chain to sync mode for them"
        )


def _request():
    return RequestFactory().get("/boom/")


@pytest.mark.django_db
class TestTheErrorMonitorInAsyncMode:
    def test_it_becomes_a_coroutine_function_around_an_async_view(self):
        async def view(request):
            return HttpResponse("ok")

        assert iscoroutinefunction(SnapErrorMonitorMiddleware(view)) is True

    def test_it_stays_sync_around_a_sync_view(self):
        assert iscoroutinefunction(SnapErrorMonitorMiddleware(lambda request: HttpResponse("ok"))) is False

    def test_an_async_5xx_is_recorded(self):
        async def view(request):
            return HttpResponseServerError("down")

        response = async_to_sync(SnapErrorMonitorMiddleware(view))(_request())

        assert response.status_code == 500
        assert ErrorEvent.objects.count() == 1
        assert ErrorEvent.objects.get().status_code == 500

    def test_an_async_success_records_nothing(self):
        async def view(request):
            return HttpResponse("ok")

        async_to_sync(SnapErrorMonitorMiddleware(view))(_request())

        assert ErrorEvent.objects.count() == 0


@pytest.mark.django_db
class TestTheTenantMiddlewareInAsyncMode:
    def test_the_tenant_is_bound_inside_the_view_and_cleared_after(self, settings):
        settings.SNAPADMIN_TENANT_RESOLVER = lambda request: "acme"
        seen = {}

        async def view(request):
            seen["tenant"] = tenancy.get_current_tenant()
            return HttpResponse("ok")

        middleware = SnapTenantMiddleware(view)
        assert iscoroutinefunction(middleware) is True

        async_to_sync(middleware)(_request())

        assert seen == {"tenant": "acme"}
        assert tenancy.get_current_tenant() is None

    def test_the_binding_is_cleared_even_when_the_view_raises(self, settings):
        settings.SNAPADMIN_TENANT_RESOLVER = lambda request: "acme"

        async def view(request):
            raise RuntimeError("view failed")

        with pytest.raises(RuntimeError, match="view failed"):
            async_to_sync(SnapTenantMiddleware(view))(_request())

        assert tenancy.get_current_tenant() is None
