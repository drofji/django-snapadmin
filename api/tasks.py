"""
api/tasks.py

Celery background tasks for the API module.
"""

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("snapadmin.api.tasks")


@shared_task(bind=True, name="api.tasks.purge_expired_tokens")
def purge_expired_tokens(self):
    """
    Delete all APIToken records whose expiration_date has passed.

    This task runs nightly via Celery Beat. Expired tokens are hard-deleted
    to keep the tokens table lean. Inactive tokens (is_active=False) are
    intentionally kept for audit purposes.

    Returns:
        dict: Summary with the count of deleted tokens.
    """
    from api.models import APIToken

    cutoff = timezone.now()
    deleted_qs = APIToken.objects.filter(
        expiration_date__lt=cutoff,
        expiration_date__isnull=False,
    )
    count, _ = deleted_qs.delete()

    logger.info("expired_tokens_purged", count=count, cutoff=cutoff.isoformat())
    return {"deleted": count, "cutoff": cutoff.isoformat()}
