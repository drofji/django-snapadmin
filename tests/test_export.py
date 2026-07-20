"""
tests/test_export.py — async background export (issue #6)

Covers the SnapExportJob model (progress/ETA), the resumable/cancellable chunk
writer (snapadmin.exporting), and the /api/exports/ endpoints (create → poll →
download, cancel, permissions).
"""

import json
import os
import tempfile
from decimal import Decimal

import pytest
from django.core.files.storage import FileSystemStorage, Storage
from django.test import override_settings
from django.utils import timezone
from rest_framework import serializers

from snapadmin import exporting
from snapadmin.models import SnapExportJob


# A FileSystemStorage rooted at a *different* directory than export_dir(), used
# to exercise the SNAPADMIN_EXPORT_STORAGE path: the worker publishes the finished
# working file into it and the download endpoint reads back through it — the
# split-deployment (worker-disk ≠ web-disk) scenario, emulated locally.
_ALT_STORAGE_DIR = tempfile.mkdtemp(prefix="snap-alt-export-")


class AltExportStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", _ALT_STORAGE_DIR)
        super().__init__(*args, **kwargs)


_NOPATH_STORAGE_DIR = tempfile.mkdtemp(prefix="snap-nopath-export-")


class NoPathExportStorage(Storage):
    """Emulates a remote backend (S3/GCS): no local ``path()``, only open/save.

    Composition rather than a ``FileSystemStorage`` subclass — ``path()`` is
    called from *within* ``FileSystemStorage``'s own ``_save``/``exists``
    machinery, so overriding it there to raise breaks construction. Here it
    delegates to a private inner ``FileSystemStorage`` for the actual I/O and
    only ``path()`` itself is unimplemented, forcing ``_publish``'s
    storage-agnostic upload branch (the one a real S3/GCS backend takes).
    """

    def __init__(self, *args, **kwargs):
        self._inner = FileSystemStorage(location=_NOPATH_STORAGE_DIR)
        super().__init__(*args, **kwargs)

    def _open(self, name, mode="rb"):
        return self._inner._open(name, mode)

    def _save(self, name, content):
        return self._inner._save(name, content)

    def exists(self, name: str) -> bool:
        return self._inner.exists(name)

    def delete(self, name: str) -> None:
        self._inner.delete(name)

    def get_available_name(self, name: str, max_length=None) -> str:
        return self._inner.get_available_name(name, max_length=max_length)

    def size(self, name: str) -> int:
        return self._inner.size(name)

    def url(self, name: str) -> str:
        return self._inner.url(name)

    def path(self, name: str) -> str:
        raise NotImplementedError("This backend does not support absolute paths.")


@pytest.fixture
def products(db):
    from demo.apps.shop.models import Product
    for i in range(5):
        Product.objects.create(name=f"P{i}", price=Decimal(i))
    return list(SnapExportJob.objects.none()) or None


def _job(**kw):
    defaults = dict(app_label="demo", model="Product", export_format="csv")
    defaults.update(kw)
    return SnapExportJob.objects.create(**defaults)


# ── model progress / ETA ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestJobModel:
    def test_progress_percent(self):
        job = _job(total_rows=200, processed_rows=50)
        assert job.progress_percent == 25

    def test_progress_zero_total(self):
        assert _job(total_rows=0).progress_percent == 0
        assert _job(total_rows=0, status="completed").progress_percent == 100

    def test_eta_none_until_processing(self):
        assert _job(status="pending").eta_seconds is None

    def test_eta_completed_is_zero(self):
        assert _job(status="completed").eta_seconds == 0

    def test_eta_estimated_while_processing(self):
        job = _job(status="processing", total_rows=100, processed_rows=25,
                   started_at=timezone.now() - timezone.timedelta(seconds=10))
        # 25 rows in 10s → 2.5 rows/s → 75 remaining ≈ 30s
        assert job.eta_seconds == 30

    def test_target_model(self):
        from demo.apps.shop.models import Product
        assert _job().target_model() is Product

    def test_is_finished(self):
        assert _job(status="completed").is_finished is True
        assert _job(status="processing").is_finished is False

    def test_str(self):
        assert "demo.Product" in str(_job(total_rows=3, processed_rows=1))

    def test_eta_none_when_no_elapsed(self):
        # started_at in the future → elapsed <= 0 → rate 0 → ETA not computable.
        job = _job(status="processing", total_rows=100, processed_rows=10,
                   started_at=timezone.now() + timezone.timedelta(seconds=60))
        assert job.eta_seconds is None


