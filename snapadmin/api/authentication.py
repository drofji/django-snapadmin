"""
snapadmin/api/authentication.py

Custom DRF authentication backend for SnapAdmin API Tokens.
"""

import logging

from django.contrib.auth.models import User
from rest_framework import authentication, exceptions

from snapadmin.models import APIToken

logger = logging.getLogger("snapadmin.api.auth")


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
        try:
            token = (
                APIToken.objects
                .select_related("user")
                .get(token_key=token_key)
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
            extra={"token_name": token.token_name, "user": token.user.username},
        )

        return (token.user, token)

    def authenticate_header(self, request):
        return self.keyword


def token_has_permission(
    token: APIToken,
    user: User,
    app_label: str,
    model_name: str,
    action: str,
) -> bool:
    if not token.can_access_model(app_label, model_name):
        return False

    perm_codename = f"{app_label}.{action}_{model_name.lower()}"
    return user.has_perm(perm_codename)
