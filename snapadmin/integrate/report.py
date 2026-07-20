"""Render the integration checks as a human report or JSON."""

from __future__ import annotations

import json

from snapadmin.integrate.detect import ProjectContext
from snapadmin.integrate.steps import Step


def render_text(steps: list[Step], project: ProjectContext) -> str:
    lines = ["🩺 SnapAdmin integration check", ""]
    lines.append(f"settings: {project.settings_path or 'not found (pass --settings PATH)'}")
    lines.append(f"urls:     {project.urls_path or 'not found (pass --urls PATH)'}")
    lines.append("")
    for step in steps:
        if step.present:
            lines.append(f"✓ {step.title}: already present")
            if step.note:
                lines.append(f"    ⚠ {step.note}")
        else:
            lines.append(f"✗ {step.title}: add this —")
            if step.note:
                lines.append(f"    ⚠ {step.note}")
            lines.extend(f"    {snippet_line}" for snippet_line in step.snippet.splitlines())
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
                    "present": step.present,
                    "note": step.note or None,
                    "snippet": step.snippet,
                }
                for step in steps
            ],
        },
        indent=2,
    )
