"""Tests for the ``snapadmin_info`` models & security collector (#CLI1e)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from snapadmin.diagnostics import get_collector
from snapadmin.models import APIToken


def _collect():
    return get_collector("inventory").collect(verbose=False)


class TestModelInventory:
    def test_lists_models_with_flags(self):
        data = _collect()
        assert data["models"]["total"] > 0
        item = data["models"]["items"][0]
        assert set(item) == {"model", "es_mode", "retention_days", "write_restricted", "masked"}

    def test_write_restricted_flag(self):
        # demo.AuditLog sets api_write_fields = ["action"] (#SEC2).
        items = _collect()["models"]["items"]
        assert any(item["write_restricted"] for item in items)

    @override_settings(SNAPADMIN_MASKED_FIELDS=["name"])
    def test_masked_fields_reflected(self):
        data = _collect()
        assert data["masked_fields"] == 1
        assert any(item["masked"] for item in data["models"]["items"])


class TestTokenCounts:
    def test_tokens_omitted_without_db(self):
        # No django_db marker → DB access is blocked → token counts are skipped, not fatal.
        assert "tokens" not in _collect()

    @pytest.mark.django_db
    def test_token_counts(self, admin_user):
        now = timezone.now()
        APIToken.objects.create(token_name="active", user=admin_user)
        APIToken.objects.create(
            token_name="expired", user=admin_user, expiration_date=now - timedelta(days=1)
        )
        data = _collect()
        assert data["tokens"]["total"] == 2
        assert data["tokens"]["active"] == 1
        assert data["tokens"]["expired"] == 1
