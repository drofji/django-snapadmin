"""
snapadmin/api/authentication.py

Custom DRF authentication backend for SnapAdmin API Tokens.
"""

import logging

from django.contrib.auth.base_user import AbstractBaseUser
from django.utils.module_loading import import_string
from rest_framework import authentication, exceptions

from snapadmin.conf import get_setting
from snapadmin.models import APIToken, hash_token_key
from snapadmin.tenancy import SnapTenantRebindMixin

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
    configured = get_setting("SNAPADMIN_API_AUTHENTICATION_CLASSES", None)
    if configured is None:
        return [APITokenAuthentication]
    return [
        import_string(entry) if isinstance(entry, str) else entry
        for entry in configured
    ]


class SnapAPIAuthMixin(SnapTenantRebindMixin):
    """Resolve authenticators per request from the SnapAdmin setting.

    DRF reads ``authentication_classes`` at class-definition time; resolving in
    ``get_authenticators()`` instead keeps the setting overridable at runtime
    (and in tests) without subclassing the views.

    Also rebinds the current tenant (#FUT1b) once DRF's own authentication
    has actually run — see :class:`~snapadmin.tenancy.SnapTenantRebindMixin`.
    """

    def get_authenticators(self):
        return [auth() for auth in get_api_authentication_classes()]


class APITokenAuthentication(authentication.BaseAuthentication):
    """DRF authentication against :class:`snapadmin.models.APIToken`.

    Reads a token from the ``Authorization`` header and resolves it to the
    owning user::

        Authorization: Token <key>

    Only the token's hash is stored, so the key itself is shown once at creation
    and never again. An expired, revoked or unknown key fails authentication.
    """

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


def token_has_scope(token: APIToken, scope: str) -> bool:
    """Whether ``token`` carries the free-form, project-defined ``scope``.

    For a project's own endpoints — SnapAdmin's generated model routes are
    gated by :func:`token_has_permission` instead, which this function does
    not touch. ``allowed_scopes`` means whatever the calling project decides;
    SnapAdmin only stores it and checks membership.

    Unlike ``allowed_models`` (empty delegates to Django permissions), an
    **empty** ``allowed_scopes`` denies every scope: there is no
    Django-permission equivalent an opaque, project-defined string could
    delegate to, so "nothing granted" can only mean "nothing allowed" — the
    fail-closed choice, and the one that makes a newly minted token gate
    something by default rather than passing every check until a project
    remembers to restrict it.

    Callers should AND this with their own authorization (Django
    permissions, object-level checks, …) — it narrows what a token may do
    and must never be the only thing standing between a request and a
    protected view.
    """
    return scope in (token.allowed_scopes or [])
