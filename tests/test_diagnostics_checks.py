"""
tests/test_diagnostics_checks.py — the system-checks section of ``snapadmin_info`` (#CLI7c)

Django prints every system-check message in full before running any management command. For a
diagnostics command that buried the report behind a wall of advisory text — a project with a dozen
models saw a screen of near-identical SnapAdmin warnings before the first line it asked for.

``snapadmin_info`` opts out of the automatic run and reports a per-severity **count** instead, with
the message text shown only under ``--verbose`` — except errors, which are always listed because
they block a working deployment.
"""

import json
from io import StringIO

import pytest
from django.core import checks
from django.core.management import call_command

from snapadmin.diagnostics import checks as checks_collector


def _msg(level, text, identifier):
    return checks.CheckMessage(level, text, id=identifier)


@pytest.fixture
def fake_messages(monkeypatch):
    """Install a fixed set of check messages for the collector to summarise."""
    def install(messages):
        monkeypatch.setattr(
            checks_collector.checks, "run_checks",
            lambda include_deployment_checks=False: messages,
        )
    return install


class TestSummary:
    def test_clean_project_is_ok(self, fake_messages):
        fake_messages([])
        assert checks_collector.collect(verbose=False) == {"ok": True}

    def test_counts_per_severity(self, fake_messages):
        fake_messages([
            _msg(checks.WARNING, "w1", "snapadmin.W004"),
            _msg(checks.WARNING, "w2", "snapadmin.W007"),
            _msg(checks.INFO, "i1", "snapadmin.I001"),
        ])
        data = checks_collector.collect(verbose=False)
        assert data["warnings"] == 2
        assert data["infos"] == 1
        assert data["ok"] is True

    def test_counts_only_and_a_pointer_when_quiet(self, fake_messages):
        fake_messages([_msg(checks.WARNING, "a long advisory message", "snapadmin.W004")])
        data = checks_collector.collect(verbose=False)
        assert "messages" not in data
        assert "manage.py check" in data["detail"]

    def test_verbose_lists_every_message(self, fake_messages):
        fake_messages([
            _msg(checks.WARNING, "a long advisory message", "snapadmin.W004"),
            _msg(checks.INFO, "an informational note", "snapadmin.I001"),
        ])
        data = checks_collector.collect(verbose=True)
        assert data["messages"] == [
            "(snapadmin.W004) a long advisory message",
            "(snapadmin.I001) an informational note",
        ]
        assert "detail" not in data

    def test_message_without_an_id(self, fake_messages):
        fake_messages([checks.Warning("bare message")])
        assert checks_collector.collect(verbose=True)["messages"] == ["bare message"]


class TestErrorsAreNeverHidden:
    def test_error_fails_the_probe_and_is_listed(self, fake_messages):
        fake_messages([_msg(checks.ERROR, "broken", "snapadmin.E001")])
        data = checks_collector.collect(verbose=False)
        assert data["ok"] is False
        assert data["errors"] == 1
        assert data["messages"] == ["(snapadmin.E001) broken"]

    def test_critical_counts_separately(self, fake_messages):
        fake_messages([_msg(checks.CRITICAL, "fatal", "x.C001")])
        data = checks_collector.collect(verbose=False)
        assert data["critical"] == 1
        assert data["ok"] is False

    def test_warnings_alone_do_not_fail_the_probe(self, fake_messages):
        fake_messages([_msg(checks.WARNING, "advisory", "snapadmin.W004")])
        assert checks_collector.collect(verbose=False)["ok"] is True


@pytest.mark.django_db
class TestInTheCommand:
    def _run(self, **kwargs):
        out = StringIO()
        call_command("snapadmin_info", stdout=out, **kwargs)
        return out.getvalue()

    def test_command_does_not_run_djangos_own_check_pass(self):
        """requires_system_checks = [] — otherwise Django reprints every message first."""
        from snapadmin.management.commands.snapadmin_info import Command

        assert Command.requires_system_checks == []

    def test_section_is_rendered(self):
        assert "System checks" in self._run(sections=["checks"])

    def test_section_is_first(self):
        """It frames the rest of the report, so it leads — but as one short block."""
        text = self._run()
        assert text.splitlines()[0].endswith("System checks")

    def test_json_carries_the_summary(self):
        payload = json.loads(self._run(as_json=True, sections=["checks"]))
        assert payload["checks"]["ok"] is True

    def test_no_check_wall_above_the_report(self):
        text = self._run()
        # The full W004 hint text must not be reprinted for every model.
        assert "mass-assignment guard" not in text

    def test_health_check_includes_it(self):
        """A configuration error should fail a readiness probe."""
        from snapadmin.diagnostics import get_collector

        assert get_collector("checks").health_probe is True
