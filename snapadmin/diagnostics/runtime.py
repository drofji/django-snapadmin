"""
Celery / broker collector for ``snapadmin_info``.

Celery is an optional extra, so the section collapses to ``disabled`` when it isn't installed.
When present it reports the broker and result-backend URLs (**with any password redacted**), the
number of online workers and the configured Beat schedule. It is *not* a health probe — a project
can be perfectly healthy with no worker running — so a missing worker never fails ``--health-check``.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from snapadmin.diagnostics.registry import register


def _mask_url(url: str) -> str:
    """Redact the password in a broker/backend URL, keeping scheme/host/port/path."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    userinfo = f"{parts.username}:***" if parts.username else "***"
    return urlunsplit(parts._replace(netloc=f"{userinfo}@{host}"))


def _worker_names(app) -> list[str]:
    """Names of the Celery workers currently answering a control ping."""
    ping = app.control.inspect().ping()
    return sorted(ping) if ping else []


@register("celery", title="Celery & Broker", icon="⚙", order=40)
def collect(*, verbose: bool) -> dict:
    """Collect the Celery/broker section."""
    try:
        from celery import current_app
    except ImportError:
        return {"enabled": False}

    conf = current_app.conf
    data: dict = {
        "enabled": True,
        "broker": _mask_url(str(conf.broker_url)) if conf.broker_url else None,
        "result_backend": _mask_url(str(conf.result_backend)) if conf.result_backend else None,
        "scheduled_tasks": sorted((conf.beat_schedule or {}).keys()),
    }
    try:
        workers = _worker_names(current_app)
        data["workers_online"] = len(workers)
        if verbose and workers:
            data["workers"] = workers
    except Exception as exc:
        data["workers_online"] = 0
        data["error"] = str(exc)
    return data
