#!/usr/bin/env python3
"""
scripts/gates.py

Every check CI runs, reproduced by one command (#QA1f).

Usage::

    python scripts/gates.py                  # every gate this machine can run
    python scripts/gates.py suite static     # only the named gates
    python scripts/gates.py --list           # what each gate runs, and which CI job runs it
    python scripts/gates.py --release        # every gate, the built distribution included;
                                             # a gate that cannot run here FAILS the run
    python scripts/gates.py lowest-requirements   # each dependency pinned to its declared minimum
    python scripts/gates.py verify-lowest    # this environment holds exactly those minimums

A gate this machine cannot run — Playwright not installed, no PostgreSQL and
Elasticsearch configured, no ``uv`` for the lowest-dependency environment — is
reported as **not run**, with what it needs, and never as passed. In a plain
run a not-run gate does not fail the command, because a contributor without a
browser can still reproduce everything else. A gate asked for by name that
cannot run fails, and so does any under ``--release`` — the release gate, where
"could not check" is a failure.

What CI runs that no local command can: the Python × Django matrix (six
interpreter/framework pairs). The ``suite`` gate runs the same command on this
interpreter and this Django; ``--list`` says so rather than implying parity.

The commands are written exactly as ``.github/workflows/test.yml`` writes them,
and ``tests/test_gates_script.py`` fails if a CI step appears there that no gate
here reproduces, or a gate claims a CI job that does not run its command.

Stdlib only: it runs before anything is known about the environment, and on the
oldest supported Python.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
DEMO_REQUIREMENTS = REPO_ROOT / "demo" / "requirements.txt"

#: The environment the service-backed gate needs — the variables the
#: ``real-services`` CI job sets (see CONTRIBUTING.md for a local docker recipe).
SERVICE_VARIABLES = ("SNAPADMIN_TEST_POSTGRES", "SNAPADMIN_TEST_ES_URL")


@dataclass(frozen=True)
class Step:
    """One command of a gate, spelled the way the CI workflow spells it."""

    command: str
    env: Mapping[str, str] = field(default_factory=dict)
    #: Expand the last argument as a file glob, as the shell does in CI.
    glob_last: bool = False


@dataclass(frozen=True)
class Gate:
    """A named check: its commands, where CI runs it, and what it needs to run."""

    name: str
    summary: str
    steps: tuple[Step, ...]
    #: Where CI runs it, for people.
    ci: str
    #: The ``test.yml`` job whose steps are exactly these commands (or which runs
    #: ``python scripts/gates.py <name>``); ``None`` when no job runs them as such.
    ci_job: str | None = None
    #: Python modules that must be importable for the gate to run.
    needs_modules: tuple[str, ...] = ()
    #: Environment variables that must be set for the gate to run.
    needs_env: tuple[str, ...] = ()
    #: Executables that must be on PATH for the gate to run.
    needs_executables: tuple[str, ...] = ()
    #: Part of a plain ``python scripts/gates.py`` run.
    default: bool = True
    #: Part of ``--release``.
    release: bool = True


def _lowest_steps() -> tuple[Step, ...]:
    root = REPO_ROOT / ".gates"
    venv = root / "lowest"
    python = shlex.quote(str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
    requirements = shlex.quote(str(root / "lowest-requirements.txt"))
    return (
        Step(f"uv venv --clear --python {shlex.quote(sys.executable)} {shlex.quote(str(venv))}"),
        Step(f"python scripts/gates.py lowest-requirements --output {requirements}"),
        Step(
            f"uv pip install --python {python} --resolution lowest-direct -r {requirements}"
        ),
        # Everything the suite needs that the package does not declare (pytest,
        # hypothesis, …). Already-satisfied packages are left alone, so the
        # declared minimums installed above stay the versions under test.
        Step(f"uv pip install --python {python} -r {shlex.quote(str(DEMO_REQUIREMENTS))}"),
        # uv's venv has no pip of its own; uv checks the same thing.
        Step(f"uv pip check --python {python}"),
        # A declared minimum that cannot be installed alongside the others is
        # silently raised by the resolver; this makes that a failure, naming
        # the bound to correct.
        Step(f"{python} scripts/gates.py verify-lowest"),
        Step(f"{python} -m pytest --hypothesis-profile=ci"),
    )


GATES: tuple[Gate, ...] = (
    Gate(
        name="suite",
        summary="the full pytest suite, 100% line + branch coverage of snapadmin/",
        steps=(
            Step(
                "python -m pytest --cov=snapadmin --cov-branch --cov-report=term-missing "
                "--cov-fail-under=100 --hypothesis-profile=ci"
            ),
        ),
        ci="test.yml · the Python × Django matrix (6 jobs; this interpreter only here)",
        ci_job="test",
        needs_modules=("pytest", "pytest_cov", "hypothesis"),
    ),
    Gate(
        name="static",
        summary="Ruff lint + security rules, Ruff format, mypy",
        steps=(
            Step("ruff check snapadmin"),
            Step("ruff format --check snapadmin"),
            # The Django plugin boots demo.core.settings_test, which imports
            # from the repository root: without it on the path mypy reports
            # errors in demo/ that do not exist.
            Step("mypy", env={"PYTHONPATH": str(REPO_ROOT)}),
        ),
        ci="test.yml · static-analysis",
        ci_job="static-analysis",
        needs_modules=("ruff", "mypy"),
    ),
    Gate(
        name="deps",
        summary="the installed dependency set is consistent (pip check)",
        steps=(Step("python -m pip check"),),
        ci="test.yml · static-analysis",
        ci_job="static-analysis",
        needs_modules=("pip",),
    ),
    Gate(
        name="security",
        summary="the security regression suite (tests/security_regressions.py)",
        steps=(Step("python -m pytest -m security_regression"),),
        ci="test.yml · the matrix (inside the full suite)",
        needs_modules=("pytest",),
        # The full suite already runs these tests; by name, or in the release
        # report, it answers "do the security regressions pass" on its own line.
        default=False,
    ),
    Gate(
        name="e2e",
        summary="browser end-to-end, once per admin theme (Unfold, stock)",
        steps=(
            Step("python -m pytest -m e2e"),
            Step("python -m pytest -m e2e", env={"SNAPADMIN_TEST_ADMIN_THEME": "stock"}),
        ),
        ci="test.yml · browser-e2e",
        ci_job="browser-e2e",
        needs_modules=("playwright",),
    ),
    Gate(
        name="services",
        summary="the whole suite on PostgreSQL, then the live-Elasticsearch tests",
        steps=(
            Step("python -m pytest --hypothesis-profile=ci"),
            Step("python -m pytest -m real_es"),
        ),
        ci="test.yml · real-services",
        ci_job="real-services",
        needs_env=SERVICE_VARIABLES,
        # The PostgreSQL run executes pg_dump and psql for real (the backup
        # round trip); the CI runner image ships both.
        needs_executables=("pg_dump", "psql"),
    ),
    Gate(
        name="lowest-deps",
        summary="the suite with every dependency at its declared minimum",
        steps=_lowest_steps(),
        ci="test.yml · lowest-deps (runs `python scripts/gates.py lowest-deps`)",
        ci_job="lowest-deps",
        needs_executables=("uv",),
        default=False,
    ),
    Gate(
        name="dist",
        summary="build the sdist and wheel, twine check --strict",
        steps=(
            Step("python -m build --outdir {dist}"),
            Step("python -m twine check --strict {dist}/*", glob_last=True),
        ),
        ci="publish.yml · build (on a version tag)",
        needs_modules=("build", "twine"),
        default=False,
    ),
)

GATES_BY_NAME = {gate.name: gate for gate in GATES}


# ─────────────────────────────────────────────────────────────────────────────
# Declared dependencies (pyproject.toml, read without tomllib — 3.10 has none)
# ─────────────────────────────────────────────────────────────────────────────

_DEPENDENCY_LINE = re.compile(
    r'^(?P<name>[A-Za-z0-9_.\-]+)\s*=\s*'
    r'(?:"(?P<plain>[^"]*)"|\{[^}]*?version\s*=\s*"(?P<table>[^"]*)"[^}]*\})\s*(?:#.*)?$'
)


def declared_dependencies(text: str | None = None) -> dict[str, str]:
    """``{name: specifier}`` of ``[tool.poetry.dependencies]``, ``python`` excluded.

    The table holds one ``name = "spec"`` or ``name = {version = "spec", …}``
    per line; ``tests/test_gates_script.py`` checks this reading against
    ``tomllib`` wherever the interpreter has it.
    """
    if text is None:
        text = PYPROJECT.read_text(encoding="utf-8")
    section = text.split("[tool.poetry.dependencies]", 1)[1]
    section = re.split(r"^\[", section, maxsplit=1, flags=re.MULTILINE)[0]
    dependencies = {}
    for line in section.splitlines():
        match = _DEPENDENCY_LINE.match(line.strip())
        if match and match["name"] != "python":
            dependencies[match["name"]] = match["plain"] or match["table"]
    return dependencies


def _release(version: str) -> tuple[int, ...]:
    """The numeric release segment, zero-padded to compare ``5.2`` with ``5.2.0``."""
    numbers = [int(part) for part in re.findall(r"\d+", version.split("+")[0])[:4]]
    return tuple(numbers + [0] * (4 - len(numbers)))


def lower_bound(specifier: str) -> str | None:
    """The version after ``>=`` in a specifier, or ``None`` if it has none."""
    match = re.search(r">=\s*([0-9][0-9A-Za-z.]*)", specifier)
    return match[1] if match else None


def lowest_mismatches(
    dependencies: Mapping[str, str], installed: Callable[[str], str | None]
) -> list[str]:
    """Each declared dependency not installed at exactly its declared minimum."""
    problems = []
    for name, specifier in sorted(dependencies.items()):
        minimum = lower_bound(specifier)
        if minimum is None:
            continue
        version = installed(name)
        if version is None:
            problems.append(f"{name}: declared {specifier}, not installed")
        elif _release(version) != _release(minimum):
            problems.append(
                f"{name}: declared {specifier}, but the lowest version that installs with "
                f"the rest is {version} — raise the declared minimum to it"
            )
    return problems


def _installed_version(name: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def lowest_requirements(dependencies: Mapping[str, str]) -> str:
    """A requirements file of every declared dependency, as declared.

    Resolved with ``uv pip install --resolution lowest-direct``, each one lands
    on the lowest version its specifier allows — the oldest release an adopter
    is told they may pin.
    """
    lines = [f"{name}{specifier}" for name, specifier in sorted(dependencies.items())]
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Running
# ─────────────────────────────────────────────────────────────────────────────

Runner = Callable[[Sequence[str], Mapping[str, str]], int]


def subprocess_runner(argv: Sequence[str], env: Mapping[str, str]) -> int:
    print(f"\n$ {shlex.join(argv)}", flush=True)
    # noqa: S603 - the argv comes from this module's own GATES table, no shell.
    return subprocess.run(list(argv), cwd=REPO_ROOT, env=dict(env), check=False).returncode  # noqa: S603


def argv_for(command: str) -> list[str]:
    """The command as it is run here: ``python`` is this interpreter, and
    ``ruff``/``mypy`` run as its modules, so the gate uses the tools of the
    environment it was started from, not whatever is first on ``PATH``."""
    argv = shlex.split(command)
    if argv[0] == "python":
        argv[0] = sys.executable
    elif argv[0] in {"ruff", "mypy"}:
        argv = [sys.executable, "-m", *argv]
    return argv


def missing_requirements(gate: Gate, environ: Mapping[str, str]) -> list[str]:
    """What this machine lacks to run ``gate``; empty when it can run."""
    missing = [f"the {name} package" for name in gate.needs_modules if find_spec(name) is None]
    missing += [f"${name}" for name in gate.needs_env if not environ.get(name)]
    missing += [f"`{name}` on PATH" for name in gate.needs_executables if shutil.which(name) is None]
    return missing


@dataclass
class Outcome:
    gate: Gate
    status: str  # "passed" | "failed" | "not run"
    detail: str = ""
    seconds: float = 0.0


def run_gate(gate: Gate, runner: Runner, environ: Mapping[str, str]) -> Outcome:
    missing = missing_requirements(gate, environ)
    if missing:
        return Outcome(gate, "not run", "needs " + ", ".join(missing))
    if not set(SERVICE_VARIABLES) & set(gate.needs_env):
        # CI gives the databases to `real-services` alone; every other job runs
        # on SQLite, so a gate exported for the services run must not switch the
        # rest onto PostgreSQL.
        environ = {key: value for key, value in environ.items() if key not in SERVICE_VARIABLES}
    started = time.monotonic()
    needs_scratch = any("{dist}" in step.command for step in gate.steps)
    with tempfile.TemporaryDirectory(prefix="snapadmin-gate-") if needs_scratch else _no_scratch() as dist:
        for step in gate.steps:
            argv = argv_for(step.command.replace("{dist}", shlex.quote(dist or "")))
            if step.glob_last:
                # A glob is the shell's job in CI; expand it here the same way.
                pattern = Path(argv.pop())
                argv += sorted(str(path) for path in pattern.parent.glob(pattern.name))
            code = runner(argv, {**environ, **step.env})
            if code != 0:
                return Outcome(
                    gate, "failed", f"`{step.command}` exited {code}", time.monotonic() - started
                )
    return Outcome(gate, "passed", "", time.monotonic() - started)


@contextmanager
def _no_scratch() -> Iterator[None]:
    yield None


def select(names: Sequence[str], release: bool) -> list[Gate]:
    if names:
        unknown = [name for name in names if name not in GATES_BY_NAME]
        if unknown:
            raise SystemExit(
                f"unknown gate(s): {', '.join(unknown)} — known: {', '.join(GATES_BY_NAME)}"
            )
        return [GATES_BY_NAME[name] for name in names]
    return [gate for gate in GATES if (gate.release if release else gate.default)]


def report(outcomes: Sequence[Outcome], strict: bool, release: bool = False) -> int:
    """Print the summary table; the exit code: 1 on a failure, or — when
    ``strict`` — on a gate that did not run.

    Strict is ``--release``, and any run that names its gates: asking for a gate
    by name (CI's ``lowest-deps`` job does) and having it silently not run would
    be a green result for a check nobody performed."""
    print("\nGates")
    print("─" * 78)
    for outcome in outcomes:
        timing = f"{outcome.seconds:6.1f}s" if outcome.status != "not run" else "       "
        line = f"{outcome.status.upper():8} {timing}  {outcome.gate.name:12} {outcome.gate.summary}"
        print(line)
        if outcome.detail:
            print(f"{'':18}{outcome.detail}")
    print("─" * 78)
    print("Not reproducible here: the Python × Django matrix — CI runs `suite` on six pairs.")
    failed = [o for o in outcomes if o.status == "failed"]
    not_run = [o for o in outcomes if o.status == "not run"]
    if not_run and strict:
        label = "RELEASE GATE FAILED" if release else "FAILED"
        print(f"{label}: gate(s) asked for could not run — "
              + ", ".join(o.gate.name for o in not_run) + ".")
    return 1 if failed or (strict and not_run) else 0


def print_list() -> None:
    for gate in GATES:
        where = []
        if gate.default:
            where.append("default")
        if gate.release:
            where.append("--release")
        print(f"{gate.name} — {gate.summary}")
        print(f"    CI: {gate.ci}")
        print(f"    in: {', '.join(where) or 'by name only'}")
        for step in gate.steps:
            env = " ".join(f"{key}={value}" for key, value in step.env.items())
            print(f"    $ {(env + ' ') if env else ''}{step.command}")
        needs = [*gate.needs_modules, *(f"${n}" for n in gate.needs_env), *gate.needs_executables]
        if needs:
            print(f"    needs: {', '.join(needs)}")


def main(argv: Sequence[str] | None = None, runner: Runner = subprocess_runner) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["lowest-requirements"]:
        parser = argparse.ArgumentParser(prog="gates.py lowest-requirements")
        parser.add_argument("--output", type=Path)
        options = parser.parse_args(arguments[1:])
        text = lowest_requirements(declared_dependencies())
        if options.output:
            options.output.parent.mkdir(parents=True, exist_ok=True)
            options.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0

    if arguments[:1] == ["verify-lowest"]:
        problems = lowest_mismatches(declared_dependencies(), _installed_version)
        for problem in problems:
            print(problem)
        if not problems:
            print("every declared dependency is installed at its declared minimum")
        return 1 if problems else 0

    parser = argparse.ArgumentParser(description="Run every check CI runs.")
    parser.add_argument("gates", nargs="*", help="gate names (default: the default set)")
    parser.add_argument("--release", action="store_true",
                        help="every gate; a gate that cannot run fails the run")
    parser.add_argument("--list", action="store_true", help="describe the gates and exit")
    options = parser.parse_args(arguments)
    if options.list:
        print_list()
        return 0
    gates = select(options.gates, options.release)
    outcomes = [run_gate(gate, runner, os.environ) for gate in gates]
    return report(outcomes, strict=options.release or bool(options.gates), release=options.release)


if __name__ == "__main__":
    raise SystemExit(main())
