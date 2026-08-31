"""
snapadmin/tasks.py

Celery background tasks for SnapAdmin.

This module lives at ``snapadmin/tasks.py`` (not under a subpackage) so that a
project's standard ``app.autodiscover_tasks()`` call registers every task
automatically — Celery scans ``<app>/tasks.py`` for each ``INSTALLED_APPS``
entry, and ``snapadmin`` is such an entry.

All tasks are namespaced under ``snapadmin.*`` (e.g. ``snapadmin.run_export``).
Reference them by that name in ``CELERY_BEAT_SCHEDULE``.

**Task-outcome convention.** Every scheduled task below except ``run_export``
(which already tracks its own success/failure on the ``SnapExportJob`` row)
returns a summary dict that always carries a ``status`` key with one of
exactly four values, plus a ``failed`` list (``[]`` when nothing failed), so
one monitoring rule covers all six:

- ``"ok"`` — the work ran and every unit of it succeeded. Logged at ``info``
  with the marker ``snapadmin_task_ok``.
- ``"partial"`` — the work ran but **some** units failed (``failed`` is
  non-empty). Never raises — retrying would redo the units that already
  succeeded. Logged at ``error`` with the marker ``snapadmin_task_partial``.
- ``"noop"`` — nothing was due, or there was nothing to do. Logged at
  ``info`` with the marker ``snapadmin_task_noop``.
- ``"disabled"`` — the feature is switched off (or unusably misconfigured,
  e.g. no alert recipients) by configuration. Logged at ``info`` with the
  marker ``snapadmin_task_disabled``.
- **Total failure raises** instead of returning — every unit failed, or the
  task could not start its work at all. A raised exception is the only thing
  Celery itself records as a task failure, which is what a task-status
  monitor watches; a return value that could still read as "it ran fine"
  would defeat the point. Logged at ``error`` with the marker
  ``snapadmin_task_failed`` right before raising.

Every existing key on each task's summary keeps its name and shape — this
convention is purely additive. **One documented monitoring rule covers all
six tasks: alert when ``status != "ok"``, page when the Celery task state is
``FAILURE``.** See docs/index.html#celery ("Task outcomes & monitoring").
"""

try:  # Celery is the ``[celery]`` extra — this module must import without it.
    from celery import shared_task
except ImportError:  # pragma: no cover - covered by executing this file with celery hidden
    from snapadmin.celery_compat import shared_task

from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger("snapadmin.tasks")


class ReindexError(Exception):
    """Raised by ``snapadmin.run_es_reindex`` when every attempted model failed."""


def _record_outcome(task_name: str, summary: dict, status: str, *, failed: list | None = None) -> dict:
    """Attach the shared ``status``/``failed`` keys to a task's summary dict
    and log it with the marker/level the outcome convention (module
    docstring above) prescribes. ``results`` is excluded from the log line —
    it can be large (one entry per destination/model) and is already logged
    in detail at the point each unit finishes.
    """
    failed = list(failed or [])
    summary = {**summary, "status": status, "failed": failed}
    marker = f"snapadmin_task_{status}"
    log = logger.error if status == "partial" else logger.info
    log(marker, task=task_name, **{k: v for k, v in summary.items() if k != "results"})
    return summary


def _log_task_failure(task_name: str, **fields) -> None:
    logger.error("snapadmin_task_failed", task=task_name, **fields)


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
    return _record_outcome(
        "purge_expired_tokens", {"deleted": count, "cutoff": cutoff.isoformat()}, "ok",
    )


