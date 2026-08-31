"""
tests/test_api_token.py  –  APIToken model + authentication tests
"""

from datetime import timedelta
import pytest
from django.utils import timezone
from rest_framework.test import APIClient


# ── Key generation ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenKeyGeneration:
    def test_key_is_40_chars(self, api_token):
        assert len(api_token.token_key) == 40

    def test_key_is_alphanumeric(self, api_token):
        assert api_token.token_key.isalnum()

    def test_two_tokens_differ(self, db, admin_user):
        from snapadmin.models import APIToken
        t1 = APIToken.create_for_user(admin_user, "T1")
        t2 = APIToken.create_for_user(admin_user, "T2")
        assert t1.token_key != t2.token_key

    def test_key_unique_in_db(self, api_token):
        from snapadmin.models import APIToken
        assert APIToken.objects.filter(token_digest=api_token.token_digest).count() == 1


# ── Expiry / validity ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenExpiry:
    def test_no_expiry_not_expired(self, api_token):
        assert api_token.is_expired is False

    def test_future_expiry_not_expired(self, db, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "Future", expires_in_days=30)
        assert t.is_expired is False

    def test_past_expiry_is_expired(self, expired_token):
        assert expired_token.is_expired is True

    def test_valid_token_is_valid(self, api_token):
        assert api_token.is_valid is True

    def test_inactive_token_not_valid(self, inactive_token):
        assert inactive_token.is_valid is False

    def test_expired_token_not_valid(self, expired_token):
        assert expired_token.is_valid is False


# ── can_access_model ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCanAccessModel:
    def test_empty_list_allows_all(self, api_token):
        api_token.allowed_models = []
        for m in ("Product", "Customer", "Order"):
            assert api_token.can_access_model("demo", m) is True

    def test_restricted_allows_listed(self, restricted_token):
        assert restricted_token.can_access_model("demo", "Product") is True

    def test_restricted_denies_unlisted(self, restricted_token):
        assert restricted_token.can_access_model("demo", "Customer") is False

    def test_restricted_denies_wrong_app(self, restricted_token):
        assert restricted_token.can_access_model("other", "Product") is False


# ── touch() ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenTouch:
    def test_starts_none(self, api_token):
        assert api_token.last_used_at is None

    def test_touch_sets_timestamp(self, api_token):
        api_token.touch()
        api_token.refresh_from_db()
        assert api_token.last_used_at is not None

    def test_touch_is_recent(self, api_token):
        api_token.touch()
        api_token.refresh_from_db()
        assert (timezone.now() - api_token.last_used_at).total_seconds() < 5


# ── create_for_user ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCreateForUser:
    def test_persisted_to_db(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "New")
        assert APIToken.objects.filter(pk=t.pk).exists()

    def test_user_assigned(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "U")
        assert t.user == admin_user

    def test_expires_in_days(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "E", expires_in_days=7)
        assert t.expiration_date is not None
        assert (t.expiration_date - timezone.now()).days <= 7

    def test_no_expiry(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "NE")
        assert t.expiration_date is None

    def test_allowed_models_stored(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "M", allowed_models=["demo.Product"])
        assert "demo.Product" in t.allowed_models

    def test_default_allowed_models_empty(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "D")
        assert t.allowed_models == []

    def test_str_repr(self, api_token):
        assert "Test Token" in str(api_token)
        assert "testadmin" in str(api_token)


