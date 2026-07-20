"""
Diagnostics collectors behind ``manage.py snapadmin_info``.

Public surface: :func:`collect` gathers the requested sections as ``(collector, data)`` pairs
in display order, and the registry API (:func:`register`, :func:`get_collectors`,
:func:`get_collector`) lets each section live in its own module. See
:mod:`snapadmin.diagnostics.registry`.
"""

from __future__ import annotations

from snapadmin.diagnostics.registry import (
    Collector,
    get_collector,
    get_collectors,
    register,
)

__all__ = ["Collector", "collect", "get_collector", "get_collectors", "register"]


def collect(
    *,
    sections: list[str] | None = None,
    verbose: bool = False,
    health_only: bool = False,
) -> list[tuple[Collector, dict]]:
    """Run the selected collectors and return ``[(collector, data), …]`` in display order.

    ``sections`` (when given) limits the report to those collector names; ``health_only``
    restricts it to health-probe collectors; ``verbose`` is forwarded to each collector.
    """
    results: list[tuple[Collector, dict]] = []
    for collector in get_collectors():
        if health_only and not collector.health_probe:
            continue
        if sections is not None and collector.name not in sections:
            continue
        results.append((collector, collector.collect(verbose=verbose)))
    return results
