"""
snapadmin/tasks.py

Celery background tasks for SnapAdmin.

This module lives at ``snapadmin/tasks.py`` (not under a subpackage) so that a
project's standard ``app.autodiscover_tasks()`` call registers every task
automatically — Celery scans ``<app>/tasks.py`` for each ``INSTALLED_APPS``
entry, and ``snapadmin`` is such an entry.

All tasks are namespaced under ``snapadmin.*`` (e.g. ``snapadmin.run_export``).
Reference them by that name in ``CELERY_BEAT_SCHEDULE``.
"""

from celery import shared_task
from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger("snapadmin.tasks")


@shared_task(bind=True, name="snapadmin.purge_expired_tokens")
def purge_expired_tokens(self):
    from snapadmin.models import APIToken

    cutoff = timezone.now()
    deleted_qs = APIToken.objects.filter(
        expiration_date__lt=cutoff,
        expiration_date__isnull=False,
    )
    count, _ = deleted_qs.delete()

    logger.info("expired_tokens_purged", count=count, cutoff=cutoff.isoformat())
    return {"deleted": count, "cutoff": cutoff.isoformat()}


@shared_task(bind=True, name="snapadmin.purge_expired_data")
def purge_expired_data(self):
    """
    GDPR data retention cleanup.

    Scans all registered SnapModel subclasses for a non-None
    data_retention_days attribute and deletes records older than that limit.
    Returns a summary dict with per-model deleted counts. A model whose purge
    only partially succeeded (e.g. the database delete went through but a
    secondary store such as Elasticsearch could not be cleared — see
    ``SnapModel.purge_expired`` / ``SnapPurgeError``) is reported under
    ``errors``, not ``purged`` — it must not be mistaken for a clean purge.
    """
    from django.apps import apps
    from snapadmin.models import SnapModel

    summary: dict[str, int] = {}
    errors: dict[str, str] = {}
    now = timezone.now()

    for model in apps.get_models():
        if not (isinstance(model, type) and issubclass(model, SnapModel) and model is not SnapModel):
            continue

        retention_days = getattr(model, "data_retention_days", None)
        if not retention_days or retention_days <= 0:
            continue

        label = f"{model._meta.app_label}.{model.__name__}"

        try:
            count = model.purge_expired(now=now)
            summary[label] = count
            logger.info("purge_expired_data_deleted", model=label, count=count)
        except Exception as exc:
            errors[label] = str(exc)
            logger.error("purge_expired_data_error", model=label, error=str(exc))

    return {"purged": summary, "total": sum(summary.values()), "errors": errors}


@shared_task(bind=True, name="snapadmin.send_error_digest")
def send_error_digest(self, hours: int = 24):
    """
    Daily grouped error digest email (schedule via Celery Beat; the digest
    hour/minute is whatever crontab the deployment configures).
    """
    from snapadmin.monitoring import send_error_digest as send_digest

    summary = send_digest(hours=hours)
    logger.info("error_digest_task_finished", **summary)
    return summary


@shared_task(bind=True, name="snapadmin.run_export", acks_late=True)
def run_export(self, job_id):
    """Run (or resume) a background CSV/JSON export job.

    ``acks_late`` + the job's resumable writer mean a worker restart re-runs the
    task and continues from the last persisted chunk instead of starting over.
    """
    from snapadmin.exporting import run_export_job

    run_export_job(job_id)
    logger.info("export_task_finished", job=str(job_id))
    return {"job_id": str(job_id)}


@shared_task(bind=True, name="snapadmin.run_es_reindex")
def run_es_reindex(self, chunk_size: int = 500):
    """Bulk-reindex every ES-enabled SnapModel into Elasticsearch.

    The async counterpart of the ``snapadmin_reindex`` command and the
    ``POST /api/es/reindex/`` endpoint — dispatched by that endpoint when
    ``SNAPADMIN_REINDEX_API_ASYNC`` is on so a large reindex never blocks the
    request/worker thread.
    """
    from snapadmin.models import run_reindex

    summary = run_reindex(chunk_size=chunk_size)
    logger.info("es_reindex_task_finished", **{k: v for k, v in summary.items() if k != "results"})
    return summary


@shared_task(bind=True, name="snapadmin.run_db_backups")
def run_db_backups(self):
    """
    3-2-1 database backups: schedule this frequently (e.g. hourly) via Celery
    Beat — it dumps and ships only to destinations whose own interval
    (SNAPADMIN_BACKUP_*_EVERY_HOURS) has elapsed, so the beat cadence only
    bounds how promptly a due backup starts.
    """
    from snapadmin.backup import run_due_backups

    summary = run_due_backups()
    logger.info("db_backup_task_finished", **summary)
    return summary
