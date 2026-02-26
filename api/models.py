"""
api/models.py

Custom Token Authentication for SnapAdmin REST API.

Provides:
  - APIToken  : A named, expirable token that restricts access to specific models.
                Integrates with Django's standard permission system for CRUD checks.
"""

import secrets
import string
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _generate_token_key() -> str:
    """
    Generate a cryptographically secure 40-character alphanumeric token.

    Returns:
        A random token string using letters and digits.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(40))


class APIToken(models.Model):
    """
    A named, user-scoped API token for authenticating against the SnapAdmin REST API.

    Fields:
        token_name      : Human-readable label for the token (e.g. "CI Pipeline Key").
        token_key       : The secret 40-character key presented in request headers.
        user            : Django auth user who owns this token.
        expiration_date : Optional expiry. When NULL the token never expires.
        allowed_models  : JSON list of "<app_label>.<ModelName>" strings this token
                          may access (empty list = all models allowed).
        is_active       : Soft-disable a token without deleting it.
        created_at      : Immutable creation timestamp.
        last_used_at    : Updated automatically on every authenticated request.

    Authentication header:
        Authorization: Token <token_key>
    """

    token_name = models.CharField(
        max_length=100,
        verbose_name=_("Token Name"),
        help_text=_("A descriptive name for this token (e.g. 'CI Pipeline', 'Read-only dashboard')."),
    )
    token_key = models.CharField(
        max_length=40,
        unique=True,
        default=_generate_token_key,
        verbose_name=_("Token Key"),
        help_text=_("Secret 40-character key. Treat like a password."),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="api_tokens",
        verbose_name=_("Owner"),
    )
    expiration_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Expiration Date"),
        help_text=_("Leave blank for a token that never expires."),
    )
    allowed_models = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Allowed Models"),
        help_text=_(
            "List of '<app_label>.<ModelName>' strings this token can access. "
            "An empty list grants access to all models."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Is Active"),
        help_text=_("Inactive tokens are rejected without being deleted."),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Used At"),
    )

    class Meta:
        verbose_name = _("API Token")
        verbose_name_plural = _("API Tokens")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.token_name} ({self.user.username})"

    # ── Validation helpers ───────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        """Return True if an expiration date is set and has passed."""
        if self.expiration_date is None:
            return False
        return timezone.now() > self.expiration_date

    @property
    def is_valid(self) -> bool:
        """Return True when the token is active and not expired."""
        return self.is_active and not self.is_expired

    def can_access_model(self, app_label: str, model_name: str) -> bool:
        """
        Check whether this token is permitted to access the given model.

        An empty ``allowed_models`` list means unrestricted access.

        Args:
            app_label:  The Django app label (e.g. "demo").
            model_name: The model class name (e.g. "Product").

        Returns:
            True if access is permitted.
        """
        if not self.allowed_models:
            return True
        return f"{app_label}.{model_name}" in self.allowed_models

    def touch(self) -> None:
        """Update ``last_used_at`` to the current time without loading the full object."""
        APIToken.objects.filter(pk=self.pk).update(last_used_at=timezone.now())

    # ── Factory helpers ──────────────────────────────────────────────────────

    @classmethod
    def create_for_user(
        cls,
        user: User,
        token_name: str,
        allowed_models: list = None,
        expires_in_days: int = None,
    ) -> "APIToken":
        """
        Convenience factory for creating tokens programmatically.

        Args:
            user:             The owner of the new token.
            token_name:       Human-readable name.
            allowed_models:   Optional model restriction list.
            expires_in_days:  If given, sets expiration_date this many days from now.

        Returns:
            The newly created APIToken instance (already saved).
        """
        expiration_date = None
        if expires_in_days is not None:
            expiration_date = timezone.now() + timedelta(days=expires_in_days)

        return cls.objects.create(
            user=user,
            token_name=token_name,
            allowed_models=allowed_models or [],
            expiration_date=expiration_date,
        )