# ── Authentication backend ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAPITokenAuthentication:
    def test_valid_token_200(self, auth_client):
        assert auth_client.get("/api/tokens/").status_code == 200

    def test_no_header_401_or_403(self, anon_client):
        assert anon_client.get("/api/tokens/").status_code in (401, 403)

    def test_wrong_scheme_denied(self, anon_client):
        anon_client.credentials(HTTP_AUTHORIZATION="Bearer sometoken")
        assert anon_client.get("/api/tokens/").status_code in (401, 403)

    def test_invalid_key_401(self, anon_client):
        anon_client.credentials(HTTP_AUTHORIZATION="Token " + "z" * 40)
        assert anon_client.get("/api/tokens/").status_code == 401

    def test_expired_token_401(self, expired_token):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {expired_token.token_key}")
        assert c.get("/api/tokens/").status_code == 401

    def test_inactive_token_401(self, inactive_token):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {inactive_token.token_key}")
        assert c.get("/api/tokens/").status_code == 401

    def test_space_in_key_401(self, anon_client):
        anon_client.credentials(HTTP_AUTHORIZATION="Token abc def")
        assert anon_client.get("/api/tokens/").status_code == 401

    def test_valid_token_updates_last_used(self, auth_client, api_token):
        auth_client.get("/api/tokens/")
        api_token.refresh_from_db()
        assert api_token.last_used_at is not None

    def test_inactive_user_401(self, db, admin_user):
        from snapadmin.models import APIToken
        admin_user.is_active = False
        admin_user.save()
        token = APIToken.create_for_user(admin_user, "IU")
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        assert c.get("/api/tokens/").status_code == 401


# ── token_has_permission ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenHasPermission:
    def test_superuser_unrestricted_token_allowed(self, api_token, admin_user):
        from snapadmin.api.authentication import token_has_permission
        assert token_has_permission(api_token, admin_user, "demo", "product", "view") is True

    def test_restricted_token_blocks_unlisted_model(self, restricted_token, admin_user):
        from snapadmin.api.authentication import token_has_permission
        assert token_has_permission(restricted_token, admin_user, "demo", "customer", "view") is False

    def test_user_without_perm_denied(self, db, regular_user):
        from snapadmin.api.authentication import token_has_permission
        from snapadmin.models import APIToken
        token = APIToken.create_for_user(regular_user, "NP")
        assert token_has_permission(token, regular_user, "demo", "product", "delete") is False


# ── /api/tokens/ CRUD ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenAPIEndpoints:
    def test_list_returns_own_token(self, auth_client, api_token):
        r = auth_client.get("/api/tokens/")
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()["results"]]
        assert api_token.pk in ids

    def test_list_has_count_key(self, auth_client):
        assert "count" in auth_client.get("/api/tokens/").json()

    def test_create_returns_201(self, auth_client):
        r = auth_client.post("/api/tokens/", {"token_name": "CI"}, format="json")
        assert r.status_code == 201

    def test_created_key_is_40_chars(self, auth_client):
        r = auth_client.post("/api/tokens/", {"token_name": "Key Test"}, format="json")
        assert len(r.json()["token_key"]) == 40

    def test_create_with_expiry_sets_date(self, auth_client):
        r = auth_client.post("/api/tokens/", {"token_name": "E", "expires_in_days": 14}, format="json")
        assert r.json()["expiration_date"] is not None

    def test_create_with_allowed_models(self, auth_client):
        r = auth_client.post(
            "/api/tokens/",
            {"token_name": "S", "allowed_models": ["demo.Product"]},
            format="json",
        )
        assert "demo.Product" in r.json()["allowed_models"]

    def test_retrieve_own_token(self, auth_client, api_token):
        r = auth_client.get(f"/api/tokens/{api_token.pk}/")
        assert r.status_code == 200
        assert r.json()["id"] == api_token.pk

    def test_delete_own_token(self, auth_client, api_token):
        assert auth_client.delete(f"/api/tokens/{api_token.pk}/").status_code == 204

    def test_delete_removes_from_db(self, auth_client, api_token):
        from snapadmin.models import APIToken
        auth_client.delete(f"/api/tokens/{api_token.pk}/")
        assert not APIToken.objects.filter(pk=api_token.pk).exists()

    def test_unauthenticated_create_denied(self, anon_client):
        r = anon_client.post("/api/tokens/", {"token_name": "X"}, format="json")
        assert r.status_code in (401, 403)

    def test_response_has_is_valid_field(self, auth_client, api_token):
        r = auth_client.get(f"/api/tokens/{api_token.pk}/")
        assert r.json()["is_valid"] is True


