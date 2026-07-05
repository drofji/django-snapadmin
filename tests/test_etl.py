"""
Tests for the generic ETL upsert helper (v0.1.0a6):

  snapadmin.etl.upsert_from_source — streamed bulk_create(update_conflicts=True)
  into a SnapModel, one bulk ES reindex at the end (never per-row).
"""

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from demo.models import ExchangeRate
from snapadmin.etl import upsert_from_source


def _rows(n, *, rate="1.0"):
    codes = ["USD", "GBP", "JPY", "CHF", "CAD", "AUD", "SEK", "NOK", "PLN", "CZK"]
    return [
        {"code": codes[i], "base": "EUR", "rate": Decimal(rate)}
        for i in range(n)
    ]


@pytest.mark.django_db
class TestUpsertFromSource:
    def test_inserts_all_rows(self):
        summary = upsert_from_source(
            ExchangeRate, _rows(5), unique_fields=["code"], batch_size=2
        )
        assert summary["processed"] == 5
        assert summary["batches"] == 3          # 2 + 2 + 1
        assert summary["reindex"] is None       # ES disabled in tests
        assert ExchangeRate.objects.count() == 5

    def test_upsert_updates_on_conflict(self):
        upsert_from_source(ExchangeRate, _rows(3, rate="1.0"), unique_fields=["code"])
        upsert_from_source(ExchangeRate, _rows(3, rate="2.5"), unique_fields=["code"])
        assert ExchangeRate.objects.count() == 3           # no duplicates
        assert ExchangeRate.objects.get(code="USD").rate == Decimal("2.5")

    def test_explicit_update_fields(self):
        upsert_from_source(ExchangeRate, _rows(2, rate="1.0"), unique_fields=["code"])
        # Only `rate` is refreshed; `base` left as-is even if it were to differ.
        upsert_from_source(
            ExchangeRate, _rows(2, rate="9.9"), unique_fields=["code"], update_fields=["rate"]
        )
        assert ExchangeRate.objects.get(code="USD").rate == Decimal("9.9")

    def test_streamed_generator_not_materialised(self):
        def gen():
            for code in ["USD", "GBP", "JPY", "CHF"]:
                yield {"code": code, "base": "EUR", "rate": Decimal("1.0")}

        summary = upsert_from_source(ExchangeRate, gen(), unique_fields=["code"], batch_size=2)
        assert summary["processed"] == 4

    def test_empty_source(self):
        summary = upsert_from_source(ExchangeRate, [], unique_fields=["code"])
        assert summary == {"processed": 0, "batches": 0, "reindex": None}

    def test_empty_unique_fields_rejected(self):
        with pytest.raises(ValueError, match="unique_fields"):
            upsert_from_source(ExchangeRate, _rows(1), unique_fields=[])

    def test_es_only_model_rejected(self):
        from demo.models import SearchLog

        with pytest.raises(ValueError, match="ES_ONLY"):
            upsert_from_source(SearchLog, _rows(1), unique_fields=["code"])

    def test_reindex_invoked_once_when_es_enabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            ExchangeRate, "es_reindex_all",
            classmethod(lambda cls, *, chunk_size=500: calls.append(chunk_size) or {"indexed": 2}),
        )
        with override_settings(ELASTICSEARCH_ENABLED=True):
            summary = upsert_from_source(
                ExchangeRate, _rows(2), unique_fields=["code"], batch_size=100
            )
        assert calls == [100]                     # one bulk reindex, not per row
        assert summary["reindex"] == {"indexed": 2}

    def test_reindex_skipped_when_reindex_false(self, monkeypatch):
        monkeypatch.setattr(
            ExchangeRate, "es_reindex_all",
            classmethod(lambda cls, *, chunk_size=500: pytest.fail("should not reindex")),
        )
        with override_settings(ELASTICSEARCH_ENABLED=True):
            summary = upsert_from_source(
                ExchangeRate, _rows(1), unique_fields=["code"], reindex=False
            )
        assert summary["reindex"] is None


@pytest.mark.django_db
def test_sync_exchange_rates_command():
    out = StringIO()
    call_command("sync_exchange_rates", stdout=out)
    assert "Synced 10 rates" in out.getvalue()
    assert ExchangeRate.objects.count() == 10
    # Idempotent re-run — codes are unique, so no duplicates
    call_command("sync_exchange_rates", stdout=StringIO())
    assert ExchangeRate.objects.count() == 10