@shared_task(bind=True, name="snapadmin.purge_expired_data")
def purge_expired_data(self):
    """
    GDPR data retention cleanup.

    Scans every registered SnapModel for a non-None ``data_retention_days``
    setting and deletes records older than that limit, then purges the
    ``SnapadminAuditLog`` table the same way against its own
    ``SNAPADMIN_AUDIT_RETENTION_DAYS`` (on, at 365 days, by default — the audit
    log is not a registered SnapModel, so it needs this explicit second step
    rather than being reachable by the loop above), then — when
    ``SNAPADMIN_EXPORT_RETENTION_DAYS`` is set (opt-in, unlike the two sweeps
    above) — finished export/reindex job rows and their published files (see
    ``snapadmin.exporting.purge_expired_export_jobs``), reported under the
    additive ``"export_jobs"`` key.
    Returns a summary dict with per-model deleted counts. A model whose purge
    only partially succeeded (e.g. the database delete went through but a
    secondary store such as Elasticsearch could not be cleared, or a
    ``data_retention_files`` storage object could not be removed — see
    ``SnapModel.purge_expired`` / ``SnapPurgeError``) is reported under
    ``errors``, not ``purged`` — it must not be mistaken for a clean purge.

    ``status`` is ``"noop"`` when no model has retention configured at all,
    ``"partial"`` when some (but not every) considered model's purge raised,
    and the task **raises** ``SnapPurgeError`` when every considered model's
    purge failed — see the module docstring's outcome convention.
    """
    from django.apps import apps
    from snapadmin.exporting import purge_expired_export_jobs
    from snapadmin.models import SnapadminAuditLog, SnapPurgeError
    from snapadmin.registry import get_model_meta, is_registered

    summary: dict[str, int] = {}
    errors: dict[str, str] = {}
    now = timezone.now()
    considered = 0

    for model in apps.get_models():
        # ``purge_expired`` is SnapModel's own — a plain model registered with
        # @snap_model gets no retention purge, so it is skipped here.
        if not (is_registered(model) and hasattr(model, "purge_expired")):
            continue

        retention_days = get_model_meta(model, "data_retention_days", None)
        if not retention_days or retention_days <= 0:
            continue

        considered += 1
        label = f"{model._meta.app_label}.{model.__name__}"

        try:
            count = model.purge_expired(now=now)
            summary[label] = count
            logger.info("purge_expired_data_deleted", model=label, count=count)
        except Exception as exc:
            errors[label] = str(exc)
            logger.error("purge_expired_data_error", model=label, error=str(exc))

    # The audit log is append-only and deliberately outside the general
    # SnapAdmin registry (see SnapadminAuditLog's docstring — registering it
    # would also expose it through the dynamic REST/GraphQL API), so it is not
    # swept by the apps.get_models() loop above. Its own retention setting
    # (SNAPADMIN_AUDIT_RETENTION_DAYS, on by default) is purged explicitly here
    # instead, through the same summary/errors this task already reports.
    if SnapadminAuditLog.data_retention_days() > 0:
        considered += 1
        label = f"snapadmin.{SnapadminAuditLog.__name__}"
        try:
            count = SnapadminAuditLog.purge_expired(now=now)
            summary[label] = count
            logger.info("purge_expired_data_deleted", model=label, count=count)
        except Exception as exc:
            errors[label] = str(exc)
            logger.error("purge_expired_data_error", model=label, error=str(exc))

    # Export/reindex job rows and their files (#RET2b) — opt-in via
    # SNAPADMIN_EXPORT_RETENTION_DAYS, unlike the two sweeps above. Reported
    # through the same summary/errors, plus the full breakdown under a new,
    # purely additive "export_jobs" key for whoever wants the detail.
    export_purge = purge_expired_export_jobs(now=now)
    if export_purge["enabled"]:
        considered += 1
        label = "snapadmin.export_jobs"
        if export_purge["failed"]:
            errors[label] = "; ".join(export_purge["failed"])
            logger.error("purge_expired_data_error", model=label, error=errors[label])
        else:
            count = sum(export_purge["jobs_deleted"].values()) + export_purge["orphan_files_deleted"]
            summary[label] = count
            logger.info("purge_expired_data_deleted", model=label, count=count)

    result = {
        "purged": summary, "total": sum(summary.values()), "errors": errors,
        "export_jobs": export_purge,
    }

    if considered == 0:
        return _record_outcome("purge_expired_data", result, "noop")
    if errors and len(errors) == considered:
        _log_task_failure("purge_expired_data", failed=list(errors))
        raise SnapPurgeError(f"Every model's data-retention purge failed: {', '.join(errors)}")
    if errors:
        return _record_outcome("purge_expired_data", result, "partial", failed=list(errors))
    return _record_outcome("purge_expired_data", result, "ok")


