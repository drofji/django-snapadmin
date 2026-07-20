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

from demo.apps.shop.models import ExchangeRate
from snapadmin.etl import StaleSyncAbort, stale_sync, upsert_from_source
from snapadmin.models import SnapPurgeError


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
        from demo.apps.shop.models import SearchLog

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

    def _capture_bulk_create_kwargs(self, monkeypatch):
        captured = {}

        def fake_bulk_create(objs, **kwargs):
            captured["kwargs"] = kwargs
            return list(objs)

        monkeypatch.setattr(ExchangeRate.objects, "bulk_create", fake_bulk_create)
        return captured

    def test_passes_unique_fields_when_backend_supports_target(self, monkeypatch):
        # PostgreSQL/SQLite: the conflict target is required and forwarded.
        from django.db import connections, router

        conn = connections[router.db_for_write(ExchangeRate)]
        monkeypatch.setattr(
            conn.features, "supports_update_conflicts_with_target", True
        )
        captured = self._capture_bulk_create_kwargs(monkeypatch)
        upsert_from_source(ExchangeRate, _rows(2), unique_fields=["code"])
        assert captured["kwargs"]["unique_fields"] == ["code"]
        assert captured["kwargs"]["update_conflicts"] is True

    def test_omits_unique_fields_on_mysql_like_backend(self, monkeypatch):
        # MySQL/MariaDB: ON DUPLICATE KEY UPDATE cannot take an explicit target,
        # so unique_fields must NOT be forwarded (else NotSupportedError).
        from django.db import connections, router

        conn = connections[router.db_for_write(ExchangeRate)]
        monkeypatch.setattr(
            conn.features, "supports_update_conflicts_with_target", False
        )
        captured = self._capture_bulk_create_kwargs(monkeypatch)
        upsert_from_source(ExchangeRate, _rows(2), unique_fields=["code"])
        assert "unique_fields" not in captured["kwargs"]
        assert captured["kwargs"]["update_conflicts"] is True
        # update_fields is still derived (everything but the conflict target/pk).
        assert "base" in captured["kwargs"]["update_fields"]
        assert "code" not in captured["kwargs"]["update_fields"]


