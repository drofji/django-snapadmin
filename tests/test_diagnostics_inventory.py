"""Tests for the ``snapadmin_info`` models & security collector (#CLI1e)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.test.utils import isolate_apps
from django.utils import timezone

from snapadmin.diagnostics import get_collector
from snapadmin.diagnostics import inventory as inventory_collector
from snapadmin.models import APIToken, SnapModel


def _collect():
    return get_collector("inventory").collect(verbose=False)


class TestModelInventory:
    def test_lists_models_with_flags(self):
        data = _collect()
        assert data["models"]["total"] > 0
        item = data["models"]["items"][0]
        assert set(item) == {
            "model", "door", "inactive_capabilities", "es_mode", "retention_days",
            "write_restricted", "masked",
        }

    def test_write_restricted_flag(self):
        # demo.AuditLog sets api_write_fields = ["action"] (#SEC2).
        items = _collect()["models"]["items"]
        assert any(item["write_restricted"] for item in items)

    @override_settings(SNAPADMIN_MASKED_FIELDS=["name"])
    def test_masked_fields_reflected(self):
        data = _collect()
        assert data["masked_fields"] == 1
        assert any(item["masked"] for item in data["models"]["items"])


class TestRegistrationDoor:
    """#PAR1e: per-model "which door did this come through" + the #RFC1g gap list.

    ``register_admin``/``purge_expired``/``_ensure_es_index_and_mapping`` are defined directly on
    ``SnapModel`` (never attached to a decorated plain model today), so their presence is the
    ground-truth marker — the same one ``features.py``'s ``decorated_models`` capability already
    uses, not an ``issubclass`` guess.
    """

    def test_snapmodel_subclass_has_no_gaps(self):
        # Every demo model is a SnapModel subclass today (#DOC8/#RFC1g haven't shipped a decorated
        # demo model yet) — any item already proves the subclass door reports clean.
        item = _collect()["models"]["items"][0]
        assert item["door"] == "subclass"
        assert item["inactive_capabilities"] == ""

    def test_decorator_registered_model_reports_the_door_and_the_rfc1g_gaps(self, monkeypatch):
        from django.db import models as django_models

        from snapadmin.models import snap_model

        with isolate_apps("snapadmin"):
            @snap_model()
            class Ledger(django_models.Model):
                class Meta:
                    app_label = "snapadmin"

        monkeypatch.setattr(inventory_collector, "_registered_models", lambda: [Ledger])
        item = _collect()["models"]["items"][0]
        assert item["door"] == "decorator"
        assert item["inactive_capabilities"] == "elasticsearch, generated_admin, retention_purge"

    def test_capability_markers_still_exist_on_snapmodel(self):
        """Drift guard: if one of these is ever renamed, the gap detection silently breaks."""
        for _capability, marker in inventory_collector._DOOR_CAPABILITY_MARKERS:
            assert hasattr(SnapModel, marker)

    def test_inactive_capabilities_narrows_once_a_marker_is_attached(self, monkeypatch):
        """The check is attribute-driven, not door-hardcoded — it self-corrects the day #RFC1g1-3
        attach real machinery (e.g. ``purge_expired``) onto a decorated model, with no further
        change needed here."""
        from django.db import models as django_models

        from snapadmin.models import snap_model

        with isolate_apps("snapadmin"):
            @snap_model()
            class Invoice(django_models.Model):
                class Meta:
                    app_label = "snapadmin"

        Invoice.purge_expired = classmethod(lambda cls, **kwargs: 0)
        monkeypatch.setattr(inventory_collector, "_registered_models", lambda: [Invoice])
        item = _collect()["models"]["items"][0]
        assert item["door"] == "decorator"
        assert item["inactive_capabilities"] == "elasticsearch, generated_admin"


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
