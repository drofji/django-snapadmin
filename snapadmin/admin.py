from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

try:
    from django.conf import settings
    if 'unfold' not in settings.INSTALLED_APPS:
        raise ImportError("Unfold not in INSTALLED_APPS")

    from unfold.admin import ModelAdmin, TabularInline, StackedInline
    from unfold.contrib.filters.admin import RelatedDropdownFilter, ChoicesDropdownFilter
    from unfold.decorators import display
    UNFOLD_INSTALLED = True
except (ImportError, RuntimeError):
    from django.contrib.admin import ModelAdmin, TabularInline, StackedInline
    RelatedDropdownFilter = admin.RelatedFieldListFilter
    ChoicesDropdownFilter = admin.ChoicesFieldListFilter
    UNFOLD_INSTALLED = False

    def display(description=None, header=False, label=False, **kwargs):
        def decorator(func):
            if description:
                func.short_description = description
            return func
        return decorator

from snapadmin.models import APIToken
from snapadmin.widgets import SmartModelSelectorWidget


class SnapTabularInline(TabularInline):
    """
    Standard inline class for SnapAdmin. Fallback to Django admin if Unfold is missing.
    """
    extra = 1


class SnapStackedInline(StackedInline):
    """
    Standard stacked inline class for SnapAdmin. Fallback to Django admin if Unfold is missing.
    """
    extra = 1


class APITokenAdmin(ModelAdmin):
    """
    Admin interface for managing API tokens using Unfold.
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
    list_filter  = [
        ("is_active", ChoicesDropdownFilter),
        ("user", RelatedDropdownFilter),
    ]
    search_fields = ["token_name", "user__username"]
    readonly_fields = ["token_key", "created_at", "last_used_at"]
    ordering = ["-created_at"]

    warn_unsaved_form = True
    list_filter_submit = True

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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "allowed_models":
            kwargs["widget"] = SmartModelSelectorWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @display(description=_("Token Key"), header=True)
    def masked_key(self, obj: APIToken):
        """Show only the first 8 characters of the token key."""
        val = f"{obj.token_key[:8]}••••••••"
        if UNFOLD_INSTALLED:
            return [val, None, None]
        return val

    @display(description=_("Status"), label=True)
    def status_badge(self, obj: APIToken):
        """Render a coloured pill badge reflecting the token's current state."""
        if not obj.is_active:
            res = (_("Disabled"), "danger")
        elif obj.is_expired:
            res = (_("Expired"), "warning")
        else:
            res = (_("Active"), "success")

        if UNFOLD_INSTALLED:
            return res

        # Fallback for standard admin: just return the label
        return res[0]
