# demo/models.py

from django.utils.translation import gettext_lazy as _
from django.db import models as django_models
from snapadmin import fields as snap_fields, models as snap_models
from snapadmin import validators


class Product(snap_models.SnapModel):
    name = snap_fields.SnapCharField(
        max_length=200,
        verbose_name=_("Name"),
        searchable=True
    )
    full_info2 = snap_fields.SnapFunctionField(
        func=lambda obj: f"{obj.name} — {obj.price}$",
        verbose_name=_("Full Info1"),
        show_in_list=True
    )
    price = snap_fields.SnapDecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Price")
    )
    available = snap_fields.SnapBooleanField(
        default=True, verbose_name=_("Available")
    )
    full_info1 = snap_fields.SnapFunctionField(
        func=lambda obj: f"{obj.name} — {obj.price}$",
        verbose_name=_("Full Info"),
        show_in_list=True
    )

    # ES Integration: Main database and duplication in elasticsearch
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

    first_name = snap_fields.SnapCharField(
        max_length=100,
        verbose_name=_("First Name"),
    )
    last_name = snap_fields.SnapCharField(
        max_length=100,
        verbose_name=_("Last Name")
    )
    origin = snap_fields.SnapCharField(
        max_length=100,
        verbose_name=_("Origin"),
        choices=[
            ('status_a', 'Status A'),
            ('status_b', 'Status B'),
            ('status_c', 'Status C')
        ],
        show_in_list=False,
        filterable=True
    )
    origin_display = snap_fields.SnapStatusBadgeField(
        field_name='origin',
        verbose_name=_("Origin 1"),
        choices=[
            snap_fields.SnapStatusBadgeFieldChoice(
                'status_a',
                text_html_color='#721C24',
                background_html_color='#F8D7DA',
                border_html_color='#F5C6CB'
            ),
            snap_fields.SnapStatusBadgeFieldChoice(
                'status_b',
                text_html_color='#856404',
                background_html_color='#FFF3CD',
                border_html_color='#FFEEBA'
            ),
            snap_fields.SnapStatusBadgeFieldChoice(
                'status_c',
                text_html_color='#155724',
                background_html_color='#D4EDDA',
                border_html_color='#C3E6CB'
            ),
        ],
        style_arguments={}
    )
    email = snap_fields.SnapEmailField(
        show_in_list=False,
        max_length=200,
        verbose_name=_("Email")
    )
    active = snap_fields.SnapBooleanField(
        default=True,
        verbose_name=_("Is Active")
    )

    class Meta:
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")


class Order(snap_models.SnapModel):
    customer = snap_fields.SnapForeignKey(
        to=Customer,
        on_delete=django_models.PROTECT,
        verbose_name=_("Customer"),
        autocomplete=True,
        show_in_list=True,
        editable=True,  # Read-only: set at creation time only
        updatable=False,
        show_in_form=True
    )
    total = snap_fields.SnapDecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Total"),
        filterable=True,
        editable=False,
        updatable=False,
        show_in_list=False,
        show_in_form=True
    )
    # NOT NULL constraint failed: app_order.total

    config = snap_fields.SnapFileField(
        verbose_name=_("Configuration"),
        upload_to="configs/",
        allowed_extensions=[validators.FileExtensionEnum.JSON],
        allowed_encodings=[validators.FileEncodingEnum.UTF8],
        max_size_bytes=512 * 1024,  # 512 KB
        show_in_form=False
    )

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
