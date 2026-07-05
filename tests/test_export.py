"""
tests/test_export.py — async background export (issue #6)

Covers the SnapExportJob model (progress/ETA), the resumable/cancellable chunk
writer (snapadmin.exporting), and the /api/exports/ endpoints (create → poll →
download, cancel, permissions).
"""

import json
import os
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone

from snapadmin import exporting
from snapadmin.models import SnapExportJob


@pytest.fixture
def products(db):
    from demo.models import Product
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
        from demo.models import Product
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

    def test_resume_appends_from_offset(self, products):
        # Simulate a crash: first chunk written, processed_rows persisted, then
        # the writer re-runs and must append the rest (not rewrite the header).
        job = _job(export_format="csv")
        with override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2):
            # First partial run, cancelled after one chunk.
            SnapExportJob.objects.filter(pk=job.pk).update(status="pending")
            job.refresh_from_db()
        # Manually run just enough to leave a partial file:
        path = exporting.output_path(job)
        with open(path, "w", newline="") as fh:
            fh.write("id,category_id,name,price,available,description\n")
            fh.write("1,,P0,0,True,\n")
        job.total_rows = 5
        job.processed_rows = 1
        job.started_at = timezone.now()
        job.status = "processing"
        job.save()

        exporting.run_export_job(job.pk)
        job.refresh_from_db()
        assert job.status == "completed"
        lines = open(path).read().strip().splitlines()
        assert len(lines) == 6                       # header + 5 data rows
        assert lines[0].startswith("id,category_id")  # header written once…
        assert not any(l.startswith("id,category_id") for l in lines[1:])  # …not repeated

    @override_settings(SNAPADMIN_EXPORT_CHUNK_SIZE=2)
    def test_rows_deleted_mid_run_stops_cleanly(self, products, monkeypatch):
        # Concurrent deletion after the row count: the next slice comes back
        # empty and the writer breaks out rather than looping forever.
        from demo.models import Product
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
