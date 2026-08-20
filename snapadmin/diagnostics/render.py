"""
Text renderer for ``snapadmin_info``.

Turns the collector output (JSON-clean dicts) into a readable, indented report. ``--json``
bypasses this entirely and dumps the raw collector data. Keeping rendering generic here means a
new collector only has to return well-shaped data — it never writes its own formatting code.

Two shapes get special treatment because the naive key/value rendering made them unreadable:

* **A uniform list of dicts** (every item with the same scalar keys) renders as an aligned table.
  Rendered as plain key/value pairs, an inventory of 11 models produced 55 lines with each of the
  five key names repeated 11 times.
* **A run of booleans** (four or more in one dict) renders as two wrapped ``on`` / ``off`` lines.
  The feature-adoption checklist is 16 booleans; as one line per flag it could not be read at a
  glance, which is the only thing a checklist is for.

Other conventions the renderer honours in the collector data:

* ``{"enabled": False}`` collapses a feature-gated section to a single ``Title: disabled`` line.
* keys beginning with ``_`` are treated as metadata and never rendered.
"""

from __future__ import annotations

import textwrap
from typing import Any

from snapadmin.diagnostics.registry import Collector

_INDENT = "  "

#: Below this many booleans, one line each is clearer than a grouped run.
_BOOL_GROUP_MIN = 4

#: Target width for wrapped grouped values. Narrow enough for a split terminal.
_WRAP_WIDTH = 88


def render_report(results: list[tuple[Collector, dict]], *, brief: bool = False) -> str:
    """Render ``[(collector, data), …]`` into the full text report."""
    return "\n".join(_render_section(collector, data, brief=brief) for collector, data in results)


def _render_section(collector: Collector, data: dict, *, brief: bool) -> str:
    header = f"{collector.icon} {collector.title}".strip()
    if data.get("enabled") is False:
        return f"{header}: disabled"
    # A collector that crashed has nothing else to show. This is not the same as a collector
    # reporting its own ``error`` (a database that refused a connection still knows its engine
    # and host, and those lines are the useful part), so only the crash key collapses a section.
    if data.get("collector_error"):
        return f"{header}: unavailable — {data['collector_error']}"
    lines = [header]
    if brief:
        for key, value in data.items():
            if key.startswith("_") or isinstance(value, (dict, list)):
                continue
            lines.append(f"{_INDENT}{_humanise(key)}: {_format_scalar(value)}")
    else:
        lines.extend(_render_value(data, depth=1))
    return "\n".join(lines)


def _render_value(value: dict | list, *, depth: int) -> list[str]:
    """Render a container. Scalars are always formatted by their parent, inline."""
    if isinstance(value, dict):
        return _render_mapping(value, depth=depth)

    table = _render_table(value, depth=depth)
    if table is not None:
        return table

    pad = _INDENT * depth
    lines: list[str] = []
    for item in value:
        if isinstance(item, (dict, list)):
            lines.extend(_render_value(item, depth=depth))
        else:
            lines.append(f"{pad}- {_format_scalar(item)}")
    return lines


def _render_mapping(mapping: dict, *, depth: int) -> list[str]:
    pad = _INDENT * depth
    lines: list[str] = []
    visible = {key: item for key, item in mapping.items() if not key.startswith("_")}

    flags = {key: item for key, item in visible.items() if isinstance(item, bool)}
    grouped = _render_flag_groups(flags, depth=depth) if len(flags) >= _BOOL_GROUP_MIN else None
    if grouped is not None:
        lines.extend(grouped)

    for key, item in visible.items():
        if grouped is not None and key in flags:
            continue
        label = _humanise(key)
        if isinstance(item, (dict, list)):
            lines.append(f"{pad}{label}:")
            lines.extend(_render_value(item, depth=depth + 1))
        else:
            lines.append(f"{pad}{label}: {_format_scalar(item)}")
    return lines


def _render_flag_groups(flags: dict[str, bool], *, depth: int) -> list[str] | None:
    """Two wrapped lines — what is on, what is off — instead of one line per flag."""
    on = [_humanise(key) for key, value in flags.items() if value]
    off = [_humanise(key) for key, value in flags.items() if not value]
    pad = _INDENT * depth
    lines: list[str] = []
    for marker, names in (("✓ on ", on), ("✗ off", off)):
        if not names:
            continue
        prefix = f"{pad}{marker}  "
        body = " · ".join(names)
        wrapped = textwrap.wrap(
            body,
            width=max(_WRAP_WIDTH, len(prefix) + 20),
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
            break_long_words=False,
            break_on_hyphens=False,
        )
        lines.extend(wrapped)
    return lines or None


def _render_table(items: list, *, depth: int) -> list[str] | None:
    """Render a uniform list of flat dicts as an aligned table, else ``None``.

    "Uniform" means: at least two items, every one a dict, all with the same key order, and no
    nested values. Anything else falls through to the generic renderer rather than guessing.
    """
    if len(items) < 2 or not all(isinstance(item, dict) for item in items):
        return None
    columns = [key for key in items[0] if not key.startswith("_")]
    if not columns:
        return None
    for item in items:
        if [key for key in item if not key.startswith("_")] != columns:
            return None
        if any(isinstance(item[key], (dict, list)) for key in columns):
            return None

    headers = [_humanise(key) for key in columns]
    cells = [[_format_scalar(item[key]) for key in columns] for item in items]
    widths = [
        max(len(header), *(len(row[index]) for row in cells))
        for index, header in enumerate(headers)
    ]

    pad = _INDENT * depth

    def _row(values: list[str]) -> str:
        return (pad + "  ".join(v.ljust(w) for v, w in zip(values, widths))).rstrip()

    lines = [_row(headers), _row(["─" * width for width in widths])]
    lines.extend(_row(row) for row in cells)
    return lines


def _humanise(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _format_scalar(value: Any) -> str:
    if value is True:
        return "✓"
    if value is False:
        return "✗"
    # An em dash for "nothing here" — a bare `Port:` with a blank after it reads
    # like a truncated report rather than an unset value.
    if value is None or value == "":
        return "—"
    return str(value)
