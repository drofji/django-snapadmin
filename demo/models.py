# demo/models.py

from django.utils.translation import gettext_lazy as _
from django.db import models as django_models
from snapadmin import fields as snap_fields, models as snap_models
from snapadmin import validators
from snapadmin.admin import SnapTabularInline, SnapStackedInline
import uuid

class Category(snap_models.SnapModel):
    # searchable=True → adds "name" to admin search box
    name = snap_fields.SnapCharField(max_length=100, verbose_name=_("Name"), searchable=True, show_in_form=True)
    # row="basic" → groups slug + is_active into a single horizontal row in the form
    slug = snap_fields.SnapSlugField(verbose_name=_("Slug"), show_in_form=True, row="basic")
    is_active = snap_fields.SnapBooleanField(default=True, verbose_name=_("Active"), show_in_form=True, row="basic")

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")

class Tag(snap_models.SnapModel):
    name = snap_fields.SnapCharField(max_length=50, verbose_name=_("Tag Name"), searchable=True, show_in_form=True)

    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")

class Product(snap_models.SnapModel):
    # filterable=True → adds a sidebar filter for this FK; autocomplete uses Django's search
    category = snap_fields.SnapForeignKey(Category, on_delete=django_models.SET_NULL, null=True, blank=True, verbose_name=_("Category"), show_in_form=True, filterable=True)
    tags = snap_fields.SnapManyToManyField(Tag, blank=True, verbose_name=_("Tags"), show_in_form=True)
    # searchable=True → full-text search on this field in the admin list view
    name = snap_fields.SnapCharField(max_length=200, verbose_name=_("Name"), searchable=True, show_in_form=True)
    # filterable=True on a DecimalField → generates a numeric range filter in the sidebar
    # row="pricing" → price + available appear side-by-side in the form
    price = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price"), show_in_form=True, filterable=True, row="pricing")
    available = snap_fields.SnapBooleanField(default=True, verbose_name=_("Available"), show_in_form=True, filterable=True, row="pricing")

    # SnapStatusBadgeField renders a color-coded pill badge in the list view
    # True → green badge, False → red badge (no extra template needed)
    status_badge = snap_fields.SnapStatusBadgeField(
        field_name="available",
        verbose_name=_("In Stock"),
        choices=[
            snap_fields.SnapStatusBadgeFieldChoice(True, "#065F46", "#D1FAE5", "#10B981"),
            snap_fields.SnapStatusBadgeFieldChoice(False, "#991B1B", "#FEE2E2", "#EF4444"),
        ]
    )
    # wysiwyg=True → renders a rich-text editor (TinyMCE/Quill) in the admin form
    description = snap_fields.SnapTextField(verbose_name=_("Description"), wysiwyg=True, show_in_form=True)

    # Unfold: compress empty tab panels into collapsible sections
    compressed_fields = True
    # Unfold: show a browser alert when the user tries to leave with unsaved changes
    warn_unsaved_form = True
    admin_tabs = [
        {"title": _("General"), "link": "#"},
        {"title": _("Advanced"), "link": "#"},
    ]

    # es_storage_mode = DUAL → writes to both PostgreSQL and Elasticsearch on save
    # Use DUAL when you need fast full-text search but also want DB reliability
    es_index_enabled = True
    es_storage_mode = snap_models.EsStorageMode.DUAL
    es_mapping = {
        "name": {"type": "text", "analyzer": "standard"},
        "price": {"type": "float"},
        "available": {"type": "boolean"},
    }

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")

class Customer(snap_models.SnapModel):
    # row="name" → first_name + last_name appear side-by-side
    first_name = snap_fields.SnapCharField(max_length=100, verbose_name=_("First Name"), show_in_form=True, row="name")
    last_name = snap_fields.SnapCharField(max_length=100, verbose_name=_("Last Name"), show_in_form=True, row="name")
    origin = snap_fields.SnapCharField(
        max_length=100,
        verbose_name=_("Origin"),
        choices=[('status_a', 'Status A'), ('status_b', 'Status B'), ('status_c', 'Status C')],
        show_in_list=False,  # hide from list but still filterable via sidebar
        filterable=True,
    )
    email = snap_fields.SnapEmailField(max_length=200, verbose_name=_("Email"), show_in_form=True, row="status")
    active = snap_fields.SnapBooleanField(default=True, verbose_name=_("Is Active"), show_in_form=True, filterable=True, row="status")

    # Demonstrates status badges on a boolean with custom colors
    status_badge = snap_fields.SnapStatusBadgeField(
        field_name="active",
        verbose_name=_("Status"),
        choices=[
            snap_fields.SnapStatusBadgeFieldChoice(True, "#065F46", "#D1FAE5", "#10B981"),
            snap_fields.SnapStatusBadgeFieldChoice(False, "#991B1B", "#FEE2E2", "#EF4444"),
        ]
    )

    # offline_mode = True → the Customer list view is cached in IndexedDB so it stays
    # usable without a connection; a dynamic toast + saved-objects panel appear when the
    # backend becomes unreachable and queued changes sync automatically on reconnect.
    offline_mode = True
    # offline_cache_limit → prefetch only the 50 most-recent customers for offline view
    # (overrides the default of 100) to keep the IndexedDB snapshot small.
    offline_cache_limit = 50

    class Meta:
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")

