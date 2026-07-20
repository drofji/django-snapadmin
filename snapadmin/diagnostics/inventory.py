"""
Models & security collector for ``snapadmin_info``.

Lists every registered concrete SnapModel with its capability flags (ES storage mode, retention,
API write-allowlist, whether any of its fields is PII-masked), plus API-token counts and the size of
the global masked-field set. Token counts are best-effort — if the database is unreachable they are
simply omitted rather than failing the whole report. Token *values* are never exposed (only counts).
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from snapadmin.diagnostics.registry import register
from snapadmin.models import EsStorageMode, SnapModel


def _model_items(masked: set) -> list[dict]:
    items: list[dict] = []
    for model in apps.get_models():
        if not SnapModel.is_concrete_subclass(model):
            continue
        field_names = {field.name for field in model._meta.get_fields()}
        items.append(
            {
                "model": f"{model._meta.app_label}.{model.__name__}",
                "es_mode": getattr(model, "es_storage_mode", EsStorageMode.DB_ONLY).name,
                "retention_days": getattr(model, "data_retention_days", None),
                "write_restricted": getattr(model, "api_write_fields", None) is not None,
                "masked": bool(masked & field_names),
            }
        )
    return sorted(items, key=lambda item: item["model"])


def _token_counts() -> dict:
    from snapadmin.models import APIToken

    now = timezone.now()
    tokens = APIToken.objects
    return {
        "total": tokens.count(),
        "active": tokens.filter(is_active=True)
        .filter(Q(expiration_date__isnull=True) | Q(expiration_date__gte=now))
        .count(),
        "expired": tokens.filter(expiration_date__isnull=False, expiration_date__lt=now).count(),
    }


@register("inventory", title="Models & Security", icon="📊", order=50)
def collect(*, verbose: bool) -> dict:
    """Collect the models & security section."""
    masked = set(getattr(settings, "SNAPADMIN_MASKED_FIELDS", []) or [])
    items = _model_items(masked)
    data: dict = {
        "models": {"total": len(items), "items": items},
        "masked_fields": len(masked),
    }
    try:
        data["tokens"] = _token_counts()
    except Exception:
        pass
    return data
