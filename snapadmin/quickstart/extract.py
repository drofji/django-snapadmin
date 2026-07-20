"""
Extract only the ``demo/`` directory from a GitHub source tarball, safely.

The archive's top level is a single ``django-snapadmin-<ref>/`` directory; we strip it and keep only
the ``demo/`` subtree. Every member path is sanitised (no absolute paths, no ``..`` traversal — a
zip-slip guard) and non-regular members (symlinks, devices) are skipped, so a hostile archive can't
write outside the destination. If extracting would overwrite existing files the user is asked to
confirm first (unless ``assume_yes``).
"""

from __future__ import annotations

import shutil
import tarfile
from collections.abc import Callable
from pathlib import Path

from snapadmin.quickstart import QuickstartError


def _strip_top(name: str) -> str:
    """``"django-snapadmin-1.2/demo/x"`` → ``"demo/x"``."""
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 else ""


def _is_demo_member(name: str) -> bool:
    rel = _strip_top(name)
    return rel == "demo" or rel.startswith("demo/")


def _prompt_overwrite(paths: list[Path]) -> bool:
    print("The following files already exist and would be replaced:")
    for path in paths[:20]:
        print(f"  {path}")
    if len(paths) > 20:
        print(f"  … and {len(paths) - 20} more")
    return input("Replace them? [y/N]: ").strip().lower() in {"y", "yes"}


def extract_demo(
    archive: Path,
    dest: Path,
    *,
    assume_yes: bool = False,
    confirm: Callable[[list[Path]], bool] | None = None,
) -> Path:
    """Extract ``demo/`` from ``archive`` into ``dest``; return the extracted ``demo`` directory."""
    dest = Path(dest).resolve()
    confirm = confirm or _prompt_overwrite

    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in tar.getmembers() if _is_demo_member(m.name)]
        if not members:
            raise QuickstartError("The archive contains no demo/ directory.")

        planned: list[tuple[tarfile.TarInfo, Path]] = []
        for member in members:
            rel = _strip_top(member.name)
            target = (dest / rel).resolve()
            if target != dest and dest not in target.parents:
                raise QuickstartError(f"Unsafe path in archive: {member.name}")
            planned.append((member, target))

        existing = [t for m, t in planned if m.isfile() and t.exists()]
        if existing and not assume_yes and not confirm(existing):
            raise QuickstartError("Aborted — existing demo files were not replaced.")

        for member, target in planned:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
            # symlinks / devices are deliberately skipped

    return dest / "demo"
