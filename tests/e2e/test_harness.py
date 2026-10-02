"""
tests/e2e/test_harness.py

The harness keeps a failed scenario's Playwright trace — including when the
failure is one only the harness sees.

The ``page`` fixture fails a test at teardown on an uncaught JavaScript error,
a missing asset or a 5xx. The ``context`` fixture, which owns the trace, tears
down after it and decided whether to keep the trace from the reports of the
setup and call phases — the teardown report does not exist yet at that point.
So exactly the failures the harness exists to catch left no trace behind, and
CI's upload-on-failure step found an empty directory (found in review, #QA1e).

Driven through a child pytest, because the scenario under test has to fail.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name("harness_probe.py")


def _outcome_counts(output: str) -> dict[str, int]:
    """The child run's final summary as ``{outcome: count}``, warnings left out.

    Parsed rather than matched as a substring: a warning the environment adds
    (a fresh checkout has no collected static directory, and whitenoise says
    so) turns "1 passed, 1 error" into "1 passed, 1 warning, 1 error", while a
    substring would also have accepted "1 failed, 1 passed, 1 error".
    """
    summary = output.strip().splitlines()[-1]
    counts = {
        outcome: int(number)
        for number, outcome in re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)", summary)
    }
    return {("error" if key == "errors" else key): value for key, value in counts.items()}


def test_a_teardown_failure_keeps_its_trace(tmp_path):
    artifacts = tmp_path / "artifacts"
    environment = {**os.environ, "SNAPADMIN_E2E_ARTIFACTS_DIR": str(artifacts)}

    child = subprocess.run(  # noqa: S603 - fixed argv, the running interpreter
        [
            sys.executable, "-m", "pytest", "-m", "e2e", str(PROBE),
            "-p", "no:randomly", "-p", "no:cacheprovider", "-q", "--no-cov",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    output = child.stdout + child.stderr
    assert child.returncode == 1, output
    assert _outcome_counts(output) == {"passed": 1, "error": 1}, output
    assert "uncaught JavaScript error(s) on the page" in output, output
    traces = sorted(path.name for path in artifacts.glob("*.zip"))
    assert traces == ["tests-e2e-harness_probe.py-test_a_page_that_throws.zip"], output
