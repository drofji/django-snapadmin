"""
Record which release an extracted ``demo/`` tree came from, and detect when it drifts.

``pip install -U django-snapadmin`` upgrades the *package*; the ``demo/`` tree
``snapadmin-demo`` extracted next to it is an ordinary directory and stays exactly as it was.
Nothing used to record its provenance, so an old tree kept serving old models, old templates and
the warnings the new release had already fixed — with no way to tell that from the inside.

Extraction therefore leaves a small JSON stamp in the tree (:data:`STAMP_NAME`): the version it
came from plus the manifest of files it wrote. The version half powers the drift notice printed by
``snapadmin-demo`` and ``manage.py snapadmin_info``; the manifest half lets a refresh delete files
a newer release removed — extraction only overwrites, so without it a template deleted upstream
would linger forever. Only paths this tool wrote are ever considered for deletion; anything the
user added is not in the manifest and is never touched.

Stdlib-only and import-safe before Django exists, like the rest of ``snapadmin.quickstart``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

#: Written at the root of the extracted tree (next to ``manage.py``).
STAMP_NAME = ".snapadmin-demo.json"

#: How many parent directories :func:`find_demo_tree` walks up before giving up.
DEFAULT_MAX_PARENTS = 3


def stamp_path(demo_dir: Path | str) -> Path:
    """Path of the stamp file inside ``demo_dir`` (whether or not it exists)."""
    return Path(demo_dir) / STAMP_NAME


def _normalise(version: str) -> str:
    return version.lstrip("v")


def read_stamp(demo_dir: Path | str) -> dict | None:
    """The stamp written into ``demo_dir``, or ``None``.

    A missing, unreadable, malformed or non-mapping stamp is not an error: the tree simply
    predates stamping (or was hand-edited), and every caller degrades to "unknown provenance".
    """
    path = stamp_path(demo_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def stamped_version(demo_dir: Path | str) -> str | None:
    """The release an extracted tree came from, or ``None`` when it is unknown."""
    stamp = read_stamp(demo_dir) or {}
    version = stamp.get("version")
    return _normalise(version) if isinstance(version, str) and version else None


def write_stamp(demo_dir: Path | str, version: str, files: Iterable[str]) -> Path:
    """Stamp ``demo_dir`` with ``version`` and the manifest of files just written."""
    demo_dir = Path(demo_dir)
    manifest = sorted({str(name).replace("\\", "/") for name in files} - {STAMP_NAME})
    path = stamp_path(demo_dir)
    path.write_text(json.dumps({"version": _normalise(version), "files": manifest}, indent=2) + "\n", encoding="utf-8")
    return path


def orphaned_files(demo_dir: Path | str, current: Iterable[str]) -> list[Path]:
    """Files a previous extraction wrote that the current release no longer ships.

    Only manifest entries are candidates, and only those still resolving inside ``demo_dir`` —
    a hand-edited manifest must not become a delete-anything primitive.
    """
    stamp = read_stamp(demo_dir)
    if not stamp:
        return []
    demo_dir = Path(demo_dir).resolve()
    keep = {str(name).replace("\\", "/") for name in current}
    orphans = []
    for name in stamp.get("files", []):
        if not isinstance(name, str) or name in keep:
            continue
        target = (demo_dir / name).resolve()
        if demo_dir not in target.parents or not target.is_file():
            continue
        orphans.append(target)
    return sorted(orphans)


def find_demo_tree(start: Path | str | None = None, *, max_parents: int = DEFAULT_MAX_PARENTS) -> Path | None:
    """The nearest stamped demo tree at or above ``start`` (default: the current directory).

    Checks each directory and its ``demo/`` child, because the demo is run both from inside the
    tree (``manage.py``) and from the directory it was extracted into (``demo/manage.py``).
    """
    base = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for candidate in [base, *list(base.parents)[:max_parents]]:
        for tree in (candidate, candidate / "demo"):
            if stamp_path(tree).is_file():
                return tree
    return None


def drift_notice(demo_dir: Path | str, installed: str | None) -> str | None:
    """One line naming both versions when a stamped tree is older (or newer) than the install."""
    stamped = stamped_version(demo_dir)
    if not stamped or not installed or stamped == _normalise(installed):
        return None
    return (
        f"The demo tree at {Path(demo_dir)} was extracted from django-snapadmin {stamped}, "
        f"but {_normalise(installed)} is installed — pip does not upgrade an extracted tree. "
        "Re-run snapadmin-demo to refresh it."
    )