# ── chunk writer ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRunExportJob:
    def test_csv_export(self, products):
        job = _job(export_format="csv")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        assert job.total_rows == 5 and job.processed_rows == 5
        text = open(exporting.output_path(job)).read()
        assert text.startswith("id,")  # header
        assert text.count("\n") == 6   # header + 5 rows

    def test_json_export(self, products):
        job = _job(export_format="json")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        lines = open(exporting.output_path(job)).read().strip().splitlines()
        assert len(lines) == 5
        assert json.loads(lines[0])["name"] == "P0"

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_chunked_still_complete(self, products):
        job = _job()
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.processed_rows == 5 and job.status == "completed"

    def test_filters_applied(self, products):
        job = _job(filters={"name": "P1"})
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.total_rows == 1
        assert open(exporting.output_path(job)).read().count("\n") == 2  # header + 1

    def test_precancelled_job_noop(self, products):
        job = _job(status="cancelled")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "cancelled"
        assert not os.path.exists(exporting.output_path(job))

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_cancel_between_chunks_stops(self, products, monkeypatch):
        job = _job()
        real_refresh = SnapExportJob.refresh_from_db
        state = {"n": 0}

        def refresh(self, *a, **k):
            real_refresh(self, *a, **k)
            state["n"] += 1
            if state["n"] == 2:  # flip to cancelled before the 2nd chunk
                SnapExportJob.objects.filter(pk=self.pk).update(status="cancelled")
                real_refresh(self, *a, **k)

        monkeypatch.setattr(SnapExportJob, "refresh_from_db", refresh)
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "cancelled"
        assert job.processed_rows < 5  # stopped early

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_resume_appends_from_cursor(self, products):
        # A clean resume: a fresh partial run is interrupted, then re-dispatched.
        # It must continue from cursor_pk (append), not rewrite the header.
        job = _job(export_format="csv")
        real_refresh = SnapExportJob.refresh_from_db
        state = {"n": 0}

        def refresh(self, *a, **k):
            real_refresh(self, *a, **k)
            state["n"] += 1
            if state["n"] == 2:  # cancel just before the 2nd chunk
                SnapExportJob.objects.filter(pk=self.pk).update(status="cancelled")
                real_refresh(self, *a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SnapExportJob, "refresh_from_db", refresh)
            exporting.run_export_job(job.pk)   # writes chunk 1, then stops
        job.refresh_from_db()
        assert job.status == "cancelled" and job.processed_rows == 2
        assert job.cursor_pk and int(job.cursor_pk) > 0

        # Operator retry: reset to pending and re-run — it resumes, not restarts.
        SnapExportJob.objects.filter(pk=job.pk).update(status="pending")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed" and job.processed_rows == 5
        lines = open(exporting.output_path(job)).read().strip().splitlines()
        assert len(lines) == 6                          # header + 5 data rows
        assert lines[0].startswith("id,")               # header written once…
        assert not any(l.startswith("id,category") for l in lines[1:])  # …not repeated

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_crash_between_flush_and_checkpoint_no_duplicate(self, products):
        # Torn write: bytes for the 2nd chunk are flushed to the file but the
        # (cursor_pk, cursor_bytes) checkpoint save never lands. On resume the
        # uncheckpointed tail must be discarded — no duplicated rows.
        job = _job(export_format="csv")
        real_save = SnapExportJob.save
        state = {"checkpoints": 0}

        def failing_save(self, *a, **k):
            fields = k.get("update_fields") or []
            if "cursor_pk" in fields:
                state["checkpoints"] += 1
                if state["checkpoints"] == 2:  # crash on the 2nd chunk's checkpoint
                    raise RuntimeError("crash after fsync, before checkpoint")
            return real_save(self, *a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SnapExportJob, "save", failing_save)
            exporting.run_export_job(job.pk)   # crashes inside _run → marked failed
        job.refresh_from_db()
        assert job.status == "failed"
        # File holds header + 4 flushed rows, but cursor only reached row 2.
        assert job.processed_rows == 2

        # Operator retry (failed is claimable): resume must not duplicate.
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed" and job.processed_rows == 5
        lines = open(exporting.output_path(job)).read().strip().splitlines()
        data_rows = lines[1:]
        assert len(data_rows) == 5                       # exactly 5, no dups
        assert len(set(data_rows)) == 5

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_crash_before_first_checkpoint_discards_stale_partial(self, products):
        # Crash before *any* checkpoint ever lands: the local working file has
        # a header (and maybe a first chunk) flushed to it, but cursor_pk is
        # still blank in the DB. On retry there is nothing to resume from, so
        # the stale partial must be discarded and the export restarted clean.
        job = _job(export_format="csv")
        real_save = SnapExportJob.save

        def failing_save(self, *a, **k):
            fields = k.get("update_fields") or []
            if "cursor_pk" in fields:
                raise RuntimeError("crash before the very first checkpoint")
            return real_save(self, *a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SnapExportJob, "save", failing_save)
            exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "failed" and job.cursor_pk == ""
        assert os.path.exists(exporting.output_path(job))  # stale header on disk

        exporting.run_export_job(job.pk)  # retry: must discard the stale file
        job.refresh_from_db()
        assert job.status == "completed" and job.processed_rows == 5
        lines = open(exporting.output_path(job)).read().strip().splitlines()
        assert len(lines) == 6 and len(set(lines[1:])) == 5

    def test_resume_with_missing_local_file_restarts_clean(self, products):
        # Cross-node / ephemeral-storage retry: cursor_pk is set in the DB from
        # a prior attempt, but the local working file it refers to does not
        # exist on *this* worker (different node, wiped ephemeral volume, ...).
        # Blindly trusting the stale cursor while opening a fresh ("wb") file
        # would silently skip every row up to that cursor — a corrupted export
        # reported as "completed". It must instead restart from scratch.
        job = _job(export_format="csv")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed" and job.processed_rows == 5
        stale_cursor = job.cursor_pk
        assert stale_cursor and int(stale_cursor) > 0

        # Simulate landing on a worker with no local copy of the working file,
        # while the DB still remembers a cursor from the "other" worker.
        os.remove(exporting.output_path(job))
        SnapExportJob.objects.filter(pk=job.pk).update(status="pending")

        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        lines = open(exporting.output_path(job)).read().strip().splitlines()
        data_rows = lines[1:]
        # All 5 rows present exactly once — nothing skipped because of the
        # stale cursor, nothing duplicated.
        assert len(data_rows) == 5
        names = sorted(r.split(",")[2] for r in data_rows)
        assert names == ["P0", "P1", "P2", "P3", "P4"]

    @override_settings(SNAPADMIN_EXPORT_STORAGE="tests.test_export.AltExportStorage")
    def test_republish_overwrites_stale_copy_in_storage(self, products):
        # A job re-dispatched after already completing (e.g. an operator retry
        # racing a late redelivery) republishes into the configured storage,
        # which already holds the file from the first run — it must overwrite
        # the stale copy rather than erroring or leaving two versions behind.
        job = _job(export_format="csv")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"

        SnapExportJob.objects.filter(pk=job.pk).update(status="pending")
        exporting.run_export_job(job.pk)  # re-publishes over the existing copy
        job.refresh_from_db()
        assert job.status == "completed"
        storage = exporting.get_export_storage()
        with storage.open(exporting.export_file_name(job), "rb") as fh:
            assert fh.read().count(b"\n") == 6

    def test_already_processing_job_is_not_double_run(self, products):
        # Single-flight: a job another worker already holds (processing) is left
        # untouched — no file written, no progress.
        job = _job(status="processing")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "processing" and job.processed_rows == 0
        assert not os.path.exists(exporting.output_path(job))

    def test_completed_job_is_not_rerun(self, products):
        job = _job(status="completed", processed_rows=5, total_rows=5)
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed" and job.processed_rows == 5

    def test_missing_job_is_a_noop(self, db):
        import uuid
        exporting.run_export_job(uuid.uuid4())  # no such job → returns quietly

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_concurrent_second_call_is_rejected(self, products):
        # A second dispatch fires mid-run (job already processing). Its atomic
        # claim loses, so it bails without interleaving writes into the file.
        job = _job()
        real_refresh = SnapExportJob.refresh_from_db
        state = {"reentered": False}

        def refresh(self, *a, **k):
            real_refresh(self, *a, **k)
            if not state["reentered"]:
                state["reentered"] = True
                exporting.run_export_job(self.pk)  # rejected: status is processing

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SnapExportJob, "refresh_from_db", refresh)
            exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert state["reentered"] and job.status == "completed"
        lines = open(exporting.output_path(job)).read().strip().splitlines()
        assert len(lines) == 6  # header + 5 rows, not corrupted/duplicated

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_pk_cursor_survives_concurrent_delete(self, products):
        # Delete an already-exported row (pk below the cursor) between chunks.
        # A pk__gt cursor must not let the OFFSET drift and skip a later row.
        from demo.apps.shop.models import Product
        job = _job()
        exported_names: set[str] = set()
        real_refresh = SnapExportJob.refresh_from_db
        state = {"n": 0}

        def refresh(self, *a, **k):
            real_refresh(self, *a, **k)
            state["n"] += 1
            if state["n"] == 2:  # after chunk 1 (P0, P1) is written
                Product.objects.filter(name="P0").delete()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SnapExportJob, "refresh_from_db", refresh)
            exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        rows = open(exporting.output_path(job)).read().strip().splitlines()[1:]
        names = sorted(r.split(",")[2] for r in rows)
        # Every remaining row exported exactly once — nothing skipped by the
        # delete of P0 (which an OFFSET slice would have dropped P2 for).
        assert names == ["P0", "P1", "P2", "P3", "P4"]

    @override_settings(SNAPADMIN_EXPORT_STORAGE="tests.test_export.AltExportStorage")
    def test_export_publishes_to_configured_storage(self, products):
        # The finished file is published into the configured storage, not just
        # left on the worker's local disk.
        job = _job(export_format="csv")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        storage = exporting.get_export_storage()
        name = exporting.export_file_name(job)
        assert storage.exists(name)
        with storage.open(name, "rb") as fh:
            body = fh.read()
        assert body.startswith(b"id,") and body.count(b"\n") == 6

    @override_settings(SNAPADMIN_EXPORT_STORAGE="tests.test_export.NoPathExportStorage")
    def test_export_publishes_to_pathless_storage(self, products):
        # A backend without a local path (S3/GCS-like) still round-trips.
        job = _job(export_format="csv")
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        storage = exporting.get_export_storage()
        with storage.open(exporting.export_file_name(job), "rb") as fh:
            assert fh.read().startswith(b"id,")

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_rows_deleted_mid_run_stops_cleanly(self, products, monkeypatch):
        # Concurrent deletion after the row count: the next slice comes back
        # empty and the writer breaks out rather than looping forever.
        from demo.apps.shop.models import Product
        job = _job()
        real_refresh = SnapExportJob.refresh_from_db

        def refresh(self, *a, **k):
            real_refresh(self, *a, **k)
            Product.objects.exclude(name__in=["P0", "P1"]).delete()

        monkeypatch.setattr(SnapExportJob, "refresh_from_db", refresh)
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        assert job.processed_rows == 2  # only the first chunk survived

    def test_failure_marks_failed(self, products, monkeypatch):
        job = _job()

        def boom(self):
            raise RuntimeError("disk full")
        monkeypatch.setattr("snapadmin.exporting._run", boom)
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "failed"
        assert "disk full" in job.error


