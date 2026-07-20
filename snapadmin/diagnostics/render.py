"""
Text renderer for ``snapadmin_info``.

Turns the collector output (JSON-clean dicts) into a readable, indented report. ``--json``
bypasses this entirely and dumps the raw collector data. Keeping rendering generic here means a
new collector only has to return well-shaped data — it never writes its own formatting code.

Conventions the renderer honours in the collector data:

* ``{"enabled": False}`` collapses a feature-gated section to a single ``Title: disabled`` line.
* keys beginning with ``_`` are treated as metadata and never rendered.
"""

from __future__ import annotations

from typing import Any

from snapadmin.diagnostics.registry import Collector

_INDENT = "  "


def render_report(results: list[tuple[Collector, dict]], *, brief: bool = False) -> str:
    """Render ``[(collector, data), …]`` into the full text report."""
    return "\n".join(_render_section(collector, data, brief=brief) for collector, data in results)


def _render_section(collector: Collector, data: dict, *, brief: bool) -> str:
    header = f"{collector.icon} {collector.title}".strip()
    if data.get("enabled") is False:
        return f"{header}: disabled"
    lines = [header]
    if brief:
        for key, value in data.items():
            if key.startswith("_") or isinstance(value, (dict, list)):
                continue
            lines.append(f"{_INDENT}{_humanise(key)}: {_format_scalar(value)}")
    else:
        lines.extend(_render_value(data, depth=1))
    return "\n".join(lines)


def _render_value(value: Any, *, depth: int) -> list[str]:
    lines: list[str] = []
    pad = _INDENT * depth
    if isinstance(value, dict):
        for key, item in value.items():
            if key.startswith("_"):
                continue
            label = _humanise(key)
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{label}:")
                lines.extend(_render_value(item, depth=depth + 1))
            else:
                lines.append(f"{pad}{label}: {_format_scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.extend(_render_value(item, depth=depth))
            else:
                lines.append(f"{pad}- {_format_scalar(item)}")
    else:
        lines.append(f"{pad}{_format_scalar(value)}")
    return lines


def _humanise(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _format_scalar(value: Any) -> str:
    if value is True:
        return "✓"
    if value is False:
        return "✗"
    if value is None:
        return "—"
    return str(value)
