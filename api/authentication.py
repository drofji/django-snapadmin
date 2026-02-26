"""
api/authentication.py

Custom DRF authentication backend for SnapAdmin API Tokens.

Provides:
  - APITokenAuthentication : Validates "Authorization: Token <key>" headers.
  - token_has_permission   : Helper to check CRUD permissions per model.
"""

import logging

from django.contrib.auth.models import User
from rest_framework import authentication, exceptions

from api.models import APIToken

logger = logging.getLogger("snapadmin.api.auth")


class APITokenAuthentication(authentication.BaseAuthentication):
    """
    DRF authentication class that validates SnapAdmin API tokens.

    Clients must send:
        Authorization: Token <40-char-token-key>

    On success, sets ``request.user`` to the token's owner and
    ``request.auth`` to the APIToken instance (available in views).
    """

    keyword = "Token"

    def authenticate(self, request):
        """
        Attempt to authenticate the request using the Authorization header.

        Args:
            request: The incoming DRF request.

        Returns:
            (User, APIToken) tuple on success, or None if no token header present.

        Raises:
            AuthenticationFailed: When the token is present but invalid/expired.
        """
        auth_header = authentication.get_authorization_header(request).split()

        if not auth_header or auth_header[0].lower() != b"token":
            return None  # Not our scheme — let DRF try other backends

        if len(auth_header) == 1:
            raise exceptions.AuthenticationFailed(
                "Invalid token header: no token key provided."
            )
        if len(auth_header) > 2:
            raise exceptions.AuthenticationFailed(
                "Invalid token header: spaces are not allowed in token keys."
            )

        try:
            token_key = auth_header[1].decode("utf-8")
        except UnicodeDecodeError:
            raise exceptions.AuthenticationFailed(
                "Invalid token header: token key contained invalid characters."
            )

        return self._validate_token(token_key)

    def _validate_token(self, token_key: str):
        """
        Look up and validate the token by its key.

        Args:
            token_key: The raw token string from the Authorization header.

        Returns:
            (User, APIToken) tuple.

        Raises:
            AuthenticationFailed: For any invalid/expired/inactive state.
        """
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

        # Record last-used timestamp asynchronously (no blocking DB write in critical path)
        token.touch()

        logger.debug(
            "api_token_authenticated",
            extra={"token_name": token.token_name, "user": token.user.username},
        )

        return (token.user, token)

    def authenticate_header(self, request):  # noqa: ARG002
        """Return the WWW-Authenticate header value for 401 responses."""
        return self.keyword


def token_has_permission(
    token: APIToken,
    user: User,
    app_label: str,
    model_name: str,
    action: str,
) -> bool:
    """
    Check whether the token + user combination has permission to perform
    a CRUD action on a given model.

    Permission logic:
      1. The token must allow access to the model (or have an unrestricted model list).
      2. The user must hold the corresponding Django auth permission.

    Django's permission codenames follow the pattern:
      - view   → ``<app_label>.view_<modelname>``
      - add    → ``<app_label>.add_<modelname>``
      - change → ``<app_label>.change_<modelname>``
      - delete → ``<app_label>.delete_<modelname>``

    Args:
        token:      The authenticated APIToken instance.
        user:       The token's owner (request.user).
        app_label:  The Django app label (e.g. "demo").
        model_name: The model class name in lowercase (e.g. "product").
        action:     One of "view", "add", "change", "delete".

    Returns:
        True if both the token and user have the required permission.
    """
    if not token.can_access_model(app_label, model_name):
        return False

    perm_codename = f"{app_label}.{action}_{model_name.lower()}"
    return user.has_perm(perm_codename)
