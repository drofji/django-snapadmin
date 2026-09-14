"""
tests/test_api_token_admin.py

``APITokenAdmin`` — the admin screen for API tokens.

Two things matter on that screen: the raw key is never shown again after
creation (storage keeps only a prefix and a digest — see
``test_api_token_hashing.py``), and a token's state is legible at a glance, so
an expired or disabled token cannot be mistaken for a working one. (#QA1b: these
tests came from a suite named for the coverage metric.)
"""

import pytest
from django.contrib import admin as django_admin

from snapadmin.admin import APITokenAdmin
from snapadmin.models import APIToken


@pytest.fixture
def token_admin():
    return APITokenAdmin(APIToken, django_admin.site)


@pytest.mark.django_db
class TestAllowedModelsWidget:
    def test_the_allowed_models_field_uses_the_model_picker(self, token_admin):
        from snapadmin.widgets import SmartModelSelectorWidget

        form_field = token_admin.formfield_for_dbfield(
            APIToken._meta.get_field("allowed_models"), request=None
        )

        assert isinstance(form_field.widget, SmartModelSelectorWidget)


@pytest.mark.django_db
class TestMaskedKey:
    def test_only_the_prefix_is_shown_beside_the_mask(self, token_admin, api_token):
        label, *rest = token_admin.masked_key(api_token)

        assert label == f"{api_token.token_prefix}••••••••"
        # Unfold's display contract is a 3-tuple; the other two slots are unused.
        assert rest == [None, None]
        assert api_token.token_key not in label


@pytest.mark.django_db
class TestStatusBadge:
    def test_a_valid_token_reads_as_active(self, token_admin, api_token):
        label, state = token_admin.status_badge(api_token)

        assert str(label) == "Active"
        assert state == "success"

    def test_a_deactivated_token_reads_as_disabled(self, token_admin, inactive_token):
        label, state = token_admin.status_badge(inactive_token)

        assert str(label) == "Disabled"
        assert state == "danger"

    def test_an_expired_token_reads_as_expired(self, token_admin, expired_token):
        label, state = token_admin.status_badge(expired_token)

        assert str(label) == "Expired"
        assert state == "warning"