class CustomerProfile(snap_models.SnapModel):
    """Demonstrates SnapOneToOneField — a one-to-one extension of Customer.

    A OneToOne models "exactly one related row" (here: one profile per customer).
    on_delete=CASCADE removes the profile when its customer is deleted.
    """
    customer = snap_fields.SnapOneToOneField(
        Customer, on_delete=django_models.CASCADE, related_name="profile",
        verbose_name=_("Customer"), autocomplete=True, show_in_list=True, show_in_form=True,
    )
    newsletter = snap_fields.SnapBooleanField(default=False, verbose_name=_("Newsletter Opt-in"), show_in_form=True, filterable=True)
    bio = snap_fields.SnapTextField(blank=True, verbose_name=_("Bio"), show_in_form=True)

    class Meta:
        verbose_name = _("Customer Profile")
        verbose_name_plural = _("Customer Profiles")

class Order(snap_models.SnapModel):
    # autocomplete=True → FK rendered as searchable autocomplete widget (requires search_fields on Customer)
    customer = snap_fields.SnapForeignKey(Customer, on_delete=django_models.PROTECT, verbose_name=_("Customer"), autocomplete=True, show_in_list=True, show_in_form=True)
    total = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Total"), show_in_form=True, filterable=True)
    created_at = snap_fields.SnapDateTimeField(auto_now_add=True, verbose_name=_("Created At"))

    snap_inlines = []  # populated below after OrderItemInline is defined

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")

class SearchLog(snap_models.SnapModel):
    """
    Demonstrates es_storage_mode = ES_ONLY.
    No database table — records live only in Elasticsearch.
    """
    query = snap_fields.SnapCharField(max_length=255, verbose_name=_("Query"), searchable=True)
    results_count = snap_fields.SnapIntegerField(verbose_name=_("Results Count"))
    timestamp = django_models.DateTimeField(auto_now_add=True, verbose_name=_("Timestamp"))

    # ES_ONLY → managed=False, no migration; data is written only to the ES index
    es_storage_mode = snap_models.EsStorageMode.ES_ONLY
    es_mapping = {
        "query": {"type": "text"},
        "results_count": {"type": "integer"},
        "timestamp": {"type": "date"},
    }

    class Meta:
        managed = False  # No DB table — required for ES_ONLY models
        verbose_name = _("Search Log")
        verbose_name_plural = _("Search Logs")


class AuditLog(snap_models.SnapModel):
    """
    Demonstrates GDPR data retention.
    Records older than data_retention_days are auto-deleted by the purge_expired_data
    Celery task. Run manually: python manage.py purge_expired_data --dry-run
    """
    action = snap_fields.SnapCharField(max_length=100, verbose_name=_("Action"), searchable=True, show_in_list=True, show_in_form=True)
    user_email = snap_fields.SnapEmailField(verbose_name=_("User Email"), show_in_list=True, show_in_form=True)
    created_at = snap_fields.SnapDateTimeField(auto_now_add=True, verbose_name=_("Created At"), filterable=True)

    # Auto-delete records older than 90 days via the purge_expired_data Celery task
    data_retention_days = 90
    data_retention_field = "created_at"  # the DateTimeField used to calculate record age

    class Meta:
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")

class OrderItem(snap_models.SnapModel):
    order = snap_fields.SnapForeignKey(Order, on_delete=django_models.CASCADE, related_name="items", verbose_name=_("Order"))
    product = snap_fields.SnapForeignKey(Product, on_delete=django_models.CASCADE, verbose_name=_("Product"), show_in_form=True)
    quantity = snap_fields.SnapPositiveIntegerField(default=1, verbose_name=_("Quantity"), show_in_form=True)
    price = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price at purchase"), show_in_form=True)

    class Meta:
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")

# SnapTabularInline renders OrderItems inside the Order change form as a compact table
class OrderItemInline(SnapTabularInline):
    model = OrderItem
    extra = 1

Order.snap_inlines = [OrderItemInline]

# ===========================================================================
# Showcase Model — every SnapField type in one place, grouped by Unfold tabs
# ===========================================================================

