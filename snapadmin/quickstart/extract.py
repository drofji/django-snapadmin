"""
Extract only the ``demo/`` directory from a GitHub source tarball, safely.

The archive's top level is a single ``django-snapadmin-<ref>/`` directory; we strip it and keep only
the ``demo/`` subtree. Every member path is sanitised (no absolute paths, no ``..`` traversal — a
zip-slip guard) and non-regular members (symlinks, devices) are skipped, so a hostile archive can't
write outside the destination. If extracting would overwrite existing files the user is asked to
confirm first (unless ``assume_yes``).

Extraction *overlays* a tree rather than replacing it, so refreshing an older copy would otherwise
leave behind every file the newer release deleted. The version stamp written by
:mod:`snapadmin.quickstart.stamp` records which files this tool wrote, and a refresh removes the
ones the new release no longer ships — never a file the user added themselves.
"""

from __future__ import annotations

import shutil
import tarfile
from collections.abc import Callable
from pathlib import Path

from snapadmin.quickstart import QuickstartError, stamp as stamp_mod

_DEMO_PREFIX = "demo/"


def _strip_top(name: str) -> str:
    """``"django-snapadmin-1.2/demo/x"`` → ``"demo/x"``."""
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 else ""


def _is_demo_member(name: str) -> bool:
    rel = _strip_top(name)
    return rel == "demo" or rel.startswith(_DEMO_PREFIX)


def _print_paths(paths: list[Path]) -> None:
    for path in paths[:20]:
        print(f"  {path}")
    if len(paths) > 20:
        print(f"  … and {len(paths) - 20} more")


def _prompt_overwrite(replaced: list[Path], removed: list[Path] = ()) -> bool:
    if replaced:
        print("The following files already exist and would be replaced:")
        _print_paths(replaced)
    if removed:
        print("These files came from an older demo release and would be deleted:")
        _print_paths(list(removed))
    return input("Continue? [y/N]: ").strip().lower() in {"y", "yes"}


def extract_demo(
    archive: Path,
    dest: Path,
    *,
    version: str | None = None,
    assume_yes: bool = False,
    confirm: Callable[[list[Path], list[Path]], bool] | None = None,
) -> Path:
    """Extract ``demo/`` from ``archive`` into ``dest``; return the extracted ``demo`` directory.

    ``version`` is the release being extracted; when given, the tree is stamped with it so a later
    run can report drift against the installed package. Files a previous extraction wrote that this
    archive no longer contains are deleted, under the same confirmation as an overwrite.
    """
    dest = Path(dest).resolve()
    demo_dir = dest / "demo"
    confirm = confirm or _prompt_overwrite

    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in tar.getmembers() if _is_demo_member(m.name)]
        if not members:
            raise QuickstartError("The archive contains no demo/ directory.")

        planned: list[tuple[tarfile.TarInfo, str, Path]] = []
        for member in members:
            rel = _strip_top(member.name)
            target = (dest / rel).resolve()
            if target != dest and dest not in target.parents:
                raise QuickstartError(f"Unsafe path in archive: {member.name}")
            planned.append((member, rel, target))

        manifest = [rel[len(_DEMO_PREFIX):] for member, rel, _ in planned if member.isfile()]
        existing = [t for m, _, t in planned if m.isfile() and t.exists()]
        orphans = stamp_mod.orphaned_files(demo_dir, manifest)
        if (existing or orphans) and not assume_yes and not confirm(existing, orphans):
            raise QuickstartError("Aborted — existing demo files were not replaced.")

        # Prune before writing: a path that was a file in the old release and is a directory in the
        # new one would otherwise collide when the tree is recreated.
        for path in orphans:
            path.unlink()
        if orphans:
            print(f"Removed {len(orphans)} file(s) the new demo release no longer ships.")

        for member, _, target in planned:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
            # symlinks / devices are deliberately skipped

    if version is not None:
        stamp_mod.write_stamp(demo_dir, version, manifest)
    return demo_dir
