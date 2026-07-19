"""
Tests for pluggable API authentication (v0.1.0a6):

  SNAPADMIN_API_AUTHENTICATION_CLASSES resolves dotted paths / classes,
  SnapAdmin API views pick it up per request, and TokenModelPermission
  falls back to plain Django model permissions for non-APIToken auth
  (session, JWT) instead of hard-rejecting.
"""

import pytest
from django.contrib.auth.models import Permission, User
from django.test import RequestFactory, override_settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.test import APIClient

from snapadmin.api.authentication import (
    APITokenAuthentication,
    get_api_authentication_classes,
)
from snapadmin.api.views import TokenModelPermission


SESSION_ONLY = ["rest_framework.authentication.SessionAuthentication"]


def _grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return User.objects.get(pk=user.pk)  # refresh perm cache


class TestAuthenticationClassesSetting:
    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=None)
    def test_default_is_api_token_auth(self):
        # Package default when the setting is unset (the demo project overrides it).
        assert get_api_authentication_classes() == [APITokenAuthentication]

    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=SESSION_ONLY)
    def test_dotted_paths_resolved(self):
        assert get_api_authentication_classes() == [SessionAuthentication]

    @override_settings(
        SNAPADMIN_API_AUTHENTICATION_CLASSES=[SessionAuthentication]
    )
    def test_class_objects_accepted(self):
        assert get_api_authentication_classes() == [SessionAuthentication]

    @override_settings(
        SNAPADMIN_API_AUTHENTICATION_CLASSES=[
            "rest_framework.authentication.SessionAuthentication",
            "snapadmin.api.authentication.APITokenAuthentication",
        ]
    )
    def test_mixed_list_order_preserved(self):
        assert get_api_authentication_classes() == [
            SessionAuthentication,
            APITokenAuthentication,
        ]


@pytest.mark.django_db
class TestTokenModelPermissionFallback:
    """Non-APIToken auth (session/JWT) → plain Django model permissions."""

    class _FakeView:
        action = "list"

        def __init__(self, app_label="demo", model_name="Product"):
            self.kwargs = {"app_label": app_label, "model_name": model_name}

    def _request(self, user, auth=None):
        request = RequestFactory().get("/api/models/demo/Product/")
        request.user = user
        request.auth = auth
        return request

    def test_user_with_model_perm_allowed(self, regular_user):
        user = _grant(regular_user, "view_product")
        assert TokenModelPermission().has_permission(
            self._request(user), self._FakeView()
        ) is True

    def test_user_without_model_perm_denied(self, regular_user):
        assert TokenModelPermission().has_permission(
            self._request(regular_user), self._FakeView()
        ) is False

    def test_anonymous_denied(self):
        from django.contrib.auth.models import AnonymousUser

        assert TokenModelPermission().has_permission(
            self._request(AnonymousUser()), self._FakeView()
        ) is False

    def test_missing_user_denied(self):
        request = RequestFactory().get("/x/")
        request.auth = None
        assert TokenModelPermission().has_permission(request, self._FakeView()) is False

    def test_api_token_path_still_scoped(self, api_token):
        # Tokens keep the stricter contract: allowed_models scope AND user perms.
        request = self._request(api_token.user, auth=api_token)
        assert TokenModelPermission().has_permission(request, self._FakeView()) is True
        api_token.allowed_models = ["demo.Order"]
        assert TokenModelPermission().has_permission(request, self._FakeView()) is False


@pytest.mark.django_db
class TestSessionAuthEndToEnd:
    """With session auth configured, model CRUD works without any APIToken."""

    def _client(self, user):
        client = APIClient()
        client.force_login(user)
        return client

    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=SESSION_ONLY)
    def test_model_list_with_session_and_perm(self, regular_user, product):
        user = _grant(regular_user, "view_product")
        response = self._client(user).get("/api/models/demo/Product/")
        assert response.status_code == 200
        assert response.data["count"] == 1

    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=SESSION_ONLY)
    def test_model_list_without_perm_forbidden(self, regular_user, product):
        response = self._client(regular_user).get("/api/models/demo/Product/")
        assert response.status_code == 403

    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=SESSION_ONLY)
    def test_schema_view_with_session(self, admin_user):
        response = self._client(admin_user).get("/api/models/schema/")
        assert response.status_code == 200
        assert response.data["count"] > 0

    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=SESSION_ONLY)
    def test_token_header_ignored_when_not_configured(self, api_token, product):
        # Only SessionAuthentication is active → a valid token header alone
        # no longer authenticates.
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {api_token.token_key}")
        response = client.get("/api/models/demo/Product/")
        assert response.status_code in (401, 403)

    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=SESSION_ONLY)
    def test_anonymous_still_rejected(self, product):
        response = APIClient().get("/api/models/demo/Product/")
        assert response.status_code in (401, 403)