@shared_task(bind=True, name="snapadmin.send_error_digest")
def send_error_digest(self, hours: int = 24):
    """
    Daily grouped error digest email (schedule via Celery Beat; the digest
    hour/minute is whatever crontab the deployment configures).

    ``status`` is ``"disabled"`` when the digest is switched off or has no
    delivery recipients configured (a misconfigured digest must not read as
    a delivered one), ``"noop"`` when there was nothing to report, and the
    task **raises** ``AlertDeliveryError`` when every delivery channel failed
    — see the module docstring's outcome convention.
    """
    from snapadmin.alerts import AlertDeliveryError
    from snapadmin.monitoring import send_error_digest as send_digest

    summary = send_digest(hours=hours)
    reason = summary.get("reason")

    if summary.get("sent"):
        return _record_outcome("send_error_digest", summary, "ok")
    if reason in ("disabled", "no_recipients"):
        return _record_outcome("send_error_digest", summary, "disabled")
    if reason == "no_errors":
        return _record_outcome("send_error_digest", summary, "noop")
    if reason == "delivery_failed":
        _log_task_failure("send_error_digest", **summary)
        raise AlertDeliveryError(f"Error digest could not be delivered to any channel: {summary}")


@shared_task(bind=True, name="snapadmin.run_export", acks_late=True)
def run_export(self, job_id):
    """Run (or resume) a background CSV/JSON export job.

    ``acks_late`` + the job's resumable writer mean a worker restart re-runs the
    task and continues from the last persisted chunk instead of starting over.
    ``SnapExportJob`` already tracks its own success/failure on the row, so
    this task sits outside the outcome convention above — an exception from
    ``run_export_job`` already propagates and marks the Celery task FAILURE.
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

    ``status`` is ``"noop"`` when no model was actually attempted (every
    reindexable model was skipped, e.g. Elasticsearch is unavailable),
    ``"partial"`` when some (but not every) attempted model errored —
    including a model whose bulk index call reported rejected documents —
    and the task **raises** ``ReindexError`` when every attempted model
    failed outright.
    """
    from snapadmin.models import run_reindex

    summary = run_reindex(chunk_size=chunk_size)
    results = summary.get("results", {})
    attempted = {label: data for label, data in results.items() if not data.get("skipped")}
    errored = [label for label, data in attempted.items() if data.get("errors")]

    if not attempted:
        return _record_outcome("run_es_reindex", summary, "noop")
    if errored and len(errored) == len(attempted):
        _log_task_failure("run_es_reindex", failed=errored)
        raise ReindexError(f"Every reindexed model failed: {', '.join(errored)}")
    if errored:
        return _record_outcome("run_es_reindex", summary, "partial", failed=errored)
    return _record_outcome("run_es_reindex", summary, "ok")


@shared_task(bind=True, name="snapadmin.send_health_alert")
def send_health_alert(self):
    """Probe subsystem health and email the recipients when one is down.

    Schedule this via Celery Beat (e.g. every few minutes). A cache-based cooldown
    means a persistent outage emails at most once per
    ``SNAPADMIN_HEALTH_ALERT_COOLDOWN_MINUTES``; a recovery re-arms it immediately.

    ``status`` is ``"ok"`` for both "everything healthy" and "an alert was
    sent" (the task did its job either way), ``"disabled"`` when alerting is
    switched off or misconfigured (no recipients), ``"noop"`` while a
    persistent outage's cooldown suppresses a repeat alert, and the task
    **raises** ``AlertDeliveryError`` when every delivery channel failed to
    send an alert that was due.
    """
    from snapadmin.alerts import AlertDeliveryError
    from snapadmin.health import send_health_alert as run

    summary = run()
    reason = summary.get("reason")

    if summary.get("sent") or reason == "healthy":
        return _record_outcome("send_health_alert", summary, "ok")
    if reason in ("disabled", "no_recipients"):
        return _record_outcome("send_health_alert", summary, "disabled")
    if reason == "cooldown":
        return _record_outcome("send_health_alert", summary, "noop")
    if reason == "delivery_failed":
        _log_task_failure("send_health_alert", **summary)
        raise AlertDeliveryError(f"Health alert could not be delivered to any channel: {summary}")


@shared_task(bind=True, name="snapadmin.run_db_backups")
def run_db_backups(self):
    """
    3-2-1 database backups: schedule this frequently (e.g. hourly) via Celery
    Beat — it dumps and ships only to destinations whose own interval
    (SNAPADMIN_BACKUP_*_EVERY_HOURS) has elapsed, so the beat cadence only
    bounds how promptly a due backup starts.

    ``snapadmin.backup.run_due_backups`` already applies the outcome
    convention (``status``/``failed``) and logs the marker itself — including
    raising ``BackupError`` when every destination failed — so this task is a
    thin pass-through.
    """
    from snapadmin.backup import run_due_backups

    return run_due_backups()
