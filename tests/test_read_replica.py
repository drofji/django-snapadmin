"""
tests/test_read_replica.py — read-replica routing (issue #15)

SNAPADMIN_ANALYTICS_DB_ALIAS pins auto-generated read-only list/retrieve
querysets to a replica via ``.using()``. Writes (and the get_object() lookups
behind update/destroy) always stay on ``default`` so replication lag can never
stale or drop a mutation. Unset / unknown alias → no routing.
"""

import pytest
from django.test import override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from snapadmin.db import analytics_db_alias, route_read
from snapadmin.api.views import DynamicModelViewSet


# ── The resolver: analytics_db_alias() ───────────────────────────────────────

class TestAnalyticsDbAlias:
    def test_unset_defaults_to_default(self):
        assert analytics_db_alias() == "default"

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="")
    def test_empty_defaults_to_default(self):
        assert analytics_db_alias() == "default"

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="replica")
    def test_configured_alias_used(self):
        assert analytics_db_alias() == "replica"

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="does_not_exist")
    def test_unknown_alias_falls_back_to_default(self):
        # Guards against a typo silently routing to a missing connection.
        assert analytics_db_alias() == "default"


# ── The helper: route_read() ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestRouteRead:
    def test_noop_when_default(self):
        from demo.app.models import Product
        qs = Product.objects.all()
        assert route_read(qs).db == "default"

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="replica")
    def test_pins_to_replica(self):
        from demo.app.models import Product
        qs = Product.objects.all()
        assert route_read(qs).db == "replica"


# ── DynamicModelViewSet integration ──────────────────────────────────────────

def _view_for(action):
    view = DynamicModelViewSet()
    view.kwargs = {"app_label": "demo", "model_name": "Product"}
    view.action = action
    view.request = Request(APIRequestFactory().get("/"))
    return view


@pytest.mark.django_db
class TestViewSetRouting:
    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="replica")
    def test_list_routed_to_replica(self):
        assert _view_for("list").get_queryset().db == "replica"

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="replica")
    def test_retrieve_routed_to_replica(self):
        assert _view_for("retrieve").get_queryset().db == "replica"

    @override_settings(SNAPADMIN_ANALYTICS_DB_ALIAS="replica")
    def test_writes_stay_on_default(self):
        # get_object() for update/partial_update/destroy must never hit the
        # replica — evaluate get_queryset for each write action.
        for action in ("update", "partial_update", "destroy", "create"):
            assert _view_for(action).get_queryset().db == "default", action

    def test_unrouted_stays_on_default(self):
        assert _view_for("list").get_queryset().db == "default"
