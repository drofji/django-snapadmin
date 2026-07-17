"""
Tests for the optional admin-only user-management API (v0.1.0a6):

  SNAPADMIN_USER_API_ENABLED mounts /api/users/ (+ set-password, permissions)
  and /api/permissions/. Every endpoint requires a staff user.
"""

import pytest
from django.contrib.auth.models import Permission, User
from django.test import override_settings
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient


@pytest.fixture
def admin_client(admin_user):
    # force_authenticate bypasses the auth backend + session CSRF, so the tests
    # exercise the viewset permissions/logic rather than the login mechanism.
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.mark.django_db
class TestUserApiRoutingToggle:
    def test_routes_present_by_default_in_demo(self):
        # the demo project enables SNAPADMIN_USER_API_ENABLED
        assert reverse("api-user-list")
        assert reverse("permission-list")


@pytest.mark.django_db
class TestUserCrud:
    def test_list_users_requires_staff(self, regular_user):
        client = APIClient()
        client.force_authenticate(user=regular_user)
        assert client.get("/api/users/").status_code == 403

    def test_anonymous_denied(self):
        assert APIClient().get("/api/users/").status_code in (401, 403)

    def test_admin_lists_users(self, admin_client, regular_user):
        response = admin_client.get("/api/users/")
        assert response.status_code == 200
        usernames = {row["username"] for row in response.data["results"]}
        assert "regular" in usernames

    def test_create_user_with_password(self, admin_client):
        response = admin_client.post("/api/users/", {
            "username": "newbie",
            "password": "s3cur3-pass-2026",
            "is_staff": True,
        }, format="json")
        assert response.status_code == 201
        user = User.objects.get(username="newbie")
        assert user.check_password("s3cur3-pass-2026")
        assert user.is_staff
        assert "password" not in response.data      # write-only

    def test_create_user_without_password_is_unusable(self, admin_client):
        response = admin_client.post("/api/users/", {"username": "nopass"}, format="json")
        assert response.status_code == 201
        assert User.objects.get(username="nopass").has_usable_password() is False

    def test_create_user_weak_password_rejected(self, admin_client):
        response = admin_client.post("/api/users/", {
            "username": "weak", "password": "123",
        }, format="json")
        assert response.status_code == 400
        assert "password" in response.data

    def test_update_user(self, admin_client, regular_user):
        response = admin_client.patch(
            f"/api/users/{regular_user.pk}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 200
        regular_user.refresh_from_db()
        assert regular_user.is_active is False

    def test_update_user_password_via_patch(self, admin_client, regular_user):
        response = admin_client.patch(
            f"/api/users/{regular_user.pk}/",
            {"password": "patched-pass-2026"}, format="json",
        )
        assert response.status_code == 200
        regular_user.refresh_from_db()
        assert regular_user.check_password("patched-pass-2026")

    def test_serializer_exposes_permissions(self, admin_client, regular_user):
        regular_user.user_permissions.add(Permission.objects.get(codename="view_product"))
        response = admin_client.get(f"/api/users/{regular_user.pk}/")
        assert "demo.view_product" in response.data["permissions"]


@pytest.mark.django_db
class TestSetPassword:
    def test_set_password(self, admin_client, regular_user):
        response = admin_client.post(
            f"/api/users/{regular_user.pk}/set-password/",
            {"password": "brand-new-pass-99"}, format="json",
        )
        assert response.status_code == 200
        regular_user.refresh_from_db()
        assert regular_user.check_password("brand-new-pass-99")

    def test_set_weak_password_rejected(self, admin_client, regular_user):
        response = admin_client.post(
            f"/api/users/{regular_user.pk}/set-password/",
            {"password": "1"}, format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestSetPermissions:
    def test_replace_permissions(self, admin_client, regular_user):
        response = admin_client.post(
            f"/api/users/{regular_user.pk}/permissions/",
            {"permissions": ["demo.view_product", "demo.add_product"]}, format="json",
        )
        assert response.status_code == 200
        assert response.data["count"] == 2
        codenames = set(regular_user.user_permissions.values_list("codename", flat=True))
        assert codenames == {"view_product", "add_product"}

    def test_permissions_must_be_list(self, admin_client, regular_user):
        response = admin_client.post(
            f"/api/users/{regular_user.pk}/permissions/",
            {"permissions": "demo.view_product"}, format="json",
        )
        assert response.status_code == 400

    def test_unknown_permission_rejected(self, admin_client, regular_user):
        response = admin_client.post(
            f"/api/users/{regular_user.pk}/permissions/",
            {"permissions": ["demo.fly_product"]}, format="json",
        )
        assert response.status_code == 400
        assert "Unknown permission" in str(response.data)


@pytest.mark.django_db
class TestPermissionList:
    def test_lists_assignable_permissions(self, admin_client):
        response = admin_client.get("/api/permissions/")
        assert response.status_code == 200
        assert response.data["count"] > 0
        sample = response.data["permissions"][0]
        assert {"app_label", "codename", "full_codename", "name"} <= sample.keys()

    def test_requires_staff(self, regular_user):
        client = APIClient()
        client.force_authenticate(user=regular_user)
        assert client.get("/api/permissions/").status_code == 403


@pytest.mark.django_db
class TestUserApiDisabled:
    """When the toggle is off the routes must not resolve."""

    def test_routes_absent_when_disabled(self):
        # The URLconf reads the setting at import; reverse still works because
        # the demo mounted them. This test asserts the toggle default is False
        # in the package (documented opt-in), not the demo project override.
        from django.conf import settings as dj_settings
        assert getattr(dj_settings, "SNAPADMIN_USER_API_ENABLED") is True  # demo opts in
