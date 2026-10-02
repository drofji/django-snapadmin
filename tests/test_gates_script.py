"""
tests/test_gates_script.py

``scripts/gates.py`` — one command that reproduces every check CI runs (#QA1f).

The script is only worth having while it stays equal to CI, so the first half
of this file is the parity check, generated from the workflow itself: every
command a ``test.yml`` job runs is either environment setup or a step of a gate,
and every gate that names a CI job has each of its commands in that job. A new
CI step without a gate, or a gate whose command CI stopped running, fails here.

The rest pins the runner's contract without running the suite recursively: a
fake runner records what would have run and answers with a chosen exit code.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from functools import cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"

_spec = importlib.util.spec_from_file_location("snapadmin_gates", REPO_ROOT / "scripts" / "gates.py")
gates = importlib.util.module_from_spec(_spec)
sys.modules["snapadmin_gates"] = gates
_spec.loader.exec_module(gates)

#: Commands that prepare a CI runner rather than check anything.
_SETUP = re.compile(
    r"^(python -m pip install |pip install |sudo apt-get |python --version$|psql --version$"
    r"|python -c |python -m playwright install )"
)


@cache
def _ci_commands() -> tuple[tuple[str, str, dict[str, str]], ...]:
    """``(job, command, env)`` for every non-setup command of ``test.yml``."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    commands = []
    for job, spec in workflow["jobs"].items():
        for step in spec["steps"]:
            for line in str(step.get("run", "")).splitlines():
                line = line.strip()
                if line and not _SETUP.match(line):
                    commands.append((job, line, dict(step.get("env", {}))))
    return tuple(commands)


def _gate_commands() -> set[str]:
    return {step.command for gate in gates.GATES for step in gate.steps}


class FakeRunner:
    def __init__(self, exit_codes: dict[str, int] | None = None):
        self.exit_codes = exit_codes or {}
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        command = " ".join(argv)
        return next((code for key, code in self.exit_codes.items() if key in command), 0)


# ─────────────────────────────────────────────────────────────────────────────
# Parity with CI
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("job, command, env", _ci_commands(), ids=lambda v: str(v)[:50])
def test_every_ci_check_is_reproduced_by_a_gate(job, command, env):
    runs_a_gate = re.fullmatch(r"python scripts/gates\.py ([\w-]+)", command)
    if runs_a_gate:
        assert runs_a_gate[1] in gates.GATES_BY_NAME
        assert gates.GATES_BY_NAME[runs_a_gate[1]].ci_job == job
        return

    assert command in _gate_commands(), (
        f"test.yml job {job!r} runs {command!r}, which no gate in scripts/gates.py "
        f"reproduces. Add it to the gate that matches the job (or to the setup "
        f"pattern here, if it only prepares the runner)."
    )


@pytest.mark.parametrize(
    "gate", [gate for gate in gates.GATES if gate.ci_job], ids=lambda gate: gate.name
)
def test_every_gate_that_names_a_ci_job_is_what_that_job_runs(gate):
    job_commands = [(command, env) for job, command, env in _ci_commands() if job == gate.ci_job]
    if (f"python scripts/gates.py {gate.name}", {}) in job_commands:
        return

    for step in gate.steps:
        matching = [env for command, env in job_commands if command == step.command]
        assert matching, f"gate {gate.name!r}: CI job {gate.ci_job!r} does not run {step.command!r}"
        assert any(
            all(env.get(key) is not None for key in step.env) for env in matching
        ), f"gate {gate.name!r}: {step.command!r} runs in CI without {sorted(step.env)}"


def test_the_ci_jobs_all_have_a_gate():
    jobs = {job for job, _, _ in _ci_commands()}
    claimed = {gate.ci_job for gate in gates.GATES}

    assert jobs <= claimed, f"CI jobs no gate reproduces: {sorted(jobs - claimed)}"