# ── PII masking (#SEC6) ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestExportMasksPii:
    CUST = {"demo.Customer": ["email", "first_name"]}

    @staticmethod
    def _seed_customer():
        from demo.apps.shop.models import Customer
        Customer.objects.create(
            first_name="Alice", last_name="Smith", email="alice@example.com",
            origin="status_a", active=True,
        )

    @staticmethod
    def _first_row(job):
        line = open(exporting.output_path(job)).read().strip().splitlines()[0]
        return json.loads(line)

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_masks_for_unprivileged_requester(self, regular_user):
        self._seed_customer()
        job = SnapExportJob.objects.create(
            app_label="demo", model="Customer", export_format="json", requested_by=regular_user,
        )
        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        row = self._first_row(job)
        assert row["email"] == "a***@example.com"
        assert row["first_name"] == "*****"  # "Alice" (5 chars) fully starred
        assert row["last_name"] == "Smith"  # not masked

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_raw_for_privileged_requester(self, admin_user):
        self._seed_customer()
        job = SnapExportJob.objects.create(
            app_label="demo", model="Customer", export_format="json", requested_by=admin_user,
        )
        exporting.run_export_job(job.pk)
        row = self._first_row(job)
        assert row["email"] == "alice@example.com"
        assert row["first_name"] == "Alice"

    @override_settings(SNAPADMIN_MASKED_FIELDS=CUST)
    def test_no_requester_masks_fail_closed(self):
        # requested_by can be None (never set, or SET_NULL after the user was
        # deleted) — must default to masked, not raw.
        self._seed_customer()
        job = SnapExportJob.objects.create(
            app_label="demo", model="Customer", export_format="json", requested_by=None,
        )
        exporting.run_export_job(job.pk)
        row = self._first_row(job)
        assert row["email"] == "a***@example.com"

    def test_unconfigured_model_untouched(self, regular_user):
        self._seed_customer()
        job = SnapExportJob.objects.create(
            app_label="demo", model="Customer", export_format="json", requested_by=regular_user,
        )
        exporting.run_export_job(job.pk)
        row = self._first_row(job)
        assert row["email"] == "alice@example.com"  # no SNAPADMIN_MASKED_FIELDS set


