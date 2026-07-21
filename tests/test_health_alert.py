"""
Tests for the subsystem health-alert email stack (``snapadmin.health``):

  diagnostics health probes → send_health_alert → email (Celery task +
  ``snapadmin_health_alert`` management command), with a cache-based cooldown.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import override_settings
from unittest.mock import patch

from snapadmin import health
from snapadmin.health import (
    HEALTH_ALERT_COOLDOWN_CACHE_KEY,
    get_health_config,
    run_health_probes,
    send_health_alert,
)

RECIPIENTS = ["ops@example.com"]

_DOWN = [{"name": "database", "title": "Database", "ok": False, "data": {"error": "connection refused"}}]
_OK = [
    {"name": "database", "title": "Database", "ok": True, "data": {"ok": True}},
    {"name": "elasticsearch", "title": "Elasticsearch", "ok": None, "data": {"enabled": False}},
]


@pytest.fixture(autouse=True)
def _clean_state():
    cache.delete(HEALTH_ALERT_COOLDOWN_CACHE_KEY)
    mail.outbox = []
    yield
    cache.delete(HEALTH_ALERT_COOLDOWN_CACHE_KEY)


class TestConfig:
    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=["health@example.com"],
                       SNAPADMIN_ERROR_ALERT_EMAILS=["errors@example.com"])
    def test_dedicated_recipients_win(self):
        assert get_health_config().emails == ["health@example.com"]

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=[],
                       SNAPADMIN_ERROR_ALERT_EMAILS=["errors@example.com"])
    def test_falls_back_to_error_alert_recipients(self):
        # No dedicated list → reuse the error-alert recipients so it needn't be set twice.
        assert get_health_config().emails == ["errors@example.com"]


@pytest.mark.django_db
class TestRunHealthProbes:
    def test_probes_the_real_database(self):
        probes = run_health_probes()
        by_name = {p["name"]: p for p in probes}
        assert "database" in by_name
        assert by_name["database"]["ok"] is True  # sqlite test DB is reachable


class TestSendHealthAlert:
    def test_disabled_does_not_send(self):
        with override_settings(SNAPADMIN_HEALTH_ALERT_ENABLED=False):
            with patch.object(health, "run_health_probes", return_value=_DOWN):
                summary = send_health_alert()
        assert summary == {"sent": False, "reason": "disabled", "checked": 1, "failing": 1}
        assert mail.outbox == []

    def test_all_healthy_sends_nothing_and_clears_cooldown(self):
        cache.set(HEALTH_ALERT_COOLDOWN_CACHE_KEY, "x", timeout=600)
        with patch.object(health, "run_health_probes", return_value=_OK):
            summary = send_health_alert()
        assert summary["sent"] is False
        assert summary["reason"] == "healthy"
        assert summary["failing"] == 0
        assert mail.outbox == []
        # A recovery re-arms the cooldown so the next outage alerts immediately.
        assert cache.get(HEALTH_ALERT_COOLDOWN_CACHE_KEY) is None

    def test_failing_without_recipients_logs_and_skips(self):
        with override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[]):
            with patch.object(health, "run_health_probes", return_value=_DOWN):
                summary = send_health_alert()
        assert summary["sent"] is False
        assert summary["reason"] == "no_recipients"
        assert mail.outbox == []

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_failing_sends_email(self):
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            summary = send_health_alert()
        assert summary["sent"] is True
        assert summary["failing"] == 1
        assert summary["failing_names"] == "database"
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == RECIPIENTS
        assert "Health alert" in message.subject
        assert "database" in message.subject
        assert "connection refused" in message.body

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_cooldown_suppresses_second_alert(self):
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            first = send_health_alert()
            second = send_health_alert()
        assert first["sent"] is True
        assert second["sent"] is False
        assert second["reason"] == "cooldown"
        assert len(mail.outbox) == 1

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_force_bypasses_cooldown(self):
        cache.set(HEALTH_ALERT_COOLDOWN_CACHE_KEY, "x", timeout=600)
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            summary = send_health_alert(force=True)
        assert summary["sent"] is True
        assert len(mail.outbox) == 1

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_force_arms_cooldown_for_the_next_run(self):
        # A forced send with no active cooldown must still arm it, so the next
        # scheduled (non-forced) run doesn't fire a second alert immediately.
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            first = send_health_alert(force=True)
            second = send_health_alert()
        assert first["sent"] is True
        assert second["sent"] is False
        assert second["reason"] == "cooldown"
        assert len(mail.outbox) == 1

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_multiple_failures_pluralise_subject(self):
        two_down = _DOWN + [
            {"name": "elasticsearch", "title": "Elasticsearch", "ok": False, "data": {"error": "no route"}}
        ]
        with patch.object(health, "run_health_probes", return_value=two_down):
            summary = send_health_alert()
        assert summary["failing"] == 2
        assert "2 subsystems down" in mail.outbox[0].subject


class TestManagementCommand:
    def test_command_reports_healthy(self):
        out = _StringIO()
        with patch.object(health, "run_health_probes", return_value=_OK):
            call_command("snapadmin_health_alert", stdout=out)
        assert "OK" in out.getvalue()
        assert mail.outbox == []

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_command_sends_and_exits_nonzero_when_unhealthy(self):
        out = _StringIO()
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            with pytest.raises(CommandError, match="1 health probe"):
                call_command("snapadmin_health_alert", stdout=out)
        assert "Health alert sent" in out.getvalue()
        assert len(mail.outbox) == 1

    def test_command_reports_not_sent_reason_when_no_recipients(self):
        out = _StringIO()
        with override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[]):
            with patch.object(health, "run_health_probes", return_value=_DOWN):
                with pytest.raises(CommandError):
                    call_command("snapadmin_health_alert", stdout=out)
        assert "not sent (no_recipients)" in out.getvalue()

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_command_force_flag(self):
        out = _StringIO()
        cache.set(HEALTH_ALERT_COOLDOWN_CACHE_KEY, "x", timeout=600)
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            with pytest.raises(CommandError):
                call_command("snapadmin_health_alert", "--force", stdout=out)
        assert len(mail.outbox) == 1


class TestCeleryTask:
    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS)
    def test_task_runs_and_returns_summary(self):
        from snapadmin.tasks import send_health_alert as task

        with patch.object(health, "run_health_probes", return_value=_DOWN):
            summary = task()
        assert summary["sent"] is True
        assert len(mail.outbox) == 1


def _StringIO():
    from io import StringIO

    return StringIO()
