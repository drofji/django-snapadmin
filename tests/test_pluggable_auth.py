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
        # Package default when the setting is unset (the sandbox overrides it).
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