# ── config helpers ───────────────────────────────────────────────────────────

class TestConfig:
    def test_enabled_default(self):
        assert exporting.export_enabled() is True

    @override_settings(SNAPADMIN_EXPORT_ENABLED=False)
    def test_disabled(self):
        assert exporting.export_enabled() is False

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=0)
    def test_chunk_size_min_one(self):
        assert exporting.export_chunk_size() == 1

    def test_export_dir_falls_back_to_media_root(self, tmp_path):
        with override_settings(SNAPADMIN_EXPORT_DIR="", MEDIA_ROOT=str(tmp_path)):
            path = exporting.export_dir()
        assert path.endswith("snapadmin_exports")
        assert os.path.isdir(path)

    def test_default_storage_is_local_filesystem(self):
        storage = exporting.get_export_storage()
        assert isinstance(storage, FileSystemStorage)
        assert os.path.abspath(storage.location) == os.path.abspath(exporting.export_dir())

    @override_settings(SNAPADMIN_EXPORT_STORAGE="tests.test_export.AltExportStorage")
    def test_configured_storage_class_is_used(self):
        assert isinstance(exporting.get_export_storage(), AltExportStorage)

    def test_configured_storage_accepts_a_class_object(self):
        with override_settings(SNAPADMIN_EXPORT_STORAGE=AltExportStorage):
            assert isinstance(exporting.get_export_storage(), AltExportStorage)


