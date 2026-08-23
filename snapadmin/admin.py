"""
Admin base classes and registration for SnapAdmin.

Resolves the admin ``ModelAdmin``/inline/widget base lazily: django-unfold's themed
classes when ``unfold`` is installed *and* in ``INSTALLED_APPS`` (``UNFOLD_INSTALLED``
is then ``True``), otherwise Django's built-in admin — so the package renders either
way. Also registers the package's own models (``APIToken``, ``ErrorEvent``) and exposes
the public inline bases (``SnapTabularInline``/``SnapStackedInline``). The auto
generated per-model ``ModelAdmin`` is built by ``register_admin`` /
``register_all_admins`` in :mod:`snapadmin.models`; this module owns the shared base
classes those build on.
"""

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
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

from snapadmin import audit
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

        return res[0]


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
        return res[0]

    @display(description=_("Message"))
    def short_message(self, obj: ErrorEvent):
        if len(obj.message) > 120:
            return f"{obj.message[:120]}…"
        return obj.message or "—"


class SnapadminAuditLogAdmin(ModelAdmin):
    """Fully read-only view of the immutable audit trail.

    Add, change and delete are all disabled — the trail is append-only and must
    not be alterable from the admin. Rows are written by
    ``snapadmin.audit.record_audit`` and pruned by the retention purge.

    The stored diff (``{field: {"old": …, "new": …}}``) is rendered as a
    field-level table rather than raw JSON, and every object links to
    :meth:`timeline_view` — its full change history on one page, newest first.
    Both are masked through ``snapadmin.masking.mask_changes``, so reading the
    trail never becomes a way around ``SNAPADMIN_MASKED_FIELDS``.
    """

    #: Entries rendered on one timeline page. The view is a diff reader, not a
    #: changelist: an object with years of history would otherwise render
    #: thousands of tables into a single response. The full history stays
    #: available through ``manage.py snapadmin_audit_export``.
    timeline_max_entries = 100

    list_display = ["timestamp", "action_badge", "actor_repr", "model", "object_timeline", "ip_address"]
    list_filter = ["action", "app_label", "model"]
    search_fields = ["actor_repr", "object_repr", "object_id", "ip_address", "model"]
    readonly_fields = [
        "action", "actor", "actor_repr", "ip_address", "user_agent",
        "content_type", "app_label", "model", "object_id", "object_repr",
        "changes", "timeline_link", "timestamp",
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

    def get_urls(self):
        # The per-object timeline is mounted *before* the default admin URLs:
        # those end in a catch-all "<path:object_id>/" that would otherwise
        # swallow "timeline/…" and 404 on a missing audit row.
        custom = [
            path(
                "timeline/<str:app_label>/<str:model>/<path:object_id>/",
                self.admin_site.admin_view(self.timeline_view),
                name="snapadmin_snapadminauditlog_timeline",
            ),
        ]
        return custom + super().get_urls()

    def timeline_view(self, request, app_label: str, model: str, object_id: str) -> TemplateResponse:
        """Render every recorded change to one object as a diff timeline.

        Gated on the audit log's own view permission — the same thing that
        gates the changelist, which already exposes these rows — and masked per
        viewer, so the timeline can never reveal more than the list it is
        reached from.
        """
        if not self.has_view_permission(request):
            raise PermissionDenied

        entries = SnapadminAuditLog.objects.filter(
            app_label=app_label, model=model, object_id=object_id,
        ).order_by("-timestamp")
        total = entries.count()
        items = [
            {"entry": entry, "rows": audit.diff_rows(self._visible_changes(entry, request.user))}
            for entry in entries[:self.timeline_max_entries]
        ]
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Audit timeline"),
            "target": f"{app_label}.{model} #{object_id}",
            "target_repr": items[0]["entry"].object_repr if items else "",
            "entries": items,
            "total": total,
            "limit": self.timeline_max_entries,
            "truncated": total > len(items),
            "changelist_url": reverse("admin:snapadmin_snapadminauditlog_changelist"),
        }
        return TemplateResponse(request, "snapadmin/audit_timeline.html", context)

    @staticmethod
    def timeline_url(obj: SnapadminAuditLog) -> str | None:
        """Admin URL of ``obj``'s timeline, or ``None`` if it names no object.

        A row whose app/model/id snapshot is blank (a legacy or hand-written
        entry) has nothing to link to.
        """
        if not (obj.app_label and obj.model and obj.object_id):
            return None
        return reverse(
            "admin:snapadmin_snapadminauditlog_timeline",
            args=[obj.app_label, obj.model, obj.object_id],
        )

    @staticmethod
    def _visible_changes(obj: SnapadminAuditLog, user=None) -> dict | None:
        """``obj.changes`` as ``user`` may see it — masked field by field.

        ``mask_changes`` returns the raw value for a viewer who may see raw PII
        (globally, or for one field through a rule's ``permission``), so this is
        the single call both the change form and the timeline go through.
        """
        from snapadmin.masking import mask_changes
        return mask_changes(obj.app_label, obj.model, obj.changes, user)

    @staticmethod
    def _render_diff(changes: dict | None) -> str:
        """Render a ``changes`` diff as the field-level table.

        Every value is escaped by the template — an audit row holds whatever was
        typed into the admin, so it is markup only after Django has escaped it.
        """
        return mark_safe(render_to_string(
            "snapadmin/audit_diff.html", {"rows": audit.diff_rows(changes)},
        ))

    def get_exclude(self, request, obj=None):
        # Keep the raw "changes" JSONField out of the change form entirely: it
        # is rendered as a diff table by get_readonly_fields instead. Left in,
        # it is no longer covered by readonly_fields (the swap below renames
        # it), so Django puts it in the form — and a view-only user gets every
        # field rendered read-only from the model, printing the *unmasked* diff
        # next to the masked one.
        return [*(super().get_exclude(request, obj) or []), "changes"]

    def get_readonly_fields(self, request, obj=None):
        # Swap the raw "changes" field for a rendered diff — masked unless the
        # viewer holds PII access, since the diff snapshot would otherwise leak
        # a masked field's raw value to anyone who can read the audit trail.
        from snapadmin.masking import user_can_view_pii

        fields = list(super().get_readonly_fields(request, obj))
        rendered = "changes_diff" if user_can_view_pii(request.user) else "masked_changes"
        return [rendered if f == "changes" else f for f in fields]

    @display(description=_("Changes"))
    def changes_diff(self, obj: SnapadminAuditLog):
        """The raw diff, rendered as a table (viewers with PII access)."""
        return self._render_diff(obj.changes)

    @display(description=_("Changes"))
    def masked_changes(self, obj: SnapadminAuditLog):
        """The diff with configured PII masked, rendered as a table.

        Masks with no viewer in hand — a display method sees only the object —
        so every masked field is masked here. A rule's per-field ``permission``
        widens access only on the surfaces that resolve the viewer per request
        (the timeline, the changelist, the API, exports); this one stays as
        strict as it has always been.
        """
        return self._render_diff(self._visible_changes(obj))

    @display(description=_("Timeline"))
    def timeline_link(self, obj: SnapadminAuditLog):
        """Link from one entry to the full history of the object it touched."""
        url = self.timeline_url(obj)
        if url is None:
            return "—"
        return format_html('<a href="{}">{}</a>', url, _("View the full change timeline"))

    @display(description=_("Object"))
    def object_timeline(self, obj: SnapadminAuditLog):
        """The object column, linked to that object's diff timeline."""
        label = obj.object_repr or obj.object_id or "—"
        url = self.timeline_url(obj)
        if url is None:
            return label
        return format_html('<a href="{}">{}</a>', url, label)

    @display(description=_("Action"), label=True)
    def action_badge(self, obj: SnapadminAuditLog):
        colours = {"create": "success", "update": "info", "delete": "danger"}
        res = (obj.get_action_display(), colours.get(obj.action, "info"))
        if UNFOLD_INSTALLED:
            return res
        return res[0]
