"""
tests/test_snap_action.py — user-defined REST actions (#RFC1h)

`@snap_action` turns a model method into a callable REST endpoint
(`POST /api/models/<app>/<Model>/<pk>/<name>/`, or the list-level route with
`detail=False`) bound by the model's own `api_read_only`/
`api_http_method_names` policy and a derived or explicit Django permission.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import override_settings
from rest_framework.response import Response
from rest_framework.test import APIClient

from snapadmin.api.views import (
    SnapActionError,
    SnapActionSpec,
    get_snap_action,
    iter_snap_actions,
    snap_action,
)


# ── snap_action() decorator ─────────────────────────────────────────────────

class TestSnapActionDecorator:
    def test_marks_the_function_with_a_spec(self):
        @snap_action()
        def approve(self, request):
            return {}

        spec = approve.__snapadmin_action__
        assert isinstance(spec, SnapActionSpec)
        assert spec.name == "approve"
        assert spec.func is approve

    def test_defaults_detail_true_methods_post(self):
        @snap_action()
        def approve(self, request):
            return {}

        spec = approve.__snapadmin_action__
        assert spec.detail is True
        assert spec.methods == frozenset({"post"})
        assert spec.permission is None

    def test_methods_are_lowercased(self):
        @snap_action(methods=["POST", "Put"])
        def approve(self, request):
            return {}

        assert approve.__snapadmin_action__.methods == frozenset({"post", "put"})

    def test_detail_false_and_explicit_permission(self):
        @snap_action(detail=False, methods=("get",), permission="demo.custom_perm")
        def bulk_summary(cls, request):
            return {}

        spec = bulk_summary.__snapadmin_action__
        assert spec.detail is False
        assert spec.permission == "demo.custom_perm"

    def test_empty_methods_raises(self):
        with pytest.raises(ValueError):
            snap_action(methods=())

    def test_function_stays_an_ordinary_callable(self):
        @snap_action()
        def approve(self, request):
            return {"self": self, "request": request}

        assert approve("instance", "req") == {"self": "instance", "request": "req"}


# ── get_snap_action() / iter_snap_actions() ─────────────────────────────────

@pytest.mark.django_db
class TestActionDiscovery:
    def test_get_snap_action_finds_a_declared_action(self):
        from demo.apps.shop.models import Order

        spec = get_snap_action(Order, "recalculate_total")
        assert spec is not None
        assert spec.name == "recalculate_total"
        assert spec.detail is True
        assert spec.methods == frozenset({"post"})

    def test_get_snap_action_returns_none_for_a_plain_method(self):
        from demo.apps.shop.models import Order

        assert get_snap_action(Order, "save") is None

    def test_get_snap_action_returns_none_for_an_unknown_name(self):
        from demo.apps.shop.models import Order

        assert get_snap_action(Order, "does_not_exist") is None

    def test_get_snap_action_does_not_see_an_inherited_action(self):
        # Own-class-dict-only scoping, mirroring @snap_property's precedent.
        from demo.apps.shop.models import Order

        class SubOrder(Order):
            class Meta:
                proxy = True
                app_label = "demo"

        assert get_snap_action(SubOrder, "recalculate_total") is None
        assert get_snap_action(Order, "recalculate_total") is not None

    def test_iter_snap_actions_lists_declared_actions(self):
        from demo.apps.shop.models import Order, Product

        names = {spec.name for spec in iter_snap_actions(Order)}
        assert "recalculate_total" in names
        assert iter_snap_actions(Product) == []


# ── dispatch_action() — HTTP round trip ─────────────────────────────────────

@pytest.mark.django_db
class TestDispatchActionSuccess:
    def test_detail_action_returns_200(self, auth_client, order):
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 200

    def test_action_recomputes_the_total_from_order_items(self, auth_client, order):
        from demo.apps.shop.models import OrderItem, Product

        product = Product.objects.create(name="Widget", price=Decimal("5.00"))
        OrderItem.objects.create(order=order, product=product, quantity=3, price=Decimal("5.00"))
        OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal("2.50"))

        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 200
        assert r.json()["total"] == "17.50"
        order.refresh_from_db()
        assert order.total == Decimal("17.50")

    def test_action_with_no_order_items_zeroes_the_total(self, auth_client, order):
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.json()["total"] == "0"


@pytest.mark.django_db
class TestDispatchActionErrors:
    def test_unknown_action_name_404s(self, auth_client, order):
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/does-not-exist/")
        assert r.status_code == 404

    def test_wrong_http_method_405s(self, auth_client, order):
        r = auth_client.get(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 405

    def test_detail_action_reached_via_the_list_route_404s(self, auth_client):
        # recalculate_total is detail=True; the list-level URL for the same
        # name must not resolve to it.
        r = auth_client.post("/api/models/demo/Order/recalculate_total/")
        assert r.status_code == 404

    def test_unauthenticated_denied(self, anon_client, order):
        r = anon_client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code in (401, 403)

    def test_unknown_model_404s(self, auth_client):
        # Inherited from #API3a's dispatch-level guard (lane 5) — no
        # duplicated model-resolution code in dispatch_action itself.
        r = auth_client.post("/api/models/demo/NoSuchModel/1/whatever/")
        assert r.status_code == 404


@pytest.mark.django_db
class TestSnapActionPermission:
    @staticmethod
    def _client_with_perm(user, codename, app_label="demo"):
        from snapadmin.models import APIToken

        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
        fresh = get_user_model().objects.get(pk=user.pk)
        token = APIToken.create_for_user(fresh, "Test")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        return client

    def test_denied_without_the_default_permission(self, regular_user, order):
        from snapadmin.models import APIToken

        token = APIToken.create_for_user(regular_user, "NoPerm")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        r = client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 403

    def test_view_permission_alone_is_not_enough(self, regular_user, order):
        # recalculate_total's methods=("post",) is not a safe-method set, so
        # its default permission is change_order, not view_order.
        client = self._client_with_perm(regular_user, "view_order")
        r = client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 403

    def test_granted_with_the_default_change_permission(self, regular_user, order):
        client = self._client_with_perm(regular_user, "change_order")
        r = client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 200

    def test_superuser_bypasses_the_permission_check(self, auth_client, order):
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 200

    def test_token_scope_restricts_the_action(self, db, restricted_token, order):
        # restricted_token only carries allowed_models=["demo.Product"].
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {restricted_token.token_key}")
        r = client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code in (403, 404)

    def test_explicit_permission_override(self, monkeypatch, regular_user, order):
        from demo.apps.shop.models import Order

        @snap_action(permission="demo.delete_order")
        def custom_action(self, request):
            return {"ok": True}

        monkeypatch.setattr(Order, "custom_action", custom_action, raising=False)

        denied_client = APIClient()
        from snapadmin.models import APIToken
        denied_token = APIToken.create_for_user(regular_user, "Denied")
        denied_client.credentials(HTTP_AUTHORIZATION=f"Token {denied_token.token_key}")
        r = denied_client.post(f"/api/models/demo/Order/{order.pk}/custom_action/")
        assert r.status_code == 403

        granted_client = self._client_with_perm(regular_user, "delete_order")
        r = granted_client.post(f"/api/models/demo/Order/{order.pk}/custom_action/")
        assert r.status_code == 200


@pytest.mark.django_db
class TestSnapActionInheritsReadOnlyGuard:
    """The permission-bypass test the lane's own Verify block asks for:
    api_read_only=True blocks a @snap_action the same way it blocks a
    regular write verb — both via the same http_method_names descriptor
    DRF's own dispatch() already consults, inherited rather than
    re-implemented, so both answer 405 (Method Not Allowed), not 403.
    """

    def test_read_only_model_blocks_a_post_action(self, monkeypatch, auth_client, order):
        from demo.apps.shop.models import Order

        monkeypatch.setattr(Order, "api_read_only", True, raising=False)
        action_response = auth_client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        write_verb_response = auth_client.patch(
            f"/api/models/demo/Order/{order.pk}/", {"total": "1.00"}, format="json"
        )
        assert action_response.status_code == 405
        assert write_verb_response.status_code == 405

    def test_api_http_method_names_without_post_blocks_the_action(self, monkeypatch, auth_client, order):
        from demo.apps.shop.models import Order

        monkeypatch.setattr(Order, "api_http_method_names", ["get"], raising=False)
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/recalculate_total/")
        assert r.status_code == 405

    def test_read_only_model_still_allows_a_get_only_action(self, monkeypatch, auth_client, order):
        from demo.apps.shop.models import Order

        @snap_action(methods=("get",))
        def summary(self, request):
            return {"total": str(self.total)}

        monkeypatch.setattr(Order, "summary", summary, raising=False)
        monkeypatch.setattr(Order, "api_read_only", True, raising=False)
        r = auth_client.get(f"/api/models/demo/Order/{order.pk}/summary/")
        assert r.status_code == 200


@pytest.mark.django_db
class TestSnapActionReturnShapes:
    def test_dict_return_is_wrapped_in_a_200_response(self, monkeypatch, auth_client, order):
        from demo.apps.shop.models import Order

        @snap_action()
        def plain_dict(self, request):
            return {"hello": "world"}

        monkeypatch.setattr(Order, "plain_dict", plain_dict, raising=False)
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/plain_dict/")
        assert r.status_code == 200
        assert r.json() == {"hello": "world"}

    def test_response_object_is_returned_unchanged(self, monkeypatch, auth_client, order):
        from demo.apps.shop.models import Order

        @snap_action()
        def custom_response(self, request):
            return Response({"custom": True}, status=201)

        monkeypatch.setattr(Order, "custom_response", custom_response, raising=False)
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/custom_response/")
        assert r.status_code == 201
        assert r.json() == {"custom": True}

    def test_snap_action_error_becomes_the_declared_status(self, monkeypatch, auth_client, order):
        from demo.apps.shop.models import Order

        @snap_action()
        def always_fails(self, request):
            raise SnapActionError("cannot approve a cancelled order", status=409)

        monkeypatch.setattr(Order, "always_fails", always_fails, raising=False)
        r = auth_client.post(f"/api/models/demo/Order/{order.pk}/always_fails/")
        assert r.status_code == 409
        assert r.json() == {"detail": "cannot approve a cancelled order"}


@pytest.mark.django_db
class TestListLevelAction:
    def test_list_level_action_receives_the_model_class(self, monkeypatch, auth_client):
        from demo.apps.shop.models import Product

        seen = {}

        @snap_action(detail=False, methods=("post",), permission="demo.add_product")
        def bulk_thing(cls, request):
            seen["cls"] = cls
            return {"ok": True}

        monkeypatch.setattr(Product, "bulk_thing", bulk_thing, raising=False)
        r = auth_client.post("/api/models/demo/Product/bulk_thing/")
        assert r.status_code == 200
        assert seen["cls"] is Product

    def test_list_action_reached_via_the_detail_route_404s(self, monkeypatch, auth_client, product):
        from demo.apps.shop.models import Product

        @snap_action(detail=False)
        def bulk_thing(cls, request):
            return {}

        monkeypatch.setattr(Product, "bulk_thing", bulk_thing, raising=False)
        r = auth_client.post(f"/api/models/demo/Product/{product.pk}/bulk_thing/")
        assert r.status_code == 404
