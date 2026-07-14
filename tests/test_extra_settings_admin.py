"""
tests/test_extra_settings_admin.py

Tests for the optional django-extra-settings Unfold restyling
(``snapadmin.extra_settings_admin.apply_unfold_styling``).

The test settings install ``extra_settings`` (after ``snapadmin``), so by the
time pytest-django has booted Django, ``SnapAdminConfig.ready()`` has already
run ``apply_unfold_styling()`` and the registered ``Setting`` admin is expected
to be Unfold-styled — this exercises the real end state, not just the unit.
"""

import sys

import pytest
from django.contrib import admin

from extra_settings.admin import SettingAdmin
from extra_settings.models import Setting
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from snapadmin.extra_settings_admin import apply_unfold_styling


def _registered_setting_models():
    """Every registered model that is ``Setting`` or a proxy subclass of it."""
    return [model for model in admin.site._registry if issubclass(model, Setting)]


def test_ready_applied_unfold_styling():
    """App loading (SnapAdminConfig.ready) has already Unfold-styled Setting."""
    models = _registered_setting_models()
    assert models, "extra_settings should have registered a Setting admin"
    for model in models:
        assert isinstance(admin.site._registry[model], UnfoldModelAdmin)


def test_apply_is_idempotent_when_already_styled():
    """A second call is a no-op once every Setting admin is already styled."""
    assert apply_unfold_styling() is False


@pytest.fixture
def plain_setting_admin():
    """Temporarily downgrade the Setting admin to a plain, unstyled one.

    Restores the original registration on teardown so the global admin registry
    is left exactly as ``ready()`` set it up for other tests.
    """
    model = _registered_setting_models()[0]
    original_class = type(admin.site._registry[model])

    admin.site.unregister(model)
    admin.site.register(model, SettingAdmin)
    try:
        yield model
    finally:
        admin.site.unregister(model)
        admin.site.register(model, original_class)


def test_apply_restyles_a_plain_admin(plain_setting_admin):
    model = plain_setting_admin
    # Precondition: the admin is currently plain (not Unfold-derived).
    assert not isinstance(admin.site._registry[model], UnfoldModelAdmin)

    assert apply_unfold_styling() is True

    styled = admin.site._registry[model]
    assert isinstance(styled, UnfoldModelAdmin)
    # extra_settings' own configuration is preserved through the composition.
    assert issubclass(type(styled), SettingAdmin)
    assert styled.search_fields == SettingAdmin.search_fields

    # And it's idempotent: a follow-up call finds nothing left to restyle.
    assert apply_unfold_styling() is False


def test_returns_false_when_extra_settings_missing(monkeypatch):
    """No hard dependency: a missing extra_settings is handled gracefully."""
    monkeypatch.setitem(sys.modules, "extra_settings.models", None)
    assert apply_unfold_styling() is False
