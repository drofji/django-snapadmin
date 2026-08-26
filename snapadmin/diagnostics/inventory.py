"""
Models & security collector for ``snapadmin_info``.

Lists every registered concrete SnapModel with its capability flags (ES storage mode, retention,
API write-allowlist, whether any of its fields is PII-masked), which registration door it came
through and which capabilities that door leaves structurally unreachable for it (#PAR1e), plus
API-token counts and the size of the global masked-field set. Token counts are best-effort — if the
database is unreachable they are simply omitted rather than failing the whole report. Token
*values* are never exposed (only counts).
"""

from __future__ import annotations

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

from snapadmin.conf import get_setting
from snapadmin.diagnostics.registry import register
from snapadmin.models import EsStorageMode
from snapadmin.registry import get_model_meta, is_registered

#: Capability name -> the ``SnapModel`` attribute whose presence means the capability is
#: *reachable at all* for a model, regardless of whether it is actually turned on. Each is
#: defined directly on ``SnapModel`` and never attached to a plain model registered through
#: ``@snap_model`` (#RFC1b) — the exact markers the runtime gates already check to skip a
#: decorated model (``apps.py``'s ``post_migrate`` ES-mapping sweep, the retention-purge sweep,
#: ``SnapModel.register_admin`` itself). Reused here rather than re-derived, per #RFC1g's own
#: verdict list of what a decorated model does not get, so this self-corrects the day #RFC1g1-3
#: attach any of them to a decorated model too — no further change needed here.
_DOOR_CAPABILITY_MARKERS: tuple[tuple[str, str], ...] = (
    ("elasticsearch", "_ensure_es_index_and_mapping"),
    ("generated_admin", "register_admin"),
    ("retention_purge", "purge_expired"),
)


def _registered_models() -> list[type]:
    return [model for model in apps.get_models() if is_registered(model)]


def _door(model: type) -> str:
    """Which registration door ``model`` came through: ``"subclass"`` or ``"decorator"``.

    ``register_admin`` is defined on ``SnapModel`` itself, so every subclass carries it
    unconditionally; a ``@snap_model``-decorated plain model never does, since the decorator only
    registers metadata. That makes it the ground-truth marker rather than an ``issubclass`` guess
    on the model's shape — the same marker ``features.py``'s ``decorated_models`` capability
    already uses.
    """
    return "subclass" if hasattr(model, "register_admin") else "decorator"


def _inactive_capabilities(model: type) -> str:
    """Capabilities the model's door leaves unreachable, regardless of its settings.

    A comma-joined string rather than a list: the renderer folds a uniform list of flat-scalar
    dicts into one aligned table, and a nested list value on even one item would fall it back to
    the far noisier per-item rendering (see ``diagnostics/render.py``'s ``_render_table``).
    """
    return ", ".join(name for name, marker in _DOOR_CAPABILITY_MARKERS if not hasattr(model, marker))


def _model_items(masked: set) -> list[dict]:
    items: list[dict] = []
    for model in _registered_models():
        field_names = {field.name for field in model._meta.get_fields()}
        items.append(
            {
                "model": f"{model._meta.app_label}.{model.__name__}",
                "door": _door(model),
                "inactive_capabilities": _inactive_capabilities(model),
                "es_mode": get_model_meta(model, "es_storage_mode", EsStorageMode.DB_ONLY).name,
                "retention_days": get_model_meta(model, "data_retention_days", None),
                "write_restricted": get_model_meta(model, "api_write_fields", None) is not None,
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
    masked = set(get_setting("SNAPADMIN_MASKED_FIELDS", []) or [])
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
