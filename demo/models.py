# demo/models.py

from django.utils.translation import gettext_lazy as _
from django.db import models as django_models
from snapadmin import fields as snap_fields, models as snap_models
from snapadmin import validators
from snapadmin.admin import SnapTabularInline, SnapStackedInline
import uuid

class Category(snap_models.SnapModel):
    name = snap_fields.SnapCharField(max_length=100, verbose_name=_("Name"), searchable=True, show_in_form=True)

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")

class Tag(snap_models.SnapModel):
    name = snap_fields.SnapCharField(max_length=50, verbose_name=_("Tag Name"), searchable=True, show_in_form=True)

    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")

class Product(snap_models.SnapModel):
    category = snap_fields.SnapForeignKey(Category, on_delete=django_models.SET_NULL, null=True, blank=True, verbose_name=_("Category"), show_in_form=True, filterable=True)
    tags = snap_fields.SnapManyToManyField(Tag, blank=True, verbose_name=_("Tags"), show_in_form=True)
    name = snap_fields.SnapCharField(max_length=200, verbose_name=_("Name"), searchable=True, show_in_form=True)
    price = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price"), show_in_form=True, filterable=True)
    available = snap_fields.SnapBooleanField(default=True, verbose_name=_("Available"), show_in_form=True, filterable=True)
    description = snap_fields.SnapTextField(verbose_name=_("Description"), wysiwyg=True, show_in_form=True)

    # Unfold features
    compressed_fields = True
    warn_unsaved_form = True
    admin_tabs = [
        {"title": _("General"), "link": "#"},
        {"title": _("Advanced"), "link": "#"},
    ]

    es_index_enabled = True
    es_mapping = {
        "name": {"type": "text", "analyzer": "standard"},
        "price": {"type": "float"},
        "available": {"type": "boolean"},
    }

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")

class Customer(snap_models.SnapModel):
    first_name = snap_fields.SnapCharField(max_length=100, verbose_name=_("First Name"), show_in_form=True)
    last_name = snap_fields.SnapCharField(max_length=100, verbose_name=_("Last Name"), show_in_form=True)
    origin = snap_fields.SnapCharField(
        max_length=100,
        verbose_name=_("Origin"),
        choices=[('status_a', 'Status A'), ('status_b', 'Status B'), ('status_c', 'Status C')],
        show_in_list=False,
        filterable=True
    )
    email = snap_fields.SnapEmailField(max_length=200, verbose_name=_("Email"), show_in_form=True)
    active = snap_fields.SnapBooleanField(default=True, verbose_name=_("Is Active"), show_in_form=True, filterable=True)

    class Meta:
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")

class Order(snap_models.SnapModel):
    customer = snap_fields.SnapForeignKey(Customer, on_delete=django_models.PROTECT, verbose_name=_("Customer"), autocomplete=True, show_in_list=True, show_in_form=True)
    total = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Total"), show_in_form=True, filterable=True)
    created_at = snap_fields.SnapDateTimeField(auto_now_add=True, verbose_name=_("Created At"))

    snap_inlines = []

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")

class OrderItem(snap_models.SnapModel):
    order = snap_fields.SnapForeignKey(Order, on_delete=django_models.CASCADE, related_name="items", verbose_name=_("Order"))
    product = snap_fields.SnapForeignKey(Product, on_delete=django_models.CASCADE, verbose_name=_("Product"), show_in_form=True)
    quantity = snap_fields.SnapPositiveIntegerField(default=1, verbose_name=_("Quantity"), show_in_form=True)
    price = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price at purchase"), show_in_form=True)

    class Meta:
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")

class OrderItemInline(SnapTabularInline):
    model = OrderItem
    extra = 1

Order.snap_inlines = [OrderItemInline]

# ===========================================================================
# Showcase Model - All possible fields with Tabs
# ===========================================================================

class Showcase(snap_models.SnapModel):
    # Tab: Text Content
    char_field = snap_fields.SnapCharField(max_length=100, verbose_name=_("Char Field"), show_in_form=True, searchable=True, tab=_("Text Content"))
    text_field = snap_fields.SnapTextField(verbose_name=_("Text Field"), show_in_form=True, tab=_("Text Content"))
    wysiwyg_field = snap_fields.SnapTextField(verbose_name=_("WYSIWYG Field"), wysiwyg=True, show_in_form=True, tab=_("Text Content"))

    # Tab: Numeric Data
    integer_field = snap_fields.SnapIntegerField(verbose_name=_("Integer Field"), show_in_form=True, filterable=True, tab=_("Numbers"))
    positive_integer = snap_fields.SnapPositiveIntegerField(verbose_name=_("Positive Int"), show_in_form=True, tab=_("Numbers"))
    float_field = snap_fields.SnapFloatField(verbose_name=_("Float Field"), show_in_form=True, tab=_("Numbers"))
    decimal_field = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Decimal Field"), show_in_form=True, tab=_("Numbers"))
    big_int = snap_fields.SnapBigIntegerField(verbose_name=_("Big Int"), show_in_form=True, tab=_("Numbers"))

    # Tab: Temporal
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

    # Unfold specific settings
    compressed_fields = True
    warn_unsaved_form = True

    class Meta:
        verbose_name = _("Showcase")
        verbose_name_plural = _("Showcase")
