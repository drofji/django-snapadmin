from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

try:
    from django.conf import settings
    if 'unfold' not in settings.INSTALLED_APPS:
        raise ImportError("Unfold not in INSTALLED_APPS")  # pragma: no cover

    from unfold.admin import ModelAdmin, TabularInline, StackedInline
    from unfold.contrib.filters.admin import RelatedDropdownFilter, ChoicesDropdownFilter
    from unfold.decorators import display
    UNFOLD_INSTALLED = True
except (ImportError, RuntimeError):  # pragma: no cover
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

from snapadmin.models import APIToken, ErrorEvent, SnapadminAuditLog
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
    search_fields = ["token_name", f"user__{get_user_model().USERNAME_FIELD}"]
    readonly_fields = ["full_key", "created_at", "last_used_at"]
    ordering = ["-created_at"]

    warn_unsaved_form = True
    list_filter_submit = True

    fieldsets = [
        (None, {
            "fields": ["token_name", "user", "full_key"],
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

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # The raw key exists only on the instance that just minted it. Surface it
        # once here, since it can never be retrieved from storage again.
        if not change and obj.token_key:
            self.message_user(
                request,
                _("API token created. Copy it now — it will not be shown again: %(key)s")
                % {"key": obj.token_key},
                level=messages.WARNING,
            )

    @display(description=_("Token Key"))
    def full_key(self, obj: APIToken):
        """Show the raw key once (right after creation) or the masked prefix."""
        if obj.token_key:
            return obj.token_key
        if obj.token_prefix:
            return f"{obj.token_prefix}•••••••• ({_('hidden — shown only once at creation')})"
        return "—"

    @display(description=_("Token Key"), header=True)
    def masked_key(self, obj: APIToken):
        """Show only the first 8 characters of the token key."""
        val = f"{obj.token_prefix}••••••••"
        if UNFOLD_INSTALLED:
            return [val, None, None]
        return val  # pragma: no cover

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

        return res[0]  # pragma: no cover


class ErrorEventAdmin(ModelAdmin):
    """
    Read-only admin for errors captured by SnapErrorMonitorMiddleware.
    Events are created by the middleware and purged by the digest task,
    so manual add/change is disabled; delete stays available for cleanup.
    """

    list_display = [
        "created_at",
        "exception_class",
        "path",
        "method",
        "status_badge",
        "short_message",
    ]
    # Free-text fields get the all-values filter (distinct recorded values);
    # a choices dropdown would be empty since these fields declare no choices.
    list_filter = ["exception_class", "method", "status_code"]
    search_fields = ["exception_class", "message", "path"]
    readonly_fields = [
        "exception_class", "message", "path", "method",
        "status_code", "fingerprint", "traceback", "created_at",
    ]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    list_filter_submit = True

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @display(description=_("Status"), label=True)
    def status_badge(self, obj: ErrorEvent):
        """Coloured pill: 5xx are danger, everything else warning."""
        res = (str(obj.status_code), "danger" if obj.status_code >= 500 else "warning")
        if UNFOLD_INSTALLED:
            return res
        return res[0]  # pragma: no cover

    @display(description=_("Message"))
    def short_message(self, obj: ErrorEvent):
        if len(obj.message) > 120:
            return f"{obj.message[:120]}…"
        return obj.message or "—"


class SnapadminAuditLogAdmin(ModelAdmin):
    """Fully read-only view of the immutable audit trail (issue #7).

    Add, change and delete are all disabled — the trail is append-only and must
    not be alterable from the admin. Rows are written by
    ``snapadmin.audit.record_audit`` and pruned by the retention purge.
    """

    list_display = ["timestamp", "action_badge", "actor_repr", "model", "object_repr", "ip_address"]
    list_filter = ["action", "app_label", "model"]
    search_fields = ["actor_repr", "object_repr", "object_id", "ip_address", "model"]
    readonly_fields = [
        "action", "actor", "actor_repr", "ip_address", "user_agent",
        "content_type", "app_label", "model", "object_id", "object_repr",
        "changes", "timestamp",
    ]
    ordering = ["-timestamp"]
    date_hierarchy = "timestamp"
    list_filter_submit = True

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Action"), label=True)
    def action_badge(self, obj: SnapadminAuditLog):
        colours = {"create": "success", "update": "info", "delete": "danger"}
        res = (obj.get_action_display(), colours.get(obj.action, "info"))
        if UNFOLD_INSTALLED:
            return res
        return res[0]  # pragma: no cover
