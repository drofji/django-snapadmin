"""
Elasticsearch collector for ``snapadmin_info``.

When ``ELASTICSEARCH_ENABLED`` is off the section collapses to ``disabled``. When on, it reports
cluster reachability, cluster status, index count and the storage-mode tally of the registered
SnapModels (DB_ONLY / DUAL / ES_ONLY). Registered as a health probe: an enabled-but-unreachable
cluster fails ``--health-check``.
"""

from __future__ import annotations

from collections import Counter

from django.apps import apps
from django.conf import settings

from snapadmin.diagnostics.registry import register
from snapadmin.models import EsStorageMode, SnapModel


def _storage_mode_tally() -> dict:
    counter: Counter = Counter()
    for model in apps.get_models():
        if not SnapModel.is_concrete_subclass(model):
            continue
        counter[getattr(model, "es_storage_mode", EsStorageMode.DB_ONLY)] += 1
    return {mode.name: counter.get(mode, 0) for mode in EsStorageMode}


@register("elasticsearch", title="Elasticsearch", icon="🔍", order=30, health_probe=True)
def collect(*, verbose: bool) -> dict:
    """Collect the Elasticsearch section."""
    if not getattr(settings, "ELASTICSEARCH_ENABLED", False):
        return {"enabled": False}
    data: dict = {"enabled": True, "storage_modes": _storage_mode_tally()}
    try:
        client = SnapModel.get_es_client()
        if not client.ping():
            data["ok"] = False
            return data
        data["ok"] = True
        health = client.cluster.health()
        data["cluster_status"] = health.get("status")
        data["indices"] = len(client.indices.get_alias(index="*"))
    except Exception as exc:
        data["ok"] = False
        data["error"] = str(exc)
    return data