def test_mypy_runs_with_the_repository_on_the_path_in_both_places():
    """Without it the Django plugin cannot import the demo settings and mypy
    reports errors in demo/ that do not exist — the local run would disagree
    with CI for no reason in the code."""
    mypy = next(step for step in gates.GATES_BY_NAME["static"].steps if step.command == "mypy")

    assert mypy.env == {"PYTHONPATH": str(REPO_ROOT)}
    assert any(
        command == "mypy" and env.get("PYTHONPATH") == "${{ github.workspace }}"
        for _, command, env in _ci_commands()
    )


def test_the_release_build_checks_what_the_publish_workflow_checks():
    publish = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    dist = [step.command for step in gates.GATES_BY_NAME["dist"].steps]

    assert "python -m build" in publish and dist[0].startswith("python -m build")
    assert "twine check --strict" in publish and "twine check --strict" in dist[1]


# ─────────────────────────────────────────────────────────────────────────────
# Selecting and running gates
# ─────────────────────────────────────────────────────────────────────────────

def test_the_default_run_is_every_gate_except_the_slow_and_the_release_only():
    selected = [gate.name for gate in gates.select([], release=False)]

    assert selected == ["suite", "static", "deps", "e2e", "services"]


def test_the_release_run_is_every_gate():
    selected = [gate.name for gate in gates.select([], release=True)]

    assert selected == [gate.name for gate in gates.GATES]


def test_an_unknown_gate_is_refused_by_name():
    with pytest.raises(SystemExit, match="unknown gate\\(s\\): nope"):
        gates.select(["suite", "nope"], release=False)


def test_python_is_this_interpreter_and_the_tools_are_its_modules():
    assert gates.argv_for("python -m pytest -q") == [sys.executable, "-m", "pytest", "-q"]
    assert gates.argv_for("ruff check snapadmin") == [sys.executable, "-m", "ruff", "check", "snapadmin"]
    assert gates.argv_for("mypy") == [sys.executable, "-m", "mypy"]
    assert gates.argv_for("uv pip check") == ["uv", "pip", "check"]


@pytest.fixture
def tools_installed(monkeypatch):
    """Every module a gate needs, present — stated rather than assumed.

    ``run_gate`` asks ``find_spec`` before it runs a step. The tests below are
    about what happens *after* that check (the steps, their order, where a
    failure stops them), so they must not depend on which tools the running
    interpreter happens to carry: the lowest-deps environment has no pip, ruff
    or mypy, and there these tests reported "not run" instead of exercising
    the logic they name. Detection itself is pinned by the not-run tests.
    """
    monkeypatch.setattr(gates, "find_spec", lambda name: object())


def test_a_passing_gate_runs_every_step_with_its_environment(tools_installed):
    runner = FakeRunner()

    outcome = gates.run_gate(gates.GATES_BY_NAME["e2e"], runner, {"PLAYWRIGHT": "1"})

    assert outcome.status == "passed"
    assert [argv[-2:] for argv, _ in runner.calls] == [["-m", "e2e"], ["-m", "e2e"]]
    assert "SNAPADMIN_TEST_ADMIN_THEME" not in runner.calls[0][1]
    assert runner.calls[1][1]["SNAPADMIN_TEST_ADMIN_THEME"] == "stock"


def test_a_failing_step_fails_the_gate_and_stops_it(tools_installed):
    runner = FakeRunner({"ruff format": 1})

    outcome = gates.run_gate(gates.GATES_BY_NAME["static"], runner, {})

    assert outcome.status == "failed"
    assert outcome.detail == "`ruff format --check snapadmin` exited 1"
    assert len(runner.calls) == 2  # mypy never ran


def test_a_gate_missing_its_services_is_not_run_and_says_what_it_needs():
    runner = FakeRunner()

    outcome = gates.run_gate(gates.GATES_BY_NAME["services"], runner, {})

    assert outcome.status == "not run"
    assert outcome.detail == "needs $SNAPADMIN_TEST_POSTGRES, $SNAPADMIN_TEST_ES_URL"
    assert runner.calls == []


def test_a_gate_missing_an_executable_is_not_run(monkeypatch):
    monkeypatch.setattr(gates.shutil, "which", lambda name: None)

    outcome = gates.run_gate(gates.GATES_BY_NAME["lowest-deps"], FakeRunner(), {})

    assert outcome.status == "not run"
    assert outcome.detail == "needs `uv` on PATH"