@pytest.mark.django_db
class TestStaleSync:
    """`stale_sync` deletes rows whose natural key vanished from the latest
    source sync, guarded by a `max_fraction` ceiling so a truncated feed can't
    silently wipe most of the table."""

    def _seed(self, n):
        upsert_from_source(ExchangeRate, _rows(n), unique_fields=["code"])
        return set(ExchangeRate.objects.values_list("code", flat=True))

    def test_deletes_only_rows_absent_from_seen_keys(self):
        self._seed(5)                         # USD GBP JPY CHF CAD
        # Source now only reports the first three currencies.
        result = stale_sync(ExchangeRate, ["USD", "GBP", "JPY"], key_field="code",
                            max_fraction=1.0)
        assert result["deleted"] == 2
        assert set(ExchangeRate.objects.values_list("code", flat=True)) == {"USD", "GBP", "JPY"}

    def test_nothing_stale_is_a_noop(self):
        codes = self._seed(4)
        result = stale_sync(ExchangeRate, codes, key_field="code")
        assert result["deleted"] == 0
        assert result["stale"] == 0
        assert ExchangeRate.objects.count() == 4

    def test_fraction_guard_aborts_and_deletes_nothing(self):
        self._seed(10)
        # Reporting only one surviving key would delete 9/10 = 90% ≫ default 10%.
        with pytest.raises(StaleSyncAbort) as exc:
            stale_sync(ExchangeRate, ["USD"], key_field="code")
        assert ExchangeRate.objects.count() == 10          # nothing deleted
        assert exc.value.stale == 9
        assert exc.value.total == 10
        assert exc.value.fraction == pytest.approx(0.9)

    def test_fraction_guard_allows_deletion_within_ceiling(self):
        self._seed(10)
        # Drop exactly one row = 10%, which equals (not exceeds) the ceiling.
        seen = set(ExchangeRate.objects.values_list("code", flat=True)) - {"CZK"}
        result = stale_sync(ExchangeRate, seen, key_field="code", max_fraction=0.1)
        assert result["deleted"] == 1
        assert ExchangeRate.objects.filter(code="CZK").exists() is False

    def test_empty_seen_keys_would_wipe_table_and_is_blocked(self):
        self._seed(5)
        with pytest.raises(StaleSyncAbort):
            stale_sync(ExchangeRate, [], key_field="code")
        assert ExchangeRate.objects.count() == 5

    def test_dry_run_reports_without_deleting(self):
        self._seed(6)
        result = stale_sync(ExchangeRate, ["USD"], key_field="code",
                            max_fraction=1.0, dry_run=True)
        assert result["dry_run"] is True
        assert result["stale"] == 5
        assert result["deleted"] == 0
        assert ExchangeRate.objects.count() == 6          # untouched

    def test_queryset_scopes_the_candidate_rows(self):
        self._seed(5)
        # Only sync the EUR-based subset; a scoped base means rows outside it are
        # never considered stale even though their code isn't in seen_keys.
        ExchangeRate.objects.filter(code="USD").update(base="GBP")
        eur = ExchangeRate.objects.filter(base="EUR")
        result = stale_sync(ExchangeRate, ["GBP", "JPY"], key_field="code",
                            queryset=eur, max_fraction=1.0)
        # USD is base=GBP → outside the scope → survives despite not being seen.
        assert ExchangeRate.objects.filter(code="USD").exists() is True
        assert result["total"] == 4          # only the 4 EUR rows were candidates

    def test_empty_table_is_a_noop(self):
        result = stale_sync(ExchangeRate, ["USD"], key_field="code")
        assert result == {"total": 0, "stale": 0, "deleted": 0, "fraction": 0.0, "dry_run": False}

    def test_es_only_model_rejected(self):
        from demo.apps.shop.models import SearchLog
        with pytest.raises(ValueError, match="ES_ONLY"):
            stale_sync(SearchLog, ["x"], key_field="query")

    def test_missing_key_field_rejected(self):
        with pytest.raises(ValueError, match="key_field"):
            stale_sync(ExchangeRate, ["USD"], key_field="")

    @pytest.mark.parametrize("bad", [0, -0.1, 1.5])
    def test_invalid_max_fraction_rejected(self, bad):
        with pytest.raises(ValueError, match="max_fraction"):
            stale_sync(ExchangeRate, ["USD"], key_field="code", max_fraction=bad)

    def test_dual_model_clears_stale_docs_from_es(self, monkeypatch):
        self._seed(5)
        deleted_pks = {}
        monkeypatch.setattr(
            ExchangeRate, "_delete_pks_from_es",
            classmethod(lambda cls, pks: deleted_pks.setdefault("pks", list(pks)) or True),
        )
        stale = list(ExchangeRate.objects.exclude(code__in=["USD", "GBP", "JPY"])
                     .values_list("pk", flat=True))
        stale_sync(ExchangeRate, ["USD", "GBP", "JPY"], key_field="code", max_fraction=1.0)
        assert sorted(deleted_pks["pks"]) == sorted(stale)

    def test_es_mirror_failure_raises_after_db_delete(self, monkeypatch):
        self._seed(5)
        monkeypatch.setattr(
            ExchangeRate, "_delete_pks_from_es",
            classmethod(lambda cls, pks: False),      # ES mirror clear fails
        )
        with pytest.raises(SnapPurgeError):
            stale_sync(ExchangeRate, ["USD", "GBP", "JPY"], key_field="code", max_fraction=1.0)
        # DB delete already happened (no 2PC) — documented contract.
        assert ExchangeRate.objects.count() == 3


@pytest.mark.django_db
def test_sync_exchange_rates_command():
    out = StringIO()
    call_command("sync_exchange_rates", stdout=out)
    assert "Synced 10 rates" in out.getvalue()
    assert ExchangeRate.objects.count() == 10
    # Idempotent re-run — codes are unique, so no duplicates
    call_command("sync_exchange_rates", stdout=StringIO())
    assert ExchangeRate.objects.count() == 10


@pytest.mark.django_db
def test_sync_exchange_rates_command_prunes_shrunk_feed():
    call_command("sync_exchange_rates", stdout=StringIO())
    assert ExchangeRate.objects.count() == 10
    # Feed shrinks to 7 currencies and prunes the 3 it no longer reports.
    out = StringIO()
    call_command("sync_exchange_rates", "--only", "7", "--prune", stdout=out)
    assert "Pruned 3 stale currency row(s)" in out.getvalue()
    assert ExchangeRate.objects.count() == 7


@pytest.mark.django_db
def test_sync_exchange_rates_command_prune_respects_guard():
    call_command("sync_exchange_rates", stdout=StringIO())
    # Shrinking to a single currency would delete 90% → guard aborts the prune.
    err = StringIO()
    call_command("sync_exchange_rates", "--only", "1", "--prune", stderr=err)
    assert "aborted" in err.getvalue().lower()
    assert ExchangeRate.objects.count() == 10          # nothing pruned
