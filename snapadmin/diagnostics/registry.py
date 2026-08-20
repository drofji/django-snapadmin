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
import re
from dataclasses import dataclass
from typing import Callable

from snapadmin.logging_config import get_logger

logger = get_logger("snapadmin.diagnostics")

#: A collector callable: ``collect(*, verbose: bool) -> dict``.
CollectorFn = Callable[..., dict]

#: Submodules of this package that are infrastructure, not collectors.
_NON_COLLECTOR_MODULES = frozenset({"registry", "render"})

#: ``scheme://user:password@host`` inside free text — exception messages quote whole DSNs.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/@\s]+):[^@/\s]+@")


def _redact(text: str) -> str:
    """Strip credentials out of a message before it is reported.

    A driver's exception text routinely quotes the connection string it failed on, password
    and all. This report is pasted into issues and shipped to monitoring, and the package's
    rule is that it never prints secrets — so the message is scrubbed even though nothing
    here *intends* to include one.
    """
    return _URL_CREDENTIALS.sub(r"\g<scheme>\g<user>:***@", text)


def _describe(exc: Exception) -> str:
    """One line: exception type and message, no traceback, no credentials."""
    message = " ".join(str(exc).split())
    return _redact(f"{type(exc).__name__}: {message}" if message else type(exc).__name__)


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
        """Run the collector, turning a crash into a reported section rather than a lost report.

        ``snapadmin_info`` is run *because* something is wrong, so one subsystem raising must
        not cost the reader every section after it. Collectors are fail-soft for the failures
        they anticipate; this catches the ones they don't (a half-migrated database, an
        optional package missing, a third-party integration exploding on import). A crashed
        **health probe** additionally reports ``ok=False``: isolating a failure must never make
        ``--health-check`` pass. ``KeyboardInterrupt`` and other ``BaseException``s propagate —
        Ctrl-C is the user leaving, not a subsystem fault.
        """
        try:
            return self.fn(verbose=verbose)
        except Exception as exc:
            logger.warning("snapadmin.diagnostics.collector_failed", section=self.name, error=_describe(exc))
            data: dict = {"collector_error": _describe(exc)}
            if self.health_probe:
                data["ok"] = False
            return data


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
