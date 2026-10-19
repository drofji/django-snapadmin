"""
tests/test_import_command_outcomes.py — every outcome of an import run, through
the command that reports it (#EXT3e).

``snapadmin_import`` ended an idempotent re-run with ``KeyError: 'reason'``: the
summary's ``skipped`` key meant "this job was not claimed" (``True``, with a
``reason``) in one shape and "rows skipped" (a count) in another, and the command
asked ``if summary.get("skipped")`` before reading ``summary["reason"]``. The only
test of that line mocked ``run_import_job`` with one hand-written shape and
asserted nothing.

Every summary now carries an ``outcome`` that names its shape, the shapes are
``TypedDict``s (mypy is blocking, so reading a key one shape lacks is a type
error), and this module drives each outcome through the **real** job function
into the command's report. The class guard: an outcome added to the union without
a producer here fails the suite.
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import get_args, get_type_hints

import pytest
from django.core.management.base import CommandError

from demo.apps.shop.models import Category
from snapadmin.importing import _ImportSummary, run_import_job, start_import
from snapadmin.management.commands.snapadmin_import import Command
from snapadmin.models import SnapImportJob


def _write_csv(path, names):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name"])
        writer.writeheader()
        for name in names:
            writer.writerow({"name": name})


def _report(summary) -> str:
    command = Command(stdout=StringIO(), stderr=StringIO())
    command._report(summary, None)
    return command.stdout.getvalue()


def _finished_with_skipped_rows(tmp_path):
    path = tmp_path / "in.csv"
    _write_csv(path, ["Alpha", "Beta"])
    first = start_import(Category, file_path=str(path), natural_key="name", on_conflict="skip")
    run_import_job(first, file_path=str(path))
    again = start_import(Category, file_path=str(path), natural_key="name", on_conflict="skip")
    return run_import_job(again, file_path=str(path))


def _job_skipped(tmp_path):
    path = tmp_path / "in.csv"
    _write_csv(path, ["Alpha"])
    job = start_import(Category, file_path=str(path))
    job.status = SnapImportJob.Status.PROCESSING
    job.save(update_fields=["status"])
    return run_import_job(job, file_path=str(path))


def _cancelled(tmp_path):
    path = tmp_path / "in.csv"
    _write_csv(path, [f"C{i}" for i in range(4)])
    job = start_import(Category, file_path=str(path))

    def cancel(running_job):
        SnapImportJob.objects.filter(pk=running_job.pk).update(status=SnapImportJob.Status.CANCELLED)

    return run_import_job(job, file_path=str(path), chunk_size=2, on_progress=cancel)


def _failed(tmp_path):
    path = tmp_path / "in.csv"
    _write_csv(path, ["Alpha"])
    job = start_import(Category, file_path=str(path))
    path.unlink()
    return run_import_job(job, file_path=str(path))


PRODUCERS = {
    "finished": _finished_with_skipped_rows,
    "job_skipped": _job_skipped,
    "cancelled": _cancelled,
    "failed": _failed,
}


def test_every_outcome_the_summary_type_declares_has_a_producer_here():
    declared = {get_args(get_type_hints(shape)["outcome"])[0] for shape in get_args(_ImportSummary)}

    assert declared == set(PRODUCERS), (
        f"review: _ImportSummary declares outcomes {sorted(declared)} but this module produces "
        f"{sorted(PRODUCERS)} — drive every outcome through the real job and the command's report"
    )


@pytest.mark.django_db
class TestEveryOutcomeThroughTheCommand:
    def test_the_reported_case_a_rerun_that_skips_rows_reports_instead_of_crashing(self, tmp_path):
        summary = _finished_with_skipped_rows(tmp_path)

        assert summary["outcome"] == "finished"
        assert summary["skipped"] == 2
        assert "0 created, 0 updated, 2 skipped, 0 failed" in _report(summary)

    def test_a_job_another_worker_claimed_is_reported_as_skipped_with_its_reason(self, tmp_path):
        summary = _job_skipped(tmp_path)

        assert summary["outcome"] == "job_skipped"
        assert _report(summary) == "skipped (already processing or finished)\n"

    def test_a_cancelled_run_reports_its_partial_progress(self, tmp_path):
        summary = _cancelled(tmp_path)

        assert summary["outcome"] == "cancelled"
        assert _report(summary).startswith("cancelled — 2 created, 0 updated, 0 skipped, 0 failed")

    def test_a_failed_run_is_a_command_error_naming_the_cause(self, tmp_path):
        summary = _failed(tmp_path)

        assert summary["outcome"] == "failed"
        with pytest.raises(CommandError, match="^Import failed: "):
            _report(summary)

    @pytest.mark.parametrize("outcome", sorted(PRODUCERS))
    def test_every_outcome_keeps_the_keys_code_written_before_it_reads(self, tmp_path, outcome):
        summary = PRODUCERS[outcome](tmp_path)

        assert "skipped" in summary
        if outcome == "job_skipped":
            assert summary["skipped"] is True and summary["job_skipped"] is True
        else:
            assert isinstance(summary["skipped"], int) and "job_skipped" not in summary
