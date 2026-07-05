"""
snapadmin/api/authentication.py

Custom DRF authentication backend for SnapAdmin API Tokens.
"""

import logging

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.utils.module_loading import import_string
from rest_framework import authentication, exceptions

from snapadmin.models import APIToken, hash_token_key

logger = logging.getLogger("snapadmin.api.auth")


def get_api_authentication_classes() -> list[type]:
    """Authentication classes used by the SnapAdmin API views.

    Configurable via ``SNAPADMIN_API_AUTHENTICATION_CLASSES`` — a list of
    dotted paths (or classes), exactly like DRF's own setting::

        SNAPADMIN_API_AUTHENTICATION_CLASSES = [
            "rest_framework_simplejwt.authentication.JWTAuthentication",
            "rest_framework.authentication.SessionAuthentication",
            "snapadmin.api.authentication.APITokenAuthentication",
        ]

    Default is SnapAdmin's own token auth only, preserving the pre-a6
    behaviour. With non-token schemes, model CRUD permissions fall back to
    plain Django model permissions (see ``TokenModelPermission``).
    """
    configured = getattr(settings, "SNAPADMIN_API_AUTHENTICATION_CLASSES", None)
    if configured is None:
        return [APITokenAuthentication]
    return [
        import_string(entry) if isinstance(entry, str) else entry
        for entry in configured
    ]


class SnapAPIAuthMixin:
    """Resolve authenticators per request from the SnapAdmin setting.

    DRF reads ``authentication_classes`` at class-definition time; resolving in
    ``get_authenticators()`` instead keeps the setting overridable at runtime
    (and in tests) without subclassing the views.
    """

    def get_authenticators(self):
        return [auth() for auth in get_api_authentication_classes()]


class APITokenAuthentication(authentication.BaseAuthentication):
    keyword = "Token"

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).split()

        if not auth_header or auth_header[0].lower() != b"token":
            return None

        if len(auth_header) == 1:
            raise exceptions.AuthenticationFailed("Invalid token header: no token key provided.")
        if len(auth_header) > 2:
            raise exceptions.AuthenticationFailed("Invalid token header: spaces are not allowed in token keys.")

        try:
            token_key = auth_header[1].decode("utf-8")
        except UnicodeDecodeError:
            raise exceptions.AuthenticationFailed("Invalid token header: token key contained invalid characters.")

        return self._validate_token(token_key)

    def _validate_token(self, token_key: str):
        # The raw key is never stored; look it up by its SHA-256 digest.
        try:
            token = (
                APIToken.objects
                .select_related("user")
                .get(token_digest=hash_token_key(token_key))
            )
        except APIToken.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid token.")

        if not token.is_active:
            raise exceptions.AuthenticationFailed("Token has been disabled.")

        if token.is_expired:
            raise exceptions.AuthenticationFailed("Token has expired.")

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed("User account is disabled.")

        token.touch()

        logger.debug(
            "api_token_authenticated",
            extra={"token_name": token.token_name, "user": token.user.get_username()},
        )

        return (token.user, token)

    def authenticate_header(self, request):
        return self.keyword


def token_has_permission(
    token: APIToken,
    user: AbstractBaseUser,
    app_label: str,
    model_name: str,
    action: str,
) -> bool:
    if not token.can_access_model(app_label, model_name):
        return False

    perm_codename = f"{app_label}.{action}_{model_name.lower()}"
    return user.has_perm(perm_codename)
