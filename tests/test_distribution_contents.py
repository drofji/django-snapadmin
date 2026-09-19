"""What the sdist and the wheel carry (#EXT1g / #RM1e).

The test suite ships in the **sdist only**: a downstream packager or auditor can
unpack it and run ``python -m pytest`` there (verified by hand on 2026-09-18 —
the full suite passes from an unpacked sdist), while the wheel a project installs
stays lean. The suite needs the demo project (its settings module), the docs it
checks for truth and the CI/contributor files it pins, so those go in with it.

Read from ``pyproject.toml`` rather than by building a distribution, which would
make every test run pay for a build.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Paths the suite needs next to it, sdist-only.
SDIST_ONLY = {
    "tests/**/*.py",
    "conftest.py",
    "pytest.ini",
    "demo/**/*",
    "docs/**/*",
    "CONTRIBUTING.md",
    ".github/**/*",
}


@pytest.fixture(scope="module")
def includes() -> dict[str, list[str] | None]:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    found: dict[str, list[str] | None] = {}
    for entry in pyproject["tool"]["poetry"]["include"]:
        if isinstance(entry, str):
            found[entry] = None  # poetry default: both formats
        else:
            found[entry["path"]] = list(entry.get("format", ["sdist", "wheel"]))
    return found


def test_the_suite_and_what_it_needs_travel_with_the_sdist(includes):
    missing = sorted(path for path in SDIST_ONLY if path not in includes)
    assert not missing, f"not included in the sdist: {missing}"


def test_none_of_it_reaches_the_wheel(includes):
    leaking = sorted(path for path in SDIST_ONLY if includes.get(path) != ["sdist"])
    assert not leaking, f"these must be sdist-only, never in the wheel: {leaking}"


def test_every_included_pattern_matches_a_tracked_file(includes):
    """A renamed directory must not leave a pattern that silently ships nothing."""
    dead = sorted(p for p in SDIST_ONLY if not any(REPO_ROOT.glob(p)))
    assert not dead, f"include patterns that match nothing: {dead}"