def test_the_dist_gate_builds_into_a_scratch_directory_and_checks_what_it_built(monkeypatch):
    built = {}

    def runner(argv, env):
        if "build" in argv:
            outdir = Path(argv[argv.index("--outdir") + 1])
            (outdir / "pkg-1.0.tar.gz").write_text("sdist")
            (outdir / "pkg-1.0-py3-none-any.whl").write_text("wheel")
            built["dir"] = outdir
        else:
            built["checked"] = argv
        return 0

    monkeypatch.setattr(gates, "find_spec", lambda name: object())

    outcome = gates.run_gate(gates.GATES_BY_NAME["dist"], runner, {})

    assert outcome.status == "passed"
    assert built["checked"][-2:] == [
        str(built["dir"] / "pkg-1.0-py3-none-any.whl"),
        str(built["dir"] / "pkg-1.0.tar.gz"),
    ]
    assert not built["dir"].exists()  # the scratch directory is gone again


# ─────────────────────────────────────────────────────────────────────────────
# The report and the exit code
# ─────────────────────────────────────────────────────────────────────────────

def _outcome(name, status, detail=""):
    return gates.Outcome(gates.GATES_BY_NAME[name], status, detail, 1.0)


def test_a_not_run_gate_does_not_fail_an_ordinary_run(capsys):
    code = gates.report(
        [_outcome("suite", "passed"), _outcome("e2e", "not run", "needs x")], strict=False
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "NOT RUN" in output and "needs x" in output
    assert "Python × Django matrix" in output


def test_a_not_run_gate_fails_the_release_run(capsys):
    code = gates.report(
        [_outcome("suite", "passed"), _outcome("e2e", "not run", "needs x")],
        strict=True,
        release=True,
    )

    assert code == 1
    assert "RELEASE GATE FAILED: gate(s) asked for could not run — e2e." in capsys.readouterr().out


def test_a_gate_asked_for_by_name_that_cannot_run_fails(monkeypatch, capsys):
    """CI's lowest-deps job names its gate: if uv went missing, a pass there
    would be a green result for a check nobody performed."""
    monkeypatch.setattr(gates.shutil, "which", lambda name: None)
    runner = FakeRunner()

    code = gates.main(["lowest-deps"], runner=runner)

    assert code == 1
    assert runner.calls == []
    assert "FAILED: gate(s) asked for could not run — lowest-deps." in capsys.readouterr().out


def test_a_failed_gate_fails_any_run():
    assert gates.report([_outcome("suite", "failed", "boom")], strict=False) == 1


def test_a_gate_without_a_scratch_directory_is_given_none(tools_installed):
    runner = FakeRunner()

    gates.run_gate(gates.GATES_BY_NAME["deps"], runner, {})

    assert runner.calls[0][0] == [sys.executable, "-m", "pip", "check"]


def test_the_lowest_environment_quotes_every_path(monkeypatch):
    monkeypatch.setattr(gates, "REPO_ROOT", Path("/tmp/My Projects/snap"))
    monkeypatch.setattr(gates, "DEMO_REQUIREMENTS", Path("/tmp/My Projects/snap/demo/requirements.txt"))

    for step in gates._lowest_steps():
        for argument in gates.argv_for(step.command):
            assert not argument.startswith("Projects"), (step.command, argument)


def test_the_lowest_environment_checks_consistency_with_uv_not_pip():
    """A ``uv venv`` has no pip in it; ``python -m pip check`` there would fail
    every run for a reason that has nothing to do with the dependencies."""
    commands = [step.command for step in gates._lowest_steps()]

    assert any(command.startswith("uv pip check --python ") for command in commands)
    assert not any("-m pip " in command for command in commands)


def test_main_runs_the_named_gates_only(capsys, tools_installed):
    runner = FakeRunner()

    code = gates.main(["deps"], runner=runner)

    assert code == 0
    assert runner.calls[0][0] == [sys.executable, "-m", "pip", "check"]
    assert len(runner.calls) == 1


def test_list_describes_every_gate(capsys):
    assert gates.main(["--list"]) == 0

    output = capsys.readouterr().out
    for gate in gates.GATES:
        assert f"{gate.name} — {gate.summary}" in output


# ─────────────────────────────────────────────────────────────────────────────
# The lowest-dependency environment
# ─────────────────────────────────────────────────────────────────────────────

PYPROJECT_SAMPLE = '''
[tool.poetry.dependencies]
python = ">=3.10"
Django = ">=5.2"
nh3 = ">=0.2.0"   # a comment
elasticsearch = {version = ">=8.0.0,<9.0.0", optional = true}

[tool.poetry.extras]
search = ["elasticsearch"]
'''


def test_the_declared_dependencies_are_read_from_the_table_without_python():
    assert gates.declared_dependencies(PYPROJECT_SAMPLE) == {
        "Django": ">=5.2",
        "nh3": ">=0.2.0",
        "elasticsearch": ">=8.0.0,<9.0.0",
    }


def test_the_reader_agrees_with_tomllib_on_the_real_pyproject():
    tomllib = pytest.importorskip("tomllib")  # 3.11+; the reader exists for 3.10
    table = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["tool"]["poetry"]
    expected = {
        name: (spec if isinstance(spec, str) else spec["version"])
        for name, spec in table["dependencies"].items()
        if name != "python"
    }

    assert gates.declared_dependencies() == expected


def test_the_lowest_requirements_are_the_declared_specifiers_sorted():
    text = gates.lowest_requirements({"b": ">=2", "A": ">=1,<2"})

    assert text == "A>=1,<2\nb>=2\n"


def test_the_main_command_writes_the_lowest_requirements(tmp_path):
    target = tmp_path / "sub" / "lowest.txt"

    assert gates.main(["lowest-requirements", "--output", str(target)]) == 0

    assert target.read_text() == gates.lowest_requirements(gates.declared_dependencies())


@pytest.mark.parametrize(
    "specifier, expected",
    [(">=5.2", "5.2"), (">=8.0.0,<9.0.0", "8.0.0"), (">= 3.15.0", "3.15.0"), ("<2", None)],
)
def test_the_lower_bound_is_read_from_a_specifier(specifier, expected):
    assert gates.lower_bound(specifier) == expected


def test_an_environment_at_the_declared_minimums_passes():
    installed = {"Django": "5.2", "nh3": "0.2.0", "elasticsearch": "8.0.0"}.get

    problems = gates.lowest_mismatches(
        {"Django": ">=5.2.0", "nh3": ">=0.2", "elasticsearch": ">=8.0.0,<9.0.0"}, installed
    )

    assert problems == []


def test_a_minimum_the_resolver_had_to_raise_names_the_bound_to_correct():
    installed = {"celery": "5.4.0", "boto3": None}.get

    problems = gates.lowest_mismatches({"celery": ">=5.3.0", "boto3": ">=1.34.0"}, installed)

    assert problems == [
        "boto3: declared >=1.34.0, not installed",
        "celery: declared >=5.3.0, but the lowest version that installs with the rest is "
        "5.4.0 — raise the declared minimum to it",
    ]


def test_verify_lowest_exits_non_zero_on_a_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(gates, "declared_dependencies", lambda: {"celery": ">=5.3.0"})
    monkeypatch.setattr(gates, "_installed_version", lambda name: "5.4.0")

    assert gates.main(["verify-lowest"]) == 1
    assert "raise the declared minimum" in capsys.readouterr().out


def test_verify_lowest_passes_at_the_minimums(monkeypatch, capsys):
    monkeypatch.setattr(gates, "declared_dependencies", lambda: {"celery": ">=5.3.0"})
    monkeypatch.setattr(gates, "_installed_version", lambda name: "5.3.0")

    assert gates.main(["verify-lowest"]) == 0
    assert "every declared dependency" in capsys.readouterr().out


def test_the_installed_version_of_a_missing_package_is_none():
    assert gates._installed_version("no-such-package-anywhere") is None
    assert gates._installed_version("pytest") == pytest.__version__
