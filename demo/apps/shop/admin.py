"""
demo/admin.py

Auto-registers all demo SnapModel subclasses with the Django admin.

The registration uses SnapModel.register_all_admins() which introspects
each model's field-level SnapAdmin attributes to build a full-featured
ModelAdmin without any boilerplate.
"""

from django.contrib import admin

from snapadmin.models import SnapModel

from .models import LegacyStockLevel

SnapModel.register_all_admins()


# register_all_admins() only reaches SnapModel subclasses. A model opted in with
# @snap_model stays a plain Django model with plain fields, so there is nothing for
# the generator to derive a ModelAdmin from — it is registered by hand, exactly as
# the decorator's docstring says. Everything else the decorator configured (the REST
# write allowlist, ?search=, the field-level guard on reorder_cost) is already active
# without this; only the admin needs the extra line.
@admin.register(LegacyStockLevel)
class LegacyStockLevelAdmin(admin.ModelAdmin):
    list_display = ("sku", "warehouse", "on_hand", "counted_at")
    search_fields = ("sku", "warehouse")
    list_filter = ("warehouse",)