# ── model-view permission gate ───────────────────────────────────────────────

@pytest.mark.django_db
class TestCanView:
    def test_denied_for_user_without_permission(self, db, regular_user):
        from rest_framework.test import APIClient
        from snapadmin.models import APIToken
        token = APIToken.create_for_user(regular_user, "Reg")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        r = client.post("/api/exports/", {"app_label": "demo", "model": "Product"}, format="json")
        assert r.status_code == 400  # no demo.view_product permission

    def test_can_view_session_user(self, admin_user, regular_user):
        from types import SimpleNamespace
        from snapadmin.api.exports import _can_view
        # Non-token request (session/JWT): falls back to Django model perms.
        assert _can_view(SimpleNamespace(auth=None, user=admin_user), "demo", "Product") is True
        assert _can_view(SimpleNamespace(auth=None, user=regular_user), "demo", "Product") is False

    def test_can_view_anonymous(self):
        from types import SimpleNamespace
        from django.contrib.auth.models import AnonymousUser
        from snapadmin.api.exports import _can_view
        assert _can_view(SimpleNamespace(auth=None, user=AnonymousUser()), "demo", "Product") is False


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestExportApi:
    def test_create_runs_and_completes_eagerly(self, auth_client, products):
        r = auth_client.post("/api/exports/", {"app_label": "demo", "model": "Product",
                                               "export_format": "csv"}, format="json")
        assert r.status_code == 201, r.content
        data = r.json()
        assert data["status"] == "completed"       # eager Celery ran it inline
        assert data["processed_rows"] == 5
        assert data["progress_percent"] == 100
        assert data["download_url"].endswith(f"/api/exports/{data['id']}/download/")

    def test_retrieve_progress(self, auth_client, products):
        create = auth_client.post("/api/exports/", {"app_label": "demo", "model": "Product"},
                                  format="json").json()
        r = auth_client.get(f"/api/exports/{create['id']}/")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_download_finished(self, auth_client, products):
        job_id = auth_client.post("/api/exports/", {"app_label": "demo", "model": "Product"},
                                  format="json").json()["id"]
        r = auth_client.get(f"/api/exports/{job_id}/download/")
        assert r.status_code == 200
        body = b"".join(r.streaming_content)
        assert body.startswith(b"id,")

    @override_settings(SNAPADMIN_EXPORT_STORAGE="tests.test_export.AltExportStorage")
    def test_download_reads_through_configured_storage(self, auth_client, products):
        # Round-trip: the worker publishes to the configured storage and the
        # download endpoint serves it back through the same abstraction.
        job_id = auth_client.post("/api/exports/", {"app_label": "demo", "model": "Product"},
                                  format="json").json()["id"]
        r = auth_client.get(f"/api/exports/{job_id}/download/")
        assert r.status_code == 200
        assert b"".join(r.streaming_content).startswith(b"id,")

    def test_download_not_ready(self, auth_client, products):
        job = _job(status="processing")
        r = auth_client.get(f"/api/exports/{job.pk}/download/")
        assert r.status_code == 409

    def test_download_file_gone(self, auth_client, products):
        job = _job(status="completed", file_name="missing.csv")
        r = auth_client.get(f"/api/exports/{job.pk}/download/")
        assert r.status_code == 410

    def test_cancel_pending(self, auth_client, products):
        job = _job(status="pending")
        r = auth_client.post(f"/api/exports/{job.pk}/cancel/")
        assert r.status_code == 200
        job.refresh_from_db()
        assert job.status == "cancelled"

    def test_cancel_finished_conflict(self, auth_client, products):
        job = _job(status="completed")
        r = auth_client.post(f"/api/exports/{job.pk}/cancel/")
        assert r.status_code == 409

    def test_unknown_model_rejected(self, auth_client):
        r = auth_client.post("/api/exports/", {"app_label": "demo", "model": "Nope"}, format="json")
        assert r.status_code == 400

    def test_non_snapmodel_rejected(self, auth_client):
        r = auth_client.post("/api/exports/", {"app_label": "auth", "model": "User"}, format="json")
        assert r.status_code == 400

    @override_settings(SNAPADMIN_EXPORT_ENABLED=False)
    def test_disabled_returns_403(self, auth_client, products):
        r = auth_client.post("/api/exports/", {"app_label": "demo", "model": "Product"}, format="json")
        assert r.status_code == 403

    def test_jobs_are_private(self, db, regular_user):
        # A non-superuser only sees their own jobs. (auth_client's token belongs
        # to a superuser, who sees all — so use a regular user's token here.)
        from rest_framework.test import APIClient
        from snapadmin.models import APIToken
        token = APIToken.create_for_user(regular_user, "Reg")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.token_key}")
        other = _job(status="completed")  # requested_by=None → not theirs
        assert client.get(f"/api/exports/{other.pk}/").status_code == 404

    def test_anon_denied(self, anon_client):
        r = anon_client.post("/api/exports/", {"app_label": "demo", "model": "Product"}, format="json")
        assert r.status_code in (401, 403)


