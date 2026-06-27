"""
snapadmin/api/tasks.py

Celery background tasks for the API module.
"""

from celery import shared_task
from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger("snapadmin.api.tasks")


@shared_task(bind=True, name="api.tasks.purge_expired_tokens")
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


@shared_task(bind=True, name="api.tasks.purge_expired_data")
def purge_expired_data(self):
    """
    GDPR data retention cleanup.

    Scans all registered SnapModel subclasses for a non-None
    data_retention_days attribute and deletes records older than that limit.
    Returns a summary dict with per-model deleted counts.
    """
    from django.apps import apps
    from snapadmin.models import SnapModel

    summary: dict[str, int] = {}
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
            logger.error("purge_expired_data_error", model=label, error=str(exc))

    return {"purged": summary, "total": sum(summary.values())}
