"""
Download + cache the demo source for a given release tag.

The demo lives only in the GitHub *source tarball* of a git tag (the PyPI wheel ships only the
``snapadmin/`` package), so this fetches ``…/archive/refs/tags/v<version>.tar.gz`` over HTTPS from
the official repository, caches it under ``~/.cache/snapadmin-demo/`` and checksums it so a repeat
run works offline and can detect a corrupted cache. Everything routes through :func:`_http_get`,
the single seam the tests mock — no real network in the suite.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import urllib.request
from importlib import metadata as _im
from pathlib import Path

from snapadmin.quickstart import QuickstartError, TagNotFoundError

REPO = "drofji/django-snapadmin"
CACHE_DIR = Path.home() / ".cache" / "snapadmin-demo"

_TARBALL_URL = "https://github.com/{repo}/archive/refs/tags/v{version}.tar.gz"
_TAGS_API = "https://api.github.com/repos/{repo}/tags"


def _http_get(url: str, *, timeout: float = 30.0) -> bytes:
    """GET ``url`` and return its body. The one network seam the tests patch."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed official https host
        return response.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def installed_version() -> str | None:
    """The installed ``django-snapadmin`` version, or ``None`` if it isn't installed."""
    try:
        return _im.version("django-snapadmin")
    except _im.PackageNotFoundError:
        return None


def resolve_version(explicit: str | None) -> str:
    """The demo version to fetch: the explicit one, else the installed package version."""
    version = explicit or installed_version()
    if not version:
        raise QuickstartError(
            "Could not determine a version to download — django-snapadmin is not installed here. "
            "Pass --version X.Y.Z explicitly."
        )
    return version.lstrip("v")


def available_tags() -> list[str]:
    """Release tag names from the GitHub API (best-effort; empty on any error)."""
    try:
        raw = _http_get(_TAGS_API.format(repo=REPO))
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [tag["name"] for tag in data if isinstance(tag, dict) and "name" in tag]


def _checksum_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".sha256")


def _is_cached(archive: Path) -> bool:
    checksum = _checksum_path(archive)
    if not (archive.exists() and checksum.exists()):
        return False
    return checksum.read_text().strip() == _sha256(archive.read_bytes())


def download_demo(version: str, *, clear_cache: bool = False, cache_dir: Path | None = None) -> Path:
    """Return the path to the cached demo tarball for ``version``, downloading it if needed."""
    cache = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    if clear_cache and cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True, exist_ok=True)

    archive = cache / f"v{version}.tar.gz"
    if _is_cached(archive):
        return archive

    url = _TARBALL_URL.format(repo=REPO, version=version)
    try:
        data = _http_get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise TagNotFoundError(version, available_tags())
        raise QuickstartError(f"Download failed (HTTP {exc.code}) for {url}")
    except urllib.error.URLError as exc:
        raise QuickstartError(f"Download failed: {exc.reason}")

    archive.write_bytes(data)
    _checksum_path(archive).write_text(_sha256(data))
    return archive