# ── filters allowlist (#SEC5) ────────────────────────────────────────────────

class TestAllowedFiltersForModel:
    """Unit coverage of the own-field/lookup allowlist dispatch."""

    def test_own_fields_only(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _allowed_filters_for_model
        allowed = _allowed_filters_for_model(Product)
        # Relations requiring a join are never allowlisted.
        assert "tags" not in allowed
        assert "orderitem" not in allowed
        # FK is only reachable via its "<name>_id" column, not the relation name.
        assert "category" not in allowed
        assert allowed["category_id"] == {"exact", "in"}

    def test_text_field_lookups(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _allowed_filters_for_model
        assert _allowed_filters_for_model(Product)["name"] == {"exact", "in", "icontains"}

    def test_boolean_field_lookups(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _allowed_filters_for_model
        assert _allowed_filters_for_model(Product)["available"] == {"exact"}

    def test_numeric_field_lookups(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _allowed_filters_for_model
        assert _allowed_filters_for_model(Product)["price"] == {"exact", "in", "gte", "lte"}

    def test_datetime_field_lookups(self):
        from demo.apps.shop.models import Order
        from snapadmin.api.exports import _allowed_filters_for_model
        assert _allowed_filters_for_model(Order)["created_at"] == {"exact", "in", "gte", "lte"}

    def test_uuid_field_lookups(self):
        from demo.apps.shop.models import Showcase
        from snapadmin.api.exports import _allowed_filters_for_model
        assert _allowed_filters_for_model(Showcase)["uuid_field"] == {"exact", "in"}


class TestValidateExportFilters:
    """Unit coverage of the key/lookup rejection logic."""

    def test_relation_traversal_rejected(self):
        from demo.apps.shop.models import Order
        from snapadmin.api.exports import _validate_export_filters
        with pytest.raises(serializers.ValidationError) as exc:
            _validate_export_filters(Order, {"customer__email": "x"})
        assert "customer__email" in str(exc.value)

    def test_unknown_field_rejected(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _validate_export_filters
        with pytest.raises(serializers.ValidationError) as exc:
            _validate_export_filters(Product, {"nonexistent_field": "x"})
        assert "nonexistent_field" in str(exc.value)

    def test_disallowed_lookup_rejected(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _validate_export_filters
        with pytest.raises(serializers.ValidationError) as exc:
            _validate_export_filters(Product, {"name__gte": "x"})
        assert "name__gte" in str(exc.value)

    def test_valid_filters_pass(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _validate_export_filters
        _validate_export_filters(
            Product, {"name__icontains": "P", "price__gte": "1", "available": True,
                      "category_id": 1},
        )  # must not raise

    def test_masked_field_rejected(self):
        # #SEC6: an otherwise-allowed filter on a masked field is still an
        # oracle (job.total_rows leaks a match/no-match) — reject it too.
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _validate_export_filters
        with pytest.raises(serializers.ValidationError) as exc:
            _validate_export_filters(Product, {"name": "x"}, {"name"})
        assert "name" in str(exc.value)

    def test_masked_field_empty_set_is_a_noop(self):
        from demo.apps.shop.models import Product
        from snapadmin.api.exports import _validate_export_filters
        _validate_export_filters(Product, {"name": "x"})  # default masked_fields=frozenset()


@pytest.mark.django_db
class TestExportFilterValidationApi:
    """POST /api/exports/ end-to-end filter allowlist enforcement."""

    def test_relation_traversal_rejected(self, auth_client, products):
        from demo.apps.shop.models import Category
        Category.objects.create(name="C1", slug="c1")
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"category__name": "C1"}},
            format="json",
        )
        assert r.status_code == 400
        assert "category__name" in str(r.json())

    def test_m2m_relation_rejected(self, auth_client, products):
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"tags__name": "x"}},
            format="json",
        )
        assert r.status_code == 400
        assert "tags__name" in str(r.json())

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_masked_field_rejected_for_unprivileged(self, regular_user, products):
        from django.contrib.auth.models import Permission
        from django.contrib.auth import get_user_model
        from snapadmin.models import APIToken
        from rest_framework.test import APIClient
        regular_user.user_permissions.add(Permission.objects.get(codename="view_product"))
        fresh = get_user_model().objects.get(pk=regular_user.pk)
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Token {APIToken.create_for_user(fresh, 'Reg').token_key}"
        )
        r = client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"name": "P1"}},
            format="json",
        )
        assert r.status_code == 400
        assert "name" in str(r.json())

    @override_settings(SNAPADMIN_MASKED_FIELDS={"demo.Product": ["name"]})
    def test_masked_field_still_applies_for_privileged(self, auth_client, products):
        # auth_client's token belongs to a superuser (PII-privileged).
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"name": "P1"}},
            format="json",
        )
        assert r.status_code == 201

    def test_unknown_field_rejected(self, auth_client, products):
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"nonexistent_field": "x"}},
            format="json",
        )
        assert r.status_code == 400
        assert "nonexistent_field" in str(r.json())

    def test_disallowed_lookup_rejected(self, auth_client, products):
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"price__icontains": "1"}},
            format="json",
        )
        assert r.status_code == 400
        assert "price__icontains" in str(r.json())

    def test_valid_char_filter_applied(self, auth_client, products):
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"name__icontains": "P1"}},
            format="json",
        )
        assert r.status_code == 201, r.content
        assert r.json()["processed_rows"] == 1

    def test_valid_number_filter_applied(self, auth_client, products):
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"price__gte": "3"}},
            format="json",
        )
        assert r.status_code == 201, r.content
        assert r.json()["processed_rows"] == 2  # price 3 and 4

    def test_valid_boolean_filter_applied(self, auth_client, products):
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Product", "filters": {"available": True}},
            format="json",
        )
        assert r.status_code == 201, r.content
        assert r.json()["processed_rows"] == 5  # all products default available=True

    def test_valid_fk_id_filter_applied(self, auth_client):
        from demo.apps.shop.models import Customer, Order
        c1 = Customer.objects.create(first_name="A", last_name="A", email="a@example.com")
        c2 = Customer.objects.create(first_name="B", last_name="B", email="b@example.com")
        Order.objects.create(customer=c1, total="10.00")
        Order.objects.create(customer=c1, total="20.00")
        Order.objects.create(customer=c2, total="30.00")
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Order", "filters": {"customer_id": c1.pk}},
            format="json",
        )
        assert r.status_code == 201, r.content
        assert r.json()["processed_rows"] == 2

    def test_valid_date_filter_applied(self, auth_client):
        from demo.apps.shop.models import Customer, Order
        customer = Customer.objects.create(first_name="A", last_name="A", email="a@example.com")
        Order.objects.create(customer=customer, total="10.00")
        cutoff = (timezone.now() - timezone.timedelta(days=1)).isoformat()
        r = auth_client.post(
            "/api/exports/",
            {"app_label": "demo", "model": "Order", "filters": {"created_at__gte": cutoff}},
            format="json",
        )
        assert r.status_code == 201, r.content
        assert r.json()["processed_rows"] == 1
