"""Render the integration checks as a human report or JSON."""

from __future__ import annotations

import json
from collections import defaultdict

from snapadmin.integrate.detect import ProjectContext
from snapadmin.integrate.steps import GROUPS, Step


def _icon(present: bool | None) -> str:
    if present is None:
        return "⚠️"
    return "✅" if present else "❌"


def render_text(steps: list[Step], project: ProjectContext) -> str:
    """Render the checklist grouped exactly like ``docs/index.html#integration-checklist``.

    Every row prints ✅ (present), ❌ (missing — add the snippet) or ⚠️ (not
    checked — this doctor cannot tell without a running project; the note
    names the live command that can). Never collapses ⚠️ into either ✅ or ❌
    — that would be exactly the false green this doctor must never print.
    """
    lines = ["🩺 SnapAdmin integration check", ""]
    lines.append(f"settings: {project.settings_path or 'not found (pass --settings PATH)'}")
    lines.append(f"urls:     {project.urls_path or 'not found (pass --urls PATH)'}")
    lines.append("")

    by_group: dict[str, list[Step]] = defaultdict(list)
    for step in steps:
        by_group[step.group].append(step)

    for group_key, group_title in GROUPS:
        group_steps = by_group[group_key]
        if not group_steps:
            continue
        lines.append(f"── {group_title} ──")
        for step in group_steps:
            icon = _icon(step.present)
            if step.present is True:
                lines.append(f"{icon} {step.title}: already present")
                if step.note:
                    lines.append(f"    {step.note}")
            elif step.present is False:
                lines.append(f"{icon} {step.title}: add this —")
                if step.note:
                    lines.append(f"    {step.note}")
                lines.extend(f"    {snippet_line}" for snippet_line in step.snippet.splitlines())
            else:
                lines.append(f"{icon} {step.title}: not checked")
                if step.note:
                    lines.append(f"    {step.note}")
        lines.append("")

    lines.append("This command only reports and prints snippets — it changes nothing. Review, then paste.")
    return "\n".join(lines)


def render_json(steps: list[Step], project: ProjectContext) -> str:
    return json.dumps(
        {
            "settings": str(project.settings_path) if project.settings_path else None,
            "urls": str(project.urls_path) if project.urls_path else None,
            "steps": [
                {
                    "name": step.name,
                    "title": step.title,
                    "group": step.group,
                    "present": step.present,
                    "note": step.note or None,
                    "snippet": step.snippet,
                }
                for step in steps
            ],
        },
        indent=2,
    )
