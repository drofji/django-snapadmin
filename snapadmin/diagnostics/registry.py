"""
Collector registry for ``snapadmin_info`` (see :mod:`snapadmin.diagnostics`).

Each diagnostics section is a *collector*: a callable ``collect(*, verbose: bool) -> dict``
registered under a name with the :func:`register` decorator. Collectors live one per module
in this package and are imported on demand by :func:`load_collectors`, so a new section is
added by dropping in a new module — there is no shared registry list to edit, which keeps the
sections independently developable.

A collector returns a JSON-serialisable ``dict`` — the raw data both for ``--json`` and for the
text renderer. A collector registered with ``health_probe=True`` additionally returns an ``"ok"``
boolean; ``snapadmin_info --health-check`` runs only those and exits non-zero if any is ``False``.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Callable

#: A collector callable: ``collect(*, verbose: bool) -> dict``.
CollectorFn = Callable[..., dict]

#: Submodules of this package that are infrastructure, not collectors.
_NON_COLLECTOR_MODULES = frozenset({"registry", "render"})


@dataclass(frozen=True)
class Collector:
    """A registered diagnostics section."""

    name: str
    title: str
    icon: str
    order: int
    health_probe: bool
    fn: CollectorFn

    def collect(self, *, verbose: bool) -> dict:
        """Run the underlying collector function."""
        return self.fn(verbose=verbose)


_REGISTRY: dict[str, Collector] = {}
_loaded = False


def register(
    name: str,
    *,
    title: str,
    icon: str = "",
    order: int = 100,
    health_probe: bool = False,
) -> Callable[[CollectorFn], CollectorFn]:
    """Register the decorated function as the ``name`` diagnostics section.

    ``title``/``icon`` are used by the text renderer; ``order`` sorts sections in the report;
    ``health_probe`` marks a section whose ``"ok"`` flag ``--health-check`` inspects.
    """

    def decorator(fn: CollectorFn) -> CollectorFn:
        _REGISTRY[name] = Collector(
            name=name, title=title, icon=icon, order=order, health_probe=health_probe, fn=fn
        )
        return fn

    return decorator


def load_collectors() -> None:
    """Import every collector submodule once, so each ``@register`` runs. Idempotent."""
    global _loaded
    if _loaded:
        return
    import snapadmin.diagnostics as package

    for module in pkgutil.iter_modules(package.__path__):
        if module.name in _NON_COLLECTOR_MODULES:
            continue
        importlib.import_module(f"{package.__name__}.{module.name}")
    _loaded = True


def get_collectors() -> list[Collector]:
    """Return every registered collector in display order (``order`` then ``name``)."""
    load_collectors()
    return sorted(_REGISTRY.values(), key=lambda collector: (collector.order, collector.name))


def get_collector(name: str) -> Collector | None:
    """Return the collector registered under ``name``, or ``None`` if there is none."""
    load_collectors()
    return _REGISTRY.get(name)
