"""
tests/test_dependency_compat.py

What the package declares about its dependencies, and what CI actually runs,
must be the same thing (#QA1f).

Three published promises, each checked against the place that would prove it:

* **Python and Django support.** The trove classifiers say which versions are
  supported; the CI matrix is what runs them. A classifier with no matrix job
  is a claim nothing checks; a matrix job with no classifier is support nobody
  was told about. The lowest of each must also be the declared lower bound, and
  the lowest-dependency job must run on the lowest Python.
* **Every declared dependency is installed by CI, at the declared range.** CI
  installs ``demo/requirements.txt``. A dependency missing there is tested only
  if something happens to pull it in; a different specifier there means CI
  resolves inside a range the package does not publish.
* **The lower bounds hold.** That is the ``lowest-deps`` CI job
  (``scripts/gates.py lowest-deps``); ``tests/test_gates_script.py`` pins how it
  is built.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from snapadmin.licensing import _normalize

from tests.test_gates_script import gates

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "test.yml").read_text())

#: Declared dependencies CI deliberately does not install, and why.
NOT_INSTALLED_IN_CI = {
    "boto3": (
        "the S3 backup transport is tested against a stubbed client (sys.modules), "
        "and the demo image does not carry the AWS SDK it never uses; the lowest-deps "
        "job installs it at its declared minimum"
    ),
}


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _matrix(axis: str) -> list[str]:
    matrix = WORKFLOW["jobs"]["test"]["strategy"]["matrix"]
    return sorted({str(value) for value in matrix[axis]}, key=_version_key)


def _classified(prefix: str) -> list[str]:
    found = re.findall(rf'"{re.escape(prefix)} :: (\d+\.\d+)"', PYPROJECT)
    return sorted(set(found), key=_version_key)


_normalise = _normalize  # PEP 503 name normalisation, the licence inventory's own

def _demo_requirements() -> dict[str, str]:
    requirements = {}
    for line in (REPO_ROOT / "demo" / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)", line)
        if match:
            requirements[_normalise(match[1])] = match[3].replace(" ", "")
    return requirements


# ─────────────────────────────────────────────────────────────────────────────
# Python and Django support
# ─────────────────────────────────────────────────────────────────────────────

def test_the_python_classifiers_are_the_python_versions_ci_runs():
    assert _classified("Programming Language :: Python") == _matrix("python")


def test_the_django_classifiers_are_the_django_versions_ci_runs():
    assert _classified("Framework :: Django") == _matrix("django")


def test_the_lowest_python_ci_runs_is_the_declared_minimum():
    declared = re.search(r'^python = ">=(\d+\.\d+)"', PYPROJECT, re.MULTILINE)[1]

    assert _matrix("python")[0] == declared


def test_the_lowest_django_ci_runs_is_the_declared_minimum():
    assert gates.lower_bound(gates.declared_dependencies()["Django"]) == _matrix("django")[0]


def test_the_lowest_dependency_job_runs_on_the_lowest_python():
    steps = WORKFLOW["jobs"]["lowest-deps"]["steps"]
    python = next(step["with"]["python-version"] for step in steps if "setup-python" in step.get("uses", ""))

    assert str(python) == _matrix("python")[0]


# ─────────────────────────────────────────────────────────────────────────────
# CI installs what the package declares, at the declared range
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, specifier", sorted(gates.declared_dependencies().items()))
def test_every_declared_dependency_is_installed_by_ci_at_the_declared_range(name, specifier):
    installed = _demo_requirements()

    if name in NOT_INSTALLED_IN_CI:
        assert _normalise(name) not in installed, (
            f"{name} is in demo/requirements.txt now — drop its NOT_INSTALLED_IN_CI entry"
        )
        return
    assert _normalise(name) in installed, (
        f"{name} is a declared dependency but demo/requirements.txt (what CI installs) "
        f"does not list it: CI only tests it if something else happens to pull it in"
    )
    assert installed[_normalise(name)] == specifier.replace(" ", ""), (
        f"{name}: pyproject.toml declares {specifier!r}, CI installs "
        f"{installed[_normalise(name)]!r}"
    )


def test_every_exception_is_a_declared_dependency_with_a_reason():
    declared = gates.declared_dependencies()

    for name, reason in NOT_INSTALLED_IN_CI.items():
        assert name in declared
        assert len(reason.split()) >= 10
