"""
Tests for :mod:`snapadmin.importing` (#IMP2) — the write-side counterpart to
:mod:`snapadmin.exporting`. See the module docstring for the eight-point
import contract (column mapping, the natural-key duplicate rule, the
``on_conflict`` modes, crash-safe chunked resume, the write-surface guard).
"""

import csv
import json
import os
from unittest.mock import patch

import pytest
from django.core.management import call_command

from demo.apps.shop.models import Category, Customer, ExchangeRate, Order, Product, Tag
from snapadmin.exporting import export_dir
from snapadmin.importing import (
    SnapImportError,
    _coerce,
    _message_dict,
    _natural_key_values,
    _process_row,
    _publish_report,
    check_write_surface,
    guess_import_format,
    iter_input_rows,
    read_header,
    resolve_column_map,
    resolve_natural_key,
    run_import_job,
    start_import,
)
from snapadmin.models import SnapImportJob


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_ndjson(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _read_report(job):
    path = os.path.join(export_dir(), job.report_file_name)
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ── format inference / header reading ───────────────────────────────────────

class TestGuessImportFormat:
    def test_csv_extension(self):
        assert guess_import_format("/tmp/x.csv") == "csv"

    @pytest.mark.parametrize("ext", [".json", ".ndjson", ".jsonl"])
    def test_json_family_extensions(self, ext):
        assert guess_import_format(f"/tmp/x{ext}") == "json"

    def test_unknown_extension_raises(self):
        with pytest.raises(SnapImportError, match="Can't infer"):
            guess_import_format("/tmp/x.tsv")


class TestReadHeader:
    def test_csv_header(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name", "slug"], [{"name": "A", "slug": "a"}])
        assert read_header(str(path), "csv") == ["name", "slug"]

    def test_csv_empty_file_returns_empty_list(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("")
        assert read_header(str(path), "csv") == []

    def test_json_header_from_first_line(self, tmp_path):
        path = tmp_path / "in.json"
        _write_ndjson(path, [{"name": "A"}, {"slug": "b"}])
        assert read_header(str(path), "json") == ["name"]

    def test_json_skips_blank_lines(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text("\n\n" + json.dumps({"name": "A"}) + "\n")
        assert read_header(str(path), "json") == ["name"]

    def test_json_empty_file_returns_empty_list(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("")
        assert read_header(str(path), "json") == []

    def test_unknown_format_raises(self, tmp_path):
        path = tmp_path / "in.txt"
        path.write_text("x")
        with pytest.raises(SnapImportError, match="Unknown import format"):
            read_header(str(path), "xml")


# ── column mapping ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestResolveColumnMap:
    def test_matches_by_field_name_case_insensitively(self):
        column_map, unmapped = resolve_column_map(Category, ["NAME", "slug", "Is_Active"])
        assert column_map == {"NAME": "name", "slug": "slug", "Is_Active": "is_active"}
        assert unmapped == []

    def test_matches_by_verbose_name(self):
        # Category.is_active's verbose_name is "Active".
        column_map, _ = resolve_column_map(Category, ["Active"])
        assert column_map == {"Active": "is_active"}

    def test_matches_normalising_whitespace_and_underscores(self):
        column_map, _ = resolve_column_map(Category, ["is active", "  slug  "])
        assert column_map == {"is active": "is_active", "  slug  ": "slug"}

    def test_unmapped_column_is_reported_not_guessed(self):
        column_map, unmapped = resolve_column_map(Category, ["name", "totally_unknown"])
        assert column_map == {"name": "name"}
        assert unmapped == ["totally_unknown"]

    def test_explicit_map_wins_over_name_matching(self):
        column_map, _ = resolve_column_map(
            Category, ["Category Name"], explicit={"Category Name": "name"},
        )
        assert column_map == {"Category Name": "name"}

    def test_explicit_map_naming_unknown_field_raises(self):
        with pytest.raises(SnapImportError, match="not a field"):
            resolve_column_map(Category, ["x"], explicit={"x": "does_not_exist"})


# ── natural key resolution ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestResolveNaturalKey:
    def test_explicit_string_key(self):
        assert resolve_natural_key(Category, explicit="name", mapped_field_names={"name"}) == ("name",)

    def test_explicit_tuple_key(self):
        result = resolve_natural_key(
            Category, explicit=("name", "slug"), mapped_field_names={"name", "slug"},
        )
        assert result == ("name", "slug")

    def test_explicit_key_naming_unknown_field_raises(self):
        with pytest.raises(SnapImportError, match="not a field"):
            resolve_natural_key(Category, explicit="nope", mapped_field_names=set())

    def test_defaults_to_first_unique_field(self):
        # ExchangeRate.code is unique=True.
        result = resolve_natural_key(ExchangeRate, explicit=None, mapped_field_names={"code", "rate"})
        assert result == ("code",)

    def test_defaults_to_pk_when_no_unique_field_but_pk_is_mapped(self):
        # Category has no unique=True field.
        result = resolve_natural_key(Category, explicit=None, mapped_field_names={"id", "name"})
        assert result == ("id",)

    def test_none_when_no_unique_field_and_pk_not_mapped(self):
        result = resolve_natural_key(Category, explicit=None, mapped_field_names={"name"})
        assert result is None


# ── write-surface rules ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCheckWriteSurface:
    def test_api_read_only_model_is_refused(self):
        with pytest.raises(SnapImportError, match="api_read_only"):
            check_write_surface(ExchangeRate, {"code", "rate"})

    def test_http_method_names_without_a_write_verb_is_refused(self, monkeypatch):
        # get_model_meta() checks the registry entry before the class attribute, and
        # treats an explicit None there as a real value rather than "unset" — so
        # register(Category, api_http_method_names=None) would not undo this override,
        # it would permanently shadow Category's class attribute for the rest of the
        # test session. monkeypatch.setitem restores the registry entry exactly as it
        # was (present or absent) once this test ends.
        from snapadmin.registry import _REGISTRY
        monkeypatch.setitem(_REGISTRY.setdefault(Category, {}), "api_http_method_names", ["get", "head"])
        with pytest.raises(SnapImportError, match="excludes every write verb"):
            check_write_surface(Category, {"name"})

    def test_excluded_field_is_refused_by_name(self):
        from demo.apps.shop.models import AuditLog
        with pytest.raises(SnapImportError, match="user_email"):
            check_write_surface(AuditLog, {"action", "user_email"})

    def test_field_outside_write_allowlist_is_refused_by_name(self, monkeypatch):
        # Category.api_write_fields = ["name", "slug", "is_active"] — id is not in it,
        # but id is never itself excluded/allowlisted since it's the pk; use a made-up
        # scenario instead by tightening the allowlist via the registry. See the note
        # above test_http_method_names_without_a_write_verb_is_refused about why this
        # uses monkeypatch.setitem rather than register(Category, api_write_fields=None).
        from snapadmin.registry import _REGISTRY
        monkeypatch.setitem(_REGISTRY.setdefault(Category, {}), "api_write_fields", ["name"])
        with pytest.raises(SnapImportError, match="slug"):
            check_write_surface(Category, {"name", "slug"})

    def test_masked_field_is_refused_with_no_requester(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": ["email"]}
        with pytest.raises(SnapImportError, match="email"):
            check_write_surface(Customer, {"first_name", "email"}, requested_by=None)

    def test_masked_field_is_allowed_for_a_superuser_requester(self, settings, django_user_model):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.Customer": ["email"]}
        superuser = django_user_model.objects.create_superuser(
            username="admin2", email="a@example.com", password="x",
        )
        check_write_surface(Customer, {"first_name", "email"}, requested_by=superuser)  # no raise

    def test_unmasked_writable_model_passes(self):
        check_write_surface(Category, {"name", "slug", "is_active"})  # no raise

    def test_tenant_scoped_model_rejects_a_column_mapped_to_the_tenant_field(self):
        # Order is tenant-scoped (#FUT1) — its tenant column is assigned only
        # from --tenant (see TestTenantScopedImport below), never from a file
        # column, rejected by name rather than silently dropped or applied.
        with pytest.raises(SnapImportError, match="tenant_id"):
            check_write_surface(Order, {"customer_id", "total", "tenant_id"})


# ── tenant-scoped import (#FUT1b) ───────────────────────────────────────────

@pytest.mark.django_db
class TestTenantScopedImport:
    def test_start_import_requires_tenant_for_a_tenant_scoped_model(self, tmp_path):
        path = tmp_path / "orders.csv"
        _write_csv(path, ["customer", "total"], [])
        with pytest.raises(SnapImportError, match="--tenant"):
            start_import(Order, file_path=str(path))

    def test_start_import_and_run_stamp_the_given_tenant(self, tmp_path):
        from snapadmin.tenancy import use_tenant

        customer = Customer.objects.create(
            first_name="A", last_name="B", email="ab@example.com",
        )
        path = tmp_path / "orders.csv"
        _write_csv(
            path, ["customer", "total"],
            [{"customer": customer.pk, "total": "12.50"}],
        )

        job = start_import(Order, file_path=str(path), tenant="acme")
        assert job.tenant_id == "acme"

        summary = run_import_job(job, file_path=str(path))
        assert summary["created"] == 1
        assert summary["failed"] == 0

        with use_tenant("acme"):
            order = Order.objects.get(customer=customer)
        assert order.tenant_id == "acme"

    def test_resume_keeps_the_original_tenant_regardless_of_a_later_argument(self, tmp_path):
        customer = Customer.objects.create(
            first_name="A", last_name="B", email="ab2@example.com",
        )
        path = tmp_path / "orders.csv"
        _write_csv(
            path, ["customer", "total"],
            [{"customer": customer.pk, "total": "5.00"}],
        )
        job = start_import(Order, file_path=str(path), tenant="acme")
        job.status = SnapImportJob.Status.FAILED
        job.save(update_fields=["status"])

        resumed = start_import(Order, file_path=str(path), tenant="a-different-tenant", resume=True)
        assert resumed.pk == job.pk
        assert resumed.tenant_id == "acme"


# ── round trip: export -> import -> identical data ──────────────────────────

@pytest.mark.django_db
class TestRoundTrip:
    def _allow_id_writes(self, monkeypatch):
        # The exported CSV/NDJSON carries "id" as a column, and Category's own
        # api_write_fields (["name", "slug", "is_active"]) does not include it — the
        # write-surface guard (#IMP2, rule 8) correctly refuses to let an import set an
        # arbitrary primary key otherwise. A round trip that means to preserve row
        # identity has to opt "id" into the allowlist explicitly, exactly as a real
        # project would.
        from snapadmin.registry import _REGISTRY
        monkeypatch.setitem(
            _REGISTRY.setdefault(Category, {}), "api_write_fields", ["id", "name", "slug", "is_active"]
        )

    def test_export_then_import_reproduces_the_data(self, monkeypatch):
        self._allow_id_writes(monkeypatch)
        from snapadmin.exporting import export_dir as _export_dir
        from snapadmin.exporting import run_export_job
        from snapadmin.models import SnapExportJob

        originals = [
            Category.objects.create(name="Books", slug="books", is_active=True),
            Category.objects.create(name="Music", slug="music", is_active=False),
            Category.objects.create(name="Games", slug="games", is_active=True),
        ]
        original_rows = {
            c.pk: (c.name, c.slug, c.is_active) for c in originals
        }

        export_job = SnapExportJob.objects.create(
            app_label="demo", model="Category", export_format="csv",
        )
        run_export_job(export_job.pk)
        export_job.refresh_from_db()
        csv_path = os.path.join(_export_dir(), export_job.file_name)

        Category.objects.all().delete()
        assert Category.objects.count() == 0

        job = start_import(Category, file_path=csv_path)
        summary = run_import_job(job, file_path=csv_path)

        assert summary["created"] == 3
        assert summary["failed"] == 0
        assert Category.objects.count() == 3
        for pk, (name, slug, is_active) in original_rows.items():
            row = Category.objects.get(pk=pk)
            assert (row.name, row.slug, row.is_active) == (name, slug, is_active)

    def test_round_trip_via_json_format(self, monkeypatch, tmp_path):
        self._allow_id_writes(monkeypatch)
        Category.objects.create(name="Toys", slug="toys", is_active=True)
        path = tmp_path / "categories.json"
        rows = list(Category.objects.values("id", "name", "slug", "is_active"))
        _write_ndjson(path, rows)
        Category.objects.all().delete()

        job = start_import(Category, file_path=str(path), import_format="json")
        summary = run_import_job(job, file_path=str(path))

        assert summary["created"] == 1
        assert Category.objects.get(slug="toys").name == "Toys"


# ── on_conflict modes ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOnConflictModes:
    def _csv_with_a_duplicate(self, tmp_path, **overrides):
        row = {"name": "Existing", "slug": "new-slug", "is_active": "False"}
        row.update(overrides)
        path = tmp_path / "dupes.csv"
        _write_csv(path, ["name", "slug", "is_active"], [row])
        return str(path)

    def test_fail_reports_the_row_and_leaves_the_row_untouched(self, tmp_path):
        existing = Category.objects.create(name="Existing", slug="old-slug", is_active=True)
        path = self._csv_with_a_duplicate(tmp_path)

        job = start_import(Category, file_path=path, natural_key="name", on_conflict="fail")
        summary = run_import_job(job, file_path=path)

        assert summary["failed"] == 1
        assert summary["created"] == 0
        existing.refresh_from_db()
        assert existing.slug == "old-slug" and existing.is_active is True
        report = _read_report(job)
        assert report[0]["action"] == "failed"
        assert "Duplicate key" in report[0]["errors"]["__all__"][0]

    def test_skip_reports_the_row_and_leaves_the_row_untouched(self, tmp_path):
        existing = Category.objects.create(name="Existing", slug="old-slug", is_active=True)
        path = self._csv_with_a_duplicate(tmp_path)

        job = start_import(Category, file_path=path, natural_key="name", on_conflict="skip")
        summary = run_import_job(job, file_path=path)

        assert summary["skipped"] == 1
        existing.refresh_from_db()
        assert existing.slug == "old-slug"
        report = _read_report(job)
        assert report[0]["action"] == "skipped"
        assert report[0]["pk"] == existing.pk

    def test_update_overwrites_the_mapped_fields_and_keeps_the_pk(self, tmp_path):
        existing = Category.objects.create(name="Existing", slug="old-slug", is_active=True)
        path = self._csv_with_a_duplicate(tmp_path)

        job = start_import(Category, file_path=path, natural_key="name", on_conflict="update")
        summary = run_import_job(job, file_path=path)

        assert summary["updated"] == 1
        existing.refresh_from_db()
        assert existing.pk == existing.pk  # unchanged identity
        assert existing.slug == "new-slug"
        assert existing.is_active is False
        report = _read_report(job)
        assert report[0]["action"] == "updated"
        assert report[0]["pk"] == existing.pk

    def test_no_natural_key_always_creates(self, tmp_path):
        Category.objects.create(name="Existing", slug="old-slug", is_active=True)
        path = tmp_path / "no_key.csv"
        _write_csv(path, ["name", "slug", "is_active"],
                    [{"name": "Existing", "slug": "another", "is_active": "True"}])

        job = start_import(Category, file_path=str(path))  # no natural_key
        summary = run_import_job(job, file_path=str(path))

        assert summary["created"] == 1
        assert Category.objects.filter(name="Existing").count() == 2


# ── malformed rows never abort the run ──────────────────────────────────────

@pytest.mark.django_db
class TestMalformedRows:
    def test_a_bad_row_is_reported_and_the_run_continues(self, tmp_path):
        path = tmp_path / "mixed.csv"
        _write_csv(path, ["name", "slug", "is_active"], [
            {"name": "Good One", "slug": "good-one", "is_active": "True"},
            # name exceeds max_length=100 -> full_clean() ValidationError
            {"name": "x" * 200, "slug": "too-long", "is_active": "True"},
            {"name": "Good Two", "slug": "good-two", "is_active": "False"},
        ])

        job = start_import(Category, file_path=str(path))
        summary = run_import_job(job, file_path=str(path))

        assert summary["created"] == 2
        assert summary["failed"] == 1
        assert Category.objects.filter(name="Good One").exists()
        assert Category.objects.filter(name="Good Two").exists()
        assert not Category.objects.filter(slug="too-long").exists()

        report = _read_report(job)
        actions = [r["action"] for r in report[:3]]
        assert actions == ["created", "failed", "created"]
        assert "name" in report[1]["errors"]

    def test_unmapped_columns_are_reported_in_the_summary_not_a_hard_failure(self, tmp_path):
        path = tmp_path / "extra_col.csv"
        _write_csv(path, ["name", "slug", "is_active", "totally_unknown"], [
            {"name": "A", "slug": "a", "is_active": "True", "totally_unknown": "ignored"},
        ])
        job = start_import(Category, file_path=str(path))
        summary = run_import_job(job, file_path=str(path))
        assert summary["created"] == 1
        report = _read_report(job)
        assert report[-1]["summary"]["unmapped_columns"] == ["totally_unknown"]

    def test_malformed_json_line_is_a_total_failure_not_a_per_row_one(self, tmp_path):
        # A structurally broken input file can't be "one bad row" — json.loads()
        # raises while reading, before any row-level try/except applies.
        path = tmp_path / "broken.json"
        path.write_text('{"name": "A"}\nnot json at all\n')
        job = start_import(Category, file_path=str(path), import_format="json")
        summary = run_import_job(job, file_path=str(path))
        assert "errors" in summary
        job.refresh_from_db()
        assert job.status == "failed"


# ── structural failures (SnapImportError paths) ─────────────────────────────

@pytest.mark.django_db
class TestStructuralFailures:
    def test_empty_file_is_a_total_failure(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("")
        job = start_import(Category, file_path=str(path))
        summary = run_import_job(job, file_path=str(path))
        assert "errors" in summary
        assert "no header row" in summary["errors"][0]

    def test_no_column_maps_is_a_total_failure(self, tmp_path):
        path = tmp_path / "nomatch.csv"
        _write_csv(path, ["totally", "unknown"], [{"totally": "x", "unknown": "y"}])
        job = start_import(Category, file_path=str(path))
        summary = run_import_job(job, file_path=str(path))
        assert "errors" in summary
        assert "No column" in summary["errors"][0]

    def test_natural_key_not_in_mapped_columns_is_a_total_failure(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}])
        job = start_import(Category, file_path=str(path), natural_key="slug")
        summary = run_import_job(job, file_path=str(path))
        assert "errors" in summary
        assert "slug" in summary["errors"][0]

    def test_write_surface_violation_is_a_total_failure(self, tmp_path):
        path = tmp_path / "rates.csv"
        _write_csv(path, ["code", "rate"], [{"code": "USD", "rate": "1.0"}])
        job = start_import(ExchangeRate, file_path=str(path))
        summary = run_import_job(job, file_path=str(path))
        assert "errors" in summary
        assert "api_read_only" in summary["errors"][0]
        assert ExchangeRate.objects.count() == 0


# ── single-flight, cancellation ──────────────────────────────────────────────

@pytest.mark.django_db
class TestSingleFlightAndCancellation:
    def test_already_processing_job_is_skipped(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}])
        job = start_import(Category, file_path=str(path))
        job.status = SnapImportJob.Status.PROCESSING
        job.save(update_fields=["status"])
        summary = run_import_job(job, file_path=str(path))
        assert summary == {"skipped": True, "reason": "already processing or finished"}
        assert Category.objects.count() == 0

    def test_cancellation_stops_between_chunks_leaving_partial_progress(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": f"C{i}"} for i in range(6)])
        job = start_import(Category, file_path=str(path))

        real_refresh = SnapImportJob.refresh_from_db

        def cancel_after_first_chunk(self, *args, **kwargs):
            real_refresh(self, *args, **kwargs)
            if self.pk == job.pk and self.processed_rows >= 2:
                type(self).objects.filter(pk=self.pk).update(status=SnapImportJob.Status.CANCELLED)
                real_refresh(self, *args, **kwargs)

        with patch.object(SnapImportJob, "refresh_from_db", cancel_after_first_chunk):
            summary = run_import_job(job, file_path=str(path), chunk_size=2)

        assert summary.get("cancelled") is True
        job.refresh_from_db()
        assert job.status == "cancelled"
        assert job.processed_rows == 2
        assert Category.objects.count() == 2


# ── crash-safe resume ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestResume:
    def test_resumed_run_after_a_simulated_crash_produces_no_duplicates(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": f"C{i}"} for i in range(5)])

        job = start_import(Category, file_path=str(path), natural_key="name")

        call_count = 0
        from snapadmin import importing as importing_module
        original_write_lines = importing_module._write_lines

        def flaky_write_lines(handle, lines):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated crash")
            return original_write_lines(handle, lines)

        with patch("snapadmin.importing._write_lines", side_effect=flaky_write_lines):
            summary = run_import_job(job, file_path=str(path), chunk_size=2)

        assert "errors" in summary
        job.refresh_from_db()
        assert job.status == "failed"
        assert job.processed_rows == 2
        assert Category.objects.count() == 2

        job2 = start_import(Category, file_path=str(path), natural_key="name", resume=True)
        assert job2.pk == job.pk

        summary2 = run_import_job(job2, file_path=str(path), chunk_size=2)

        # created_count accumulates across the whole job (2 from the crashed
        # attempt + 3 from this one), the same way processed_rows does.
        assert summary2["created"] == 5
        assert Category.objects.count() == 5
        assert sorted(Category.objects.values_list("name", flat=True)) == [f"C{i}" for i in range(5)]
        # No duplicate report lines for the rows the first attempt already
        # confirmed — the report was truncated back to its checkpoint, not
        # appended to blindly.
        report = _read_report(job2)
        rows_reported = [r["row"] for r in report if "row" in r]
        assert rows_reported == sorted(set(rows_reported))  # each row number appears once

    def test_resume_with_no_prior_job_starts_fresh(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "Solo"}])
        job = start_import(Category, file_path=str(path), resume=True)
        summary = run_import_job(job, file_path=str(path))
        assert summary["created"] == 1

    def test_resume_does_not_reuse_a_different_source_files_job(self, tmp_path):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        _write_csv(path_a, ["name"], [{"name": "FromA"}])
        _write_csv(path_b, ["name"], [{"name": "FromB"}])

        job_a = start_import(Category, file_path=str(path_a))
        job_a.status = SnapImportJob.Status.FAILED
        job_a.save(update_fields=["status"])

        job_b = start_import(Category, file_path=str(path_b), resume=True)
        assert job_b.pk != job_a.pk

    def test_stale_processed_rows_with_no_local_report_restarts_clean(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "Only"}])
        job = start_import(Category, file_path=str(path))
        # Simulate a job that recorded progress on a different worker whose
        # local report file never made it here.
        job.processed_rows = 99
        job.created_count = 99
        job.status = SnapImportJob.Status.FAILED
        job.save()

        summary = run_import_job(job, file_path=str(path))
        assert summary["created"] == 1  # not 100
        job.refresh_from_db()
        assert job.processed_rows == 1


# ── management command ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSnapadminImportCommand:
    def test_end_to_end_via_the_command(self, tmp_path, capsys):
        path = tmp_path / "categories.csv"
        _write_csv(path, ["name", "slug", "is_active"], [
            {"name": "One", "slug": "one", "is_active": "True"},
            {"name": "Two", "slug": "two", "is_active": "False"},
        ])
        call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path))
        assert Category.objects.count() == 2
        out = capsys.readouterr().out
        assert "2 created" in out

    def test_unknown_model_errors(self):
        from django.core.management import CommandError
        with pytest.raises(CommandError, match="Unknown model"):
            call_command("snapadmin_import", "--model", "demo.Nope", "--file", "x.csv")

    def test_non_registered_model_errors(self):
        from django.core.management import CommandError
        with pytest.raises(CommandError, match="not a SnapAdmin model"):
            call_command("snapadmin_import", "--model", "auth.User", "--file", "x.csv")

    def test_bad_map_json_errors(self, tmp_path):
        from django.core.management import CommandError
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}])
        with pytest.raises(CommandError, match="valid JSON"):
            call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path),
                         "--map", "{not json")

    def test_map_must_be_an_object(self, tmp_path):
        from django.core.management import CommandError
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}])
        with pytest.raises(CommandError, match="JSON object"):
            call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path),
                         "--map", "[1, 2]")

    def test_map_and_natural_key_and_on_conflict_flow_through(self, tmp_path):
        existing = Category.objects.create(name="Existing", slug="old", is_active=True)
        path = tmp_path / "in.csv"
        _write_csv(path, ["Category Name", "slug", "is_active"], [
            {"Category Name": "Existing", "slug": "updated", "is_active": "False"},
        ])
        call_command(
            "snapadmin_import", "--model", "demo.Category", "--file", str(path),
            "--map", json.dumps({"Category Name": "name"}),
            "--natural-key", "name", "--on-conflict", "update",
        )
        existing.refresh_from_db()
        assert existing.slug == "updated"

    def test_requested_by_resolves_a_user(self, tmp_path, django_user_model):
        user = django_user_model.objects.create_user(username="importer", password="x")
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}])
        call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path),
                     "--requested-by", "importer")
        job = SnapImportJob.objects.latest("created_at")
        assert job.requested_by_id == user.pk

    def test_unknown_requested_by_errors(self, tmp_path):
        from django.core.management import CommandError
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}])
        with pytest.raises(CommandError, match="No user"):
            call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path),
                         "--requested-by", "ghost")

    def test_structural_failure_raises_commanderror(self, tmp_path):
        from django.core.management import CommandError
        path = tmp_path / "in.csv"
        _write_csv(path, ["code", "rate"], [{"code": "USD", "rate": "1.0"}])
        with pytest.raises(CommandError, match="Import failed"):
            call_command("snapadmin_import", "--model", "demo.ExchangeRate", "--file", str(path))

    def test_failed_rows_raise_commanderror_after_reporting(self, tmp_path, capsys):
        path = tmp_path / "mixed.csv"
        _write_csv(path, ["name"], [{"name": "Good"}, {"name": "x" * 200}])
        from django.core.management import CommandError
        with pytest.raises(CommandError, match="1 row"):
            call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path))
        out = capsys.readouterr().out
        assert "1 created" in out and "1 failed" in out

    def test_resume_flag_continues_a_failed_job(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": f"C{i}"} for i in range(4)])

        call_count = 0
        from snapadmin import importing as importing_module
        original = importing_module._write_lines

        def flaky(handle, lines):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            return original(handle, lines)

        from django.core.management import CommandError
        with patch("snapadmin.importing._write_lines", side_effect=flaky):
            with pytest.raises(CommandError):
                call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path),
                             "--chunk-size", "2")
        assert Category.objects.count() == 0

        call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path),
                     "--resume", "--chunk-size", "2")
        assert Category.objects.count() == 4
        assert SnapImportJob.objects.filter(app_label="demo", model="Category").count() == 1

    def test_cancelled_run_is_reported_without_raising(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": f"C{i}"} for i in range(4)])
        with patch("snapadmin.management.commands.snapadmin_import.run_import_job",
                   return_value={"cancelled": True, "created": 2, "updated": 0, "skipped": 0, "failed": 0}):
            call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path))

    def test_skipped_run_is_reported(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}])
        with patch("snapadmin.management.commands.snapadmin_import.run_import_job",
                   return_value={"skipped": True, "reason": "already processing or finished"}):
            call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path))

    def test_unresolvable_format_raises_via_start_import(self, tmp_path):
        from django.core.management import CommandError
        path = tmp_path / "no_extension"
        path.write_text("name\nA\n")
        with pytest.raises(CommandError, match="Can't infer"):
            call_command("snapadmin_import", "--model", "demo.Category", "--file", str(path))


