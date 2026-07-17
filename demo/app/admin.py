"""
demo/admin.py

Auto-registers all demo SnapModel subclasses with the Django admin.

The registration uses SnapModel.register_all_admins() which introspects
each model's field-level SnapAdmin attributes to build a full-featured
ModelAdmin without any boilerplate.
"""

from snapadmin.models import SnapModel

SnapModel.register_all_admins()
