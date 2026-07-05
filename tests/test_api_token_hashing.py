"""
tests/test_api_token_hashing.py — API tokens are hashed at rest (#W).

The raw key is never persisted: storage keeps a non-secret prefix and a SHA-256
digest, the raw key is exposed only on the instance that just minted it, and the
auth backend looks the token up by digest.
"""

import hashlib

import pytest
from django.contrib import admin as django_admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from rest_framework.test import APIClient

from snapadmin.models import APIToken, hash_token_key


# ── Hashing helper ─────────────────────────────────────────────────────────────

class TestHashTokenKey:
    def test_matches_sha256(self):
        assert hash_token_key("abc") == hashlib.sha256(b"abc").hexdigest()

    def test_is_deterministic(self):
        assert hash_token_key("xyz") == hash_token_key("xyz")


# ── At-rest storage ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenAtRest:
    def test_raw_key_available_on_creation(self, api_token):
        assert api_token.token_key is not None
        assert len(api_token.token_key) == 40

    def test_raw_key_not_persisted(self, api_token):
        reloaded = APIToken.objects.get(pk=api_token.pk)
        assert reloaded.token_key is None

    def test_prefix_is_first_8_chars(self, api_token):
        assert api_token.token_prefix == api_token.token_key[:8]

    def test_digest_is_hash_of_raw_key(self, api_token):
        assert api_token.token_digest == hash_token_key(api_token.token_key)

    def test_digest_persisted(self, api_token):
        reloaded = APIToken.objects.get(pk=api_token.pk)
        assert reloaded.token_digest == api_token.token_digest

    def test_bare_save_generates_key(self, admin_user):
        token = APIToken(user=admin_user, token_name="Bare")
        token.save()
        assert token.token_key is not None
        assert token.token_digest == hash_token_key(token.token_key)


# ── Authentication by digest ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuthByDigest:
    def test_valid_raw_key_authenticates(self, api_token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {api_token.token_key}")
        assert client.get("/api/tokens/").status_code == 200

    def test_digest_string_is_not_a_valid_key(self, api_token):
        # Presenting the stored digest itself must not authenticate.
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {api_token.token_digest}")
        assert client.get("/api/tokens/").status_code == 401


# ── Serializer exposure ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSerializerExposure:
    def test_create_response_exposes_raw_key_once(self, auth_client):
        r = auth_client.post("/api/tokens/", {"token_name": "Once"}, format="json")
        body = r.json()
        assert len(body["token_key"]) == 40
        assert body["token_prefix"] == body["token_key"][:8]

    def test_retrieve_hides_raw_key(self, auth_client, api_token):
        r = auth_client.get(f"/api/tokens/{api_token.pk}/")
        body = r.json()
        assert body["token_key"] is None
        assert body["token_prefix"] == api_token.token_prefix


# ── Admin display + one-time reveal ────────────────────────────────────────────

def _request_with_messages():
    request = RequestFactory().post("/")
    setattr(request, "session", "session")
    setattr(request, "_messages", FallbackStorage(request))
    return request


@pytest.mark.django_db
class TestAdminKeyDisplay:
    def _admin(self):
        from snapadmin.admin import APITokenAdmin
        return APITokenAdmin(APIToken, django_admin.site)

    def test_full_key_shows_raw_on_fresh_instance(self, api_token):
        assert self._admin().full_key(api_token) == api_token.token_key

    def test_full_key_masks_reloaded_token(self, api_token):
        reloaded = APIToken.objects.get(pk=api_token.pk)
        out = self._admin().full_key(reloaded)
        assert reloaded.token_prefix in out
        assert "••••••••" in out

    def test_full_key_dash_when_no_prefix(self):
        assert self._admin().full_key(APIToken()) == "—"

    def test_masked_key_uses_prefix(self, api_token):
        result = self._admin().masked_key(api_token)
        assert api_token.token_prefix in result[0]

    def test_save_model_reveals_raw_key_on_create(self, admin_user):
        request = _request_with_messages()
        obj = APIToken(user=admin_user, token_name="New")
        self._admin().save_model(request, obj, form=None, change=False)
        messages = [m.message for m in request._messages]
        assert any(obj.token_key in m for m in messages)

    def test_save_model_silent_on_change(self, api_token):
        request = _request_with_messages()
        reloaded = APIToken.objects.get(pk=api_token.pk)
        reloaded.token_name = "Renamed"
        self._admin().save_model(request, reloaded, form=None, change=True)
        assert list(request._messages) == []