# ── #TOK2a: allowed_scopes + token_has_scope ──────────────────────────────────

@pytest.mark.django_db
class TestAllowedScopes:
    def test_default_is_empty_list(self, api_token):
        assert api_token.allowed_scopes == []

    def test_create_for_user_stores_scopes(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "Scoped", allowed_scopes=["reports:read"])
        assert t.allowed_scopes == ["reports:read"]

    def test_invalid_scope_type_rejected(self, api_token):
        from django.core.exceptions import ValidationError
        api_token.allowed_scopes = ["reports:read", 42]
        with pytest.raises(ValidationError):
            api_token.full_clean()

    def test_blank_scope_string_rejected(self, api_token):
        from django.core.exceptions import ValidationError
        api_token.allowed_scopes = ["   "]
        with pytest.raises(ValidationError):
            api_token.full_clean()

    def test_not_a_list_rejected(self, api_token):
        from django.core.exceptions import ValidationError
        api_token.allowed_scopes = "reports:read"
        with pytest.raises(ValidationError):
            api_token.full_clean()


@pytest.mark.django_db
class TestTokenHasScope:
    def test_granted_scope_allowed(self, admin_user):
        from snapadmin.api.authentication import token_has_scope
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "S", allowed_scopes=["reports:read"])
        assert token_has_scope(t, "reports:read") is True

    def test_ungranted_scope_denied(self, admin_user):
        from snapadmin.api.authentication import token_has_scope
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(admin_user, "S", allowed_scopes=["reports:read"])
        assert token_has_scope(t, "reports:write") is False

    def test_empty_scopes_denies_by_default(self, api_token):
        # Unlike allowed_models, empty allowed_scopes is fail-closed: there is
        # no Django-permission equivalent for an opaque, project-defined
        # string to delegate to.
        from snapadmin.api.authentication import token_has_scope
        assert api_token.allowed_scopes == []
        assert token_has_scope(api_token, "anything") is False

    def test_independent_of_allowed_models(self, admin_user):
        # allowed_models and allowed_scopes are orthogonal checks — neither
        # leaks into the other.
        from snapadmin.api.authentication import token_has_scope
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(
            admin_user, "Both",
            allowed_models=["demo.Product"],
            allowed_scopes=["reports:read"],
        )
        assert t.can_access_model("demo", "Product") is True
        assert t.can_access_model("demo", "Customer") is False
        assert token_has_scope(t, "reports:read") is True
        assert token_has_scope(t, "reports:write") is False
        # A model grant does not imply a scope grant, and vice versa.
        assert t.can_access_model("other_app", "Whatever") is False
        assert token_has_scope(t, "demo.Product") is False


# ── #TOK2b: rotate ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenRotateModelMethod:
    def test_returns_a_new_40_char_key(self, api_token):
        old_key = api_token.token_key
        new_key = api_token.rotate()
        assert len(new_key) == 40
        assert new_key != old_key

    def test_row_identity_survives(self, api_token):
        pk = api_token.pk
        api_token.rotate()
        assert api_token.pk == pk

    def test_scopes_and_history_survive(self, admin_user):
        from snapadmin.models import APIToken
        t = APIToken.create_for_user(
            admin_user, "Rotate Me",
            allowed_models=["demo.Product"], allowed_scopes=["reports:read"],
        )
        created_at = t.created_at
        t.rotate()
        t.refresh_from_db()
        assert t.allowed_models == ["demo.Product"]
        assert t.allowed_scopes == ["reports:read"]
        assert t.created_at == created_at

    def test_old_key_stops_authenticating(self, api_token):
        old_key = api_token.token_key
        api_token.rotate()
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {old_key}")
        assert c.get("/api/tokens/").status_code == 401

    def test_new_key_authenticates(self, api_token):
        new_key = api_token.rotate()
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {new_key}")
        assert c.get("/api/tokens/").status_code == 200

    def test_rotate_written_to_audit_log(self, api_token):
        from snapadmin.models import SnapadminAuditLog
        before = SnapadminAuditLog.objects.count()
        api_token.rotate()
        assert SnapadminAuditLog.objects.count() == before + 1
        entry = SnapadminAuditLog.objects.latest("timestamp")
        assert entry.action == SnapadminAuditLog.Action.UPDATE
        assert entry.model == "apitoken"
        # The raw key must never reach the audit trail, only the prefix.
        assert "token_digest" in entry.changes
        for side in ("old", "new"):
            assert api_token.token_key not in str(entry.changes["token_digest"][side])


