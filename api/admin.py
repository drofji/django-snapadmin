"""
api/admin.py

Django Admin registration for the APIToken model.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from api.models import APIToken


@admin.register(APIToken)
class APITokenAdmin(admin.ModelAdmin):
    """
    Admin interface for managing API tokens.

    Security note: the token_key is shown masked in the list view.
    It is only fully visible in the detail/change view.
    """

    list_display = [
        "token_name",
        "user",
        "masked_key",
        "expiration_date",
        "is_active",
        "status_badge",
        "last_used_at",
        "created_at",
    ]
    list_filter  = ["is_active", "user"]
    search_fields = ["token_name", "user__username"]
    readonly_fields = ["token_key", "created_at", "last_used_at"]
    ordering = ["-created_at"]

    fieldsets = [
        (None, {
            "fields": ["token_name", "user", "token_key"],
        }),
        (_("Access Control"), {
            "fields": ["is_active", "expiration_date", "allowed_models"],
        }),
        (_("Audit"), {
            "fields": ["created_at", "last_used_at"],
            "classes": ["collapse"],
        }),
    ]

    @admin.display(description=_("Token Key"))
    def masked_key(self, obj: APIToken) -> str:
        """Show only the first 8 characters of the token key."""
        return f"{obj.token_key[:8]}••••••••"

    @admin.display(description=_("Status"))
    def status_badge(self, obj: APIToken) -> str:
        """Render a coloured pill badge reflecting the token's current state."""
        if not obj.is_active:
            return format_html(
                '<span style="background:#dc3545;color:white;padding:2px 8px;border-radius:12px;">Disabled</span>'
            )
        if obj.is_expired:
            return format_html(
                '<span style="background:#fd7e14;color:white;padding:2px 8px;border-radius:12px;">Expired</span>'
            )
        return format_html(
            '<span style="background:#28a745;color:white;padding:2px 8px;border-radius:12px;">Active</span>'
        )