@pytest.mark.django_db
class TestTokenModelPermissionActionMap:
    """The action → permission map (and its ``view`` default) gates every verb.

    Regression cover for #TEST1: a mutating action must require its own
    add/change/delete permission and never slip through on ``view``; a custom
    or otherwise unmapped action (``count``/``export``, or anything the map
    doesn't know) must fall back to the safe ``view`` floor — never to an
    unauthenticated-open ``.get(action, <no check>)``.
    """

    class _View:
        def __init__(self, action, app_label="demo", model_name="Product"):
            self.action = action
            self.kwargs = {"app_label": app_label, "model_name": model_name}

    def _allows(self, user, action) -> bool:
        request = RequestFactory().get("/api/models/demo/Product/")
        request.user = user
        request.auth = None
        return TokenModelPermission().has_permission(request, self._View(action))

    @pytest.mark.parametrize("action, needed", [
        ("create", "add_product"),
        ("update", "change_product"),
        ("partial_update", "change_product"),
        ("destroy", "delete_product"),
    ])
    def test_mutating_action_denied_with_only_view(self, regular_user, action, needed):
        user = _grant(regular_user, "view_product")
        assert self._allows(user, action) is False

    @pytest.mark.parametrize("action, needed", [
        ("create", "add_product"),
        ("update", "change_product"),
        ("partial_update", "change_product"),
        ("destroy", "delete_product"),
    ])
    def test_mutating_action_allowed_with_its_permission(self, regular_user, action, needed):
        user = _grant(regular_user, needed)
        assert self._allows(user, action) is True

    @pytest.mark.parametrize("action", ["count", "export"])
    def test_readonly_custom_action_requires_view(self, regular_user, action):
        # Not in _action_map → resolves to the `view` floor, not "open".
        assert self._allows(regular_user, action) is False          # no perms
        user = _grant(regular_user, "view_product")
        assert self._allows(user, action) is True

    def test_unmapped_action_defaults_to_view_not_open(self, regular_user):
        # An action the map doesn't know still demands *at least* view — the
        # `.get(action, "view")` default must never degrade to no permission check.
        assert self._allows(regular_user, "frobnicate") is False
        user = _grant(regular_user, "view_product")
        assert self._allows(user, "frobnicate") is True

    def test_add_permission_does_not_grant_read_actions(self, regular_user):
        # `add` implies neither `view` nor read-only custom actions (count/export).
        user = _grant(regular_user, "add_product")
        assert self._allows(user, "count") is False
        assert self._allows(user, "list") is False


@pytest.mark.django_db
class TestReadOnlyUserReachesReadActionsOnlyEndToEnd:
    """End-to-end: a session user holding only ``view`` can read (list / count /
    export) but is 403'd on the mutating verbs — proving the map is wired through
    the live viewset, not just the permission class in isolation."""

    def _client(self, user):
        client = APIClient()
        client.force_login(user)
        return client

    @override_settings(SNAPADMIN_API_AUTHENTICATION_CLASSES=SESSION_ONLY)
    def test_view_only_reaches_reads_but_not_create(self, regular_user, product):
        user = _grant(regular_user, "view_product")
        client = self._client(user)
        assert client.get("/api/models/demo/Product/count/").status_code == 200
        assert client.get("/api/models/demo/Product/export/").status_code == 200
        created = client.post(
            "/api/models/demo/Product/",
            {"name": "New", "price": "1.00", "available": True},
            format="json",
        )
        assert created.status_code == 403