@pytest.mark.django_db
class TestTokenRotateEndpoint:
    def test_rotate_returns_200_with_new_key(self, auth_client, api_token):
        r = auth_client.post(f"/api/tokens/{api_token.pk}/rotate/")
        assert r.status_code == 200
        assert len(r.json()["token_key"]) == 40
        assert r.json()["token_key"] != api_token.token_key

    def test_rotate_persists(self, auth_client, api_token):
        r = auth_client.post(f"/api/tokens/{api_token.pk}/rotate/")
        new_key = r.json()["token_key"]
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {new_key}")
        assert c.get("/api/tokens/").status_code == 200

    def test_rotate_requires_authentication(self, anon_client, api_token):
        r = anon_client.post(f"/api/tokens/{api_token.pk}/rotate/")
        assert r.status_code in (401, 403)

    def test_owner_can_rotate_own_token_without_superuser(self, db, regular_user):
        from snapadmin.models import APIToken
        token = APIToken.create_for_user(regular_user, "Mine")
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        r = c.post(f"/api/tokens/{token.pk}/rotate/")
        assert r.status_code == 200

    def test_non_owner_cannot_rotate_others_token(self, db, regular_user, api_token):
        from snapadmin.models import APIToken
        other = APIToken.create_for_user(regular_user, "Not Yours")
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {other.token_key}")
        r = c.post(f"/api/tokens/{api_token.pk}/rotate/")
        assert r.status_code in (403, 404)


# ── #TOK2c: deactivate — the documented revocation path ────────────────────────

@pytest.mark.django_db
class TestTokenDeactivateEndpoint:
    def test_deactivate_flips_is_active_false(self, auth_client, api_token):
        r = auth_client.post(f"/api/tokens/{api_token.pk}/deactivate/")
        assert r.status_code == 200
        assert r.json()["is_active"] is False
        api_token.refresh_from_db()
        assert api_token.is_active is False

    def test_deactivate_keeps_the_row(self, auth_client, api_token):
        from snapadmin.models import APIToken
        auth_client.post(f"/api/tokens/{api_token.pk}/deactivate/")
        assert APIToken.objects.filter(pk=api_token.pk).exists()

    def test_deactivated_token_stops_authenticating(self, auth_client, api_token):
        auth_client.post(f"/api/tokens/{api_token.pk}/deactivate/")
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {api_token.token_key}")
        assert c.get("/api/tokens/").status_code == 401

    def test_owner_can_deactivate_own_token_without_superuser(self, db, regular_user):
        from snapadmin.models import APIToken
        token = APIToken.create_for_user(regular_user, "Mine")
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        r = c.post(f"/api/tokens/{token.pk}/deactivate/")
        assert r.status_code == 200

    def test_non_owner_cannot_deactivate_others_token(self, db, regular_user, api_token):
        from snapadmin.models import APIToken
        other = APIToken.create_for_user(regular_user, "Not Yours")
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Token {other.token_key}")
        r = c.post(f"/api/tokens/{api_token.pk}/deactivate/")
        assert r.status_code in (403, 404)