class Showcase(snap_models.SnapModel):
    # Tab: Text Content
    char_field = snap_fields.SnapCharField(max_length=100, verbose_name=_("Char Field"), show_in_form=True, searchable=True, tab=_("Text Content"))
    text_field = snap_fields.SnapTextField(verbose_name=_("Text Field"), show_in_form=True, tab=_("Text Content"))
    # wysiwyg=True enables the rich-text editor (same as SnapRichTextField shorthand)
    wysiwyg_field = snap_fields.SnapTextField(verbose_name=_("WYSIWYG Field"), wysiwyg=True, show_in_form=True, tab=_("Text Content"))

    # Tab: Numeric Data — filterable fields show numeric range filters in the sidebar
    integer_field = snap_fields.SnapIntegerField(verbose_name=_("Integer Field"), show_in_form=True, filterable=True, tab=_("Numbers"))
    positive_integer = snap_fields.SnapPositiveIntegerField(verbose_name=_("Positive Int"), show_in_form=True, tab=_("Numbers"))
    float_field = snap_fields.SnapFloatField(verbose_name=_("Float Field"), show_in_form=True, tab=_("Numbers"))
    decimal_field = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Decimal Field"), show_in_form=True, tab=_("Numbers"))
    big_int = snap_fields.SnapBigIntegerField(verbose_name=_("Big Int"), show_in_form=True, tab=_("Numbers"))

    # Tab: Temporal — filterable DateFields get date range filters
    date_field = snap_fields.SnapDateField(verbose_name=_("Date Field"), show_in_form=True, filterable=True, tab=_("Dates & Times"))
    datetime_field = snap_fields.SnapDateTimeField(verbose_name=_("DateTime Field"), show_in_form=True, filterable=True, tab=_("Dates & Times"))
    time_field = snap_fields.SnapTimeField(verbose_name=_("Time Field"), show_in_form=True, tab=_("Dates & Times"))
    duration_field = snap_fields.SnapDurationField(verbose_name=_("Duration Field"), show_in_form=True, tab=_("Dates & Times"))

    # Tab: Specialized
    email_field = snap_fields.SnapEmailField(verbose_name=_("Email Field"), show_in_form=True, tab=_("Specialized"))
    slug_field = snap_fields.SnapSlugField(verbose_name=_("Slug Field"), show_in_form=True, tab=_("Specialized"))
    url_field = snap_fields.SnapURLField(verbose_name=_("URL Field"), show_in_form=True, tab=_("Specialized"))
    uuid_field = snap_fields.SnapUUIDField(default=uuid.uuid4, verbose_name=_("UUID Field"), show_in_form=True, tab=_("Specialized"))
    ip_field = snap_fields.SnapGenericIPAddressField(verbose_name=_("IP Address"), show_in_form=True, tab=_("Specialized"))

    # Tab: Files & Misc
    image_field = snap_fields.SnapImageField(upload_to="showcase/images/", verbose_name=_("Image Field"), show_in_form=True, blank=True, null=True, tab=_("Assets"))
    file_field = snap_fields.SnapFileField(upload_to="showcase/files/", verbose_name=_("File Field"), show_in_form=True, blank=True, null=True, tab=_("Assets"))
    boolean_field = snap_fields.SnapBooleanField(default=False, verbose_name=_("Boolean Field"), show_in_form=True, filterable=True, tab=_("Assets"))
    json_field = snap_fields.SnapJSONField(default=dict, verbose_name=_("JSON Field"), show_in_form=True, tab=_("Assets"))

    # Tab: Extended Types — new field types added in v0.1.0a2
    # SnapRichTextField is SnapTextField with wysiwyg=True pre-applied
    rich_text_field = snap_fields.SnapRichTextField(verbose_name=_("Rich Text"), show_in_form=True, tab=_("Extended"))
    # SnapPhoneField validates E.164 and common national formats
    phone_field = snap_fields.SnapPhoneField(verbose_name=_("Phone Number"), show_in_form=True, tab=_("Extended"))
    # SnapColorField validates #RRGGBB and #RGB hex strings
    color_field = snap_fields.SnapColorField(default="#3B82F6", verbose_name=_("Color"), show_in_form=True, tab=_("Extended"))
    small_int_field = snap_fields.SnapSmallIntegerField(verbose_name=_("Small Integer"), show_in_form=True, tab=_("Extended"))
    pos_small_int_field = snap_fields.SnapPositiveSmallIntegerField(verbose_name=_("Pos. Small Integer"), show_in_form=True, tab=_("Extended"))
    pos_big_int_field = snap_fields.SnapPositiveBigIntegerField(verbose_name=_("Pos. Big Integer"), show_in_form=True, tab=_("Extended"))

    # SnapFunctionField — a computed, read-only column rendered from a callable.
    # It is NOT a database column (no migration), so it's perfect for derived or
    # aggregated display values. safe_html=False escapes the output.
    summary = snap_fields.SnapFunctionField(
        func=lambda obj: f"{obj.char_field or '—'} · int={obj.integer_field or 0}",
        verbose_name=_("Computed Summary"),
        show_in_list=True,
        show_in_form=False,
    )

    # Unfold: group fields into collapsible sections and warn on unsaved changes
    compressed_fields = True
    warn_unsaved_form = True

    class Meta:
        verbose_name = _("Showcase")
        verbose_name_plural = _("Showcase")
