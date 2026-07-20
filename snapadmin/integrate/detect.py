"""
Locate an existing project's ``settings.py`` and root ``urls.py`` and read them (read-only).

Detection is best-effort and never fatal: an explicit ``--settings`` / ``--urls`` wins, otherwise we
read ``manage.py`` for its ``DJANGO_SETTINGS_MODULE`` and fall back to globbing. When a file can't be
found the doctor still prints the snippet to add (treating it as missing), so a failed guess only
means "here's what to paste".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from snapadmin.integrate import IntegrateError


@dataclass
class ProjectContext:
    project_dir: Path
    settings_path: Path | None
    settings_text: str
    urls_path: Path | None
    urls_text: str
    requirements_text: str
    url_prefix: str = ""
    extras: list[str] = field(default_factory=list)
    include_api: bool = False
    include_graphql: bool = False


def _read(path: Path | None) -> str:
    if path and path.exists():
        return path.read_text(errors="ignore")
    return ""


def _settings_module_from_manage(project_dir: Path) -> str | None:
    manage = project_dir / "manage.py"
    if not manage.exists():
        return None
    match = re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([\w.]+)['\"]", manage.read_text(errors="ignore")
    )
    return match.group(1) if match else None


def find_settings(project_dir: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    module = _settings_module_from_manage(project_dir)
    if module:
        candidate = project_dir / (module.replace(".", "/") + ".py")
        if candidate.exists():
            return candidate
    matches = sorted(project_dir.glob("*/settings.py")) + sorted(project_dir.glob("settings.py"))
    return matches[0] if matches else None


def find_urls(project_dir: Path, settings_path: Path | None, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    if settings_path:
        candidate = settings_path.parent / "urls.py"
        if candidate.exists():
            return candidate
    matches = sorted(project_dir.glob("*/urls.py"))
    return matches[0] if matches else None


def _read_requirements(project_dir: Path) -> str:
    return "\n".join(
        _read(project_dir / name) for name in ("requirements.txt", "pyproject.toml")
    )


def build_context(
    *,
    project_dir: str,
    settings: str | None = None,
    urls: str | None = None,
    url_prefix: str = "",
    extras: list[str] | None = None,
    include_api: bool = False,
    include_graphql: bool = False,
) -> ProjectContext:
    root = Path(project_dir)
    if not root.is_dir():
        raise IntegrateError(f"Not a directory: {root}")
    settings_path = find_settings(root, settings)
    urls_path = find_urls(root, settings_path, urls)
    return ProjectContext(
        project_dir=root,
        settings_path=settings_path,
        settings_text=_read(settings_path),
        urls_path=urls_path,
        urls_text=_read(urls_path),
        requirements_text=_read_requirements(root),
        url_prefix=url_prefix,
        extras=list(extras or []),
        include_api=include_api,
        include_graphql=include_graphql,
    )
