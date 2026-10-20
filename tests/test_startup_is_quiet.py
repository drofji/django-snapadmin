"""
tests/test_startup_is_quiet.py — nothing is swallowed into a log line at startup (#QA2e).

#EXT3h's model vanished from the GraphQL schema behind one ``graphql_model_skipped``
warning: a broad ``except`` turned a failure into a log line, and no test read the
log. This test starts the demo project the way a deployment does — ``django.setup()``
(app ``ready()``, admin registration), the URL configuration (which builds the
GraphQL schema and mounts the REST API) and ``manage.py check`` — in a fresh
process, records every structlog event, and fails on any warning or worse that is
not in a frozen allowlist with its reason.

The probe plants one warning of its own inside the recorded block, so a recording
that silently captures nothing (a level filter, a reconfigured logger) fails the
test instead of passing it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROBE = r"""
import io, json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.core.settings_test")
from django.conf import settings
settings.INSTALLED_APPS  # import the settings module, which configures logging
from snapadmin.logging_config import configure_logging, get_logger
configure_logging(log_level="DEBUG", json_logs=False)  # the test settings mute below CRITICAL
from structlog.testing import capture_logs
import django
with capture_logs() as captured:
    get_logger("startup_probe").warning("startup_probe_planted")
    django.setup()
    from django.urls import get_resolver
    get_resolver().url_patterns
    from django.core.management import call_command
    call_command("check", stdout=io.StringIO(), stderr=io.StringIO())
print(json.dumps([{key: str(value) for key, value in event.items()} for event in captured]))
"""

PLANTED = "startup_probe_planted"
LOUD = {"warning", "error", "critical", "exception"}

#: Startup warnings that are the intended outcome, each with its reason. Frozen at
#: empty; an entry is added deliberately, never to get green.
STARTUP_ALLOWLIST: dict[str, str] = {}


def _startup_events() -> list[dict[str, str]]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(  # noqa: S603 - our own interpreter, our own probe
        [sys.executable, "-c", PROBE],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_demo_starts_without_a_warning_in_its_log():
    events = _startup_events()
    loud = [
        (event.get("event"), event.get("log_level"))
        for event in events
        if event.get("log_level") in LOUD
    ]

    assert (PLANTED, "warning") in loud, "the probe recorded nothing — it is not listening"
    unexplained = [
        (name, level) for name, level in loud if name != PLANTED and name not in STARTUP_ALLOWLIST
    ]
    assert unexplained == [], (
        f"review: a startup path converted an error into a log line: {unexplained} — make it "
        "raise or a system check, or add it to STARTUP_ALLOWLIST in tests/test_startup_is_quiet.py "
        "with the reason"
    )