# ── direct coverage of small private helpers ────────────────────────────────

class TestIterInputRows:
    def test_csv_rows(self, tmp_path):
        path = tmp_path / "in.csv"
        _write_csv(path, ["name"], [{"name": "A"}, {"name": "B"}])
        assert list(iter_input_rows(str(path), "csv")) == [{"name": "A"}, {"name": "B"}]

    def test_json_rows_skip_blank_lines(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text("\n" + json.dumps({"name": "A"}) + "\n\n" + json.dumps({"name": "B"}) + "\n")
        assert list(iter_input_rows(str(path), "json")) == [{"name": "A"}, {"name": "B"}]

    def test_unknown_format_raises(self, tmp_path):
        path = tmp_path / "in.txt"
        path.write_text("x")
        with pytest.raises(SnapImportError, match="Unknown import format"):
            list(iter_input_rows(str(path), "xml"))


class TestCoerce:
    def test_blank_string_against_a_nullable_field_becomes_none(self):
        field = Product._meta.get_field("category")  # SnapForeignKey(..., null=True)
        assert _coerce(field, "") is None

    def test_non_blank_value_goes_through_to_python(self):
        field = Category._meta.get_field("name")
        assert _coerce(field, "Books") == "Books"


@pytest.mark.django_db
class TestNaturalKeyValuesMissingColumn:
    def test_returns_none_when_a_key_column_is_absent_from_the_row(self):
        # The row simply doesn't carry the "slug" column this time (e.g. an
        # NDJSON file whose rows don't all share the same key set).
        result = _natural_key_values(("slug",), {"name": "name"}, {"name": "A"})
        assert result is None


class TestMessageDictFallback:
    def test_plain_error_list_validation_error_without_message_dict(self):
        from django.core.exceptions import ValidationError
        exc = ValidationError("Enter a whole number.", code="invalid")
        assert _message_dict(exc) == {"__all__": ["Enter a whole number."]}


@pytest.mark.django_db
class TestProcessRowIntegrityError:
    def test_a_db_level_integrity_error_is_reported_as_a_failed_row(self):
        from django.db import IntegrityError

        with patch.object(Tag, "save", side_effect=IntegrityError("duplicate key value violates constraint")):
            entry = _process_row(
                Tag, {"name_col": "name"}, None, "fail", 1, {"name_col": "Sale"},
            )

        assert entry["action"] == "failed"
        assert entry["pk"] is None
        assert "duplicate key value" in entry["errors"]["__all__"][0]
        assert Tag.objects.count() == 0


@pytest.mark.django_db
class TestProcessRowBareValidationError:
    def test_to_python_raising_a_bare_validation_error_is_reported(self):
        # Product.price is a DecimalField — to_python() on a non-numeric raw
        # value raises a plain (non-error_dict) ValidationError, exercised
        # before full_clean() is ever reached.
        entry = _process_row(
            Product, {"price_col": "price"}, None, "fail", 1, {"price_col": "not-a-number"},
        )
        assert entry["action"] == "failed"
        assert Product.objects.count() == 0


@pytest.mark.django_db
class TestPublishReport:
    def _job(self):
        return SnapImportJob.objects.create(
            app_label="demo", model="Category", report_file_name="pub_test_report.ndjson",
        )

    def test_local_storage_is_a_no_op_when_paths_already_match(self):
        job = self._job()
        path = os.path.join(export_dir(), job.report_file_name)
        with open(path, "w") as fh:
            fh.write('{"row": 1}\n')
        _publish_report(job)  # must not raise; local storage already IS the file

    def test_remote_storage_uploads_the_local_file(self):
        from unittest.mock import MagicMock

        job = self._job()
        path = os.path.join(export_dir(), job.report_file_name)
        with open(path, "w") as fh:
            fh.write('{"row": 1}\n')

        fake_storage = MagicMock()
        fake_storage.path.side_effect = NotImplementedError("remote backend")
        fake_storage.exists.return_value = True

        with patch("snapadmin.importing.get_export_storage", return_value=fake_storage):
            _publish_report(job)

        fake_storage.delete.assert_called_once_with(job.report_file_name)
        assert fake_storage.save.call_count == 1
        assert fake_storage.save.call_args[0][0] == job.report_file_name


@pytest.mark.django_db
class TestResumePastEndOfFile:
    def test_skip_count_exceeding_the_file_stops_gracefully(self, tmp_path):
        path = tmp_path / "short.csv"
        _write_csv(path, ["name"], [{"name": "A"}, {"name": "B"}])

        job = start_import(Category, file_path=str(path))
        run_import_job(job, file_path=str(path))
        job.refresh_from_db()
        assert job.processed_rows == 2

        # Force a checkpoint further than the file actually has, as if a
        # different, longer run had been recorded against this job row.
        SnapImportJob.objects.filter(pk=job.pk).update(
            processed_rows=10, status=SnapImportJob.Status.FAILED,
        )
        job.refresh_from_db()

        summary = run_import_job(job, file_path=str(path))
        assert summary["created"] == 2  # unchanged — nothing left to read past row 2
        assert Category.objects.count() == 2
