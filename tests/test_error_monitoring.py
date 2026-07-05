"""
Tests for the optional email error monitoring stack (v0.1.0a5):

  ErrorEvent model → SnapErrorMonitorMiddleware → spike alert → daily digest
  (Celery task + management command) → retention purge → read-only admin.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone

from snapadmin import monitoring
from snapadmin.middleware import SnapErrorMonitorMiddleware
from snapadmin.models import (
    ERROR_MESSAGE_MAX_LENGTH,
    ERROR_TRACEBACK_MAX_LENGTH,
    ErrorEvent,
)
from snapadmin.monitoring import (
    ALERT_COOLDOWN_CACHE_KEY,
    get_config,
    group_events,
    maybe_send_spike_alert,
    purge_expired_events,
    record_error,
    send_error_digest,
)

RECIPIENTS = ["ops@example.com"]


@pytest.fixture(autouse=True)
def _clean_state():
    cache.delete(ALERT_COOLDOWN_CACHE_KEY)
    mail.outbox = []
    yield
    cache.delete(ALERT_COOLDOWN_CACHE_KEY)


def _make_events(n, *, exception_class="ValueError", path="/api/x/", age=None):
    events = [
        ErrorEvent.record(exception_class=exception_class, path=path, message="boom")
        for _ in range(n)
    ]
    if age is not None:
        ErrorEvent.objects.filter(pk__in=[e.pk for e in events]).update(
            created_at=timezone.now() - age
        )
    return events


# ─────────────────────────────────────────────────────────────────────────────
# ErrorEvent model
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestErrorEventModel:
    def test_str_with_and_without_path(self):
        assert str(ErrorEvent.record(exception_class="ValueError", path="/x/")) == "ValueError @ /x/"
        assert str(ErrorEvent.record(exception_class="ValueError")) == "ValueError @ —"

    def test_fingerprint_groups_same_class_and_path(self):
        a = ErrorEvent.record(exception_class="ValueError", path="/x/")
        b = ErrorEvent.record(exception_class="ValueError", path="/x/")
        c = ErrorEvent.record(exception_class="TypeError", path="/x/")
        assert a.fingerprint == b.fingerprint != c.fingerprint
        assert ErrorEvent.fingerprint_for("ValueError", "/x/") == a.fingerprint

    def test_record_truncates_unbounded_inputs(self):
        event = ErrorEvent.record(
            exception_class="E" * 300,
            message="m" * 5000,
            path="/p/" + "x" * 600,
            method="OPTIONSXYZ99",
            traceback_text="t" * 20000,
        )
        assert len(event.exception_class) == 255
        assert len(event.message) == ERROR_MESSAGE_MAX_LENGTH
        assert len(event.path) == 500
        assert len(event.method) == 10
        assert len(event.traceback) == ERROR_TRACEBACK_MAX_LENGTH


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestConfig:
    def test_defaults(self):
        config = get_config()
        assert config.enabled is True
        assert config.alert_enabled is True
        assert config.alert_threshold == 20
        assert config.alert_window_minutes == 15
        assert config.alert_cooldown_minutes == 15
        assert config.alert_emails == []
        assert config.digest_enabled is True
        assert config.digest_emails == []
        assert config.digest_max_groups == 20
        assert config.retention_days == 30

    @override_settings(SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS)
    def test_digest_emails_fall_back_to_alert_emails(self):
        assert get_config().digest_emails == RECIPIENTS

    @override_settings(
        SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
        SNAPADMIN_ERROR_DIGEST_EMAILS=["digest@example.com"],
        SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES=5,
        SNAPADMIN_ERROR_ALERT_COOLDOWN_MINUTES=30,
    )
    def test_explicit_overrides(self):
        config = get_config()
        assert config.digest_emails == ["digest@example.com"]
        assert config.alert_window_minutes == 5
        assert config.alert_cooldown_minutes == 30


# ─────────────────────────────────────────────────────────────────────────────
# record_error
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRecordError:
    def test_records_exception_details(self):
        request = RequestFactory().post("/api/orders/")
        try:
            raise RuntimeError("kaput")
        except RuntimeError as exc:
            event = record_error(request=request, exception=exc)
        assert event.exception_class == "RuntimeError"
        assert event.message == "kaput"
        assert event.path == "/api/orders/"
        assert event.method == "POST"
        assert event.status_code == 500
        assert "RuntimeError: kaput" in event.traceback

    def test_records_5xx_without_exception(self):
        event = record_error(request=RequestFactory().get("/x/"), status_code=502)
        assert event.exception_class == "HTTP502"
        assert event.message == ""
        assert event.traceback == ""

    def test_records_without_request(self):
        event = record_error(status_code=500)
        assert event.path == ""
        assert event.method == ""

    @override_settings(SNAPADMIN_ERROR_MONITOR_ENABLED=False)
    def test_disabled_records_nothing(self):
        assert record_error(status_code=500) is None
        assert ErrorEvent.objects.count() == 0

    def test_storage_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            ErrorEvent, "record", classmethod(lambda cls, **kw: 1 / 0)
        )
        assert record_error(status_code=500) is None

    def test_alert_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            monitoring, "maybe_send_spike_alert", lambda **kw: 1 / 0
        )
        event = record_error(status_code=500)
        assert event is not None
        assert ErrorEvent.objects.count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Spike alert
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSpikeAlert:
    @override_settings(
        SNAPADMIN_ERROR_ALERT_THRESHOLD=3,
        SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
    )
    def test_alert_sent_once_threshold_crossed(self):
        _make_events(3)
        assert maybe_send_spike_alert() is True
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == RECIPIENTS
        assert "3 server errors" in message.subject
        assert "ValueError" in message.body
        assert "text/html" in message.alternatives[0][1]
        # Cooldown: a second crossing within the window stays silent
        assert maybe_send_spike_alert() is False
        assert len(mail.outbox) == 1

    @override_settings(
        SNAPADMIN_ERROR_ALERT_THRESHOLD=3,
        SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
    )
    def test_below_threshold_no_alert(self):
        _make_events(2)
        assert maybe_send_spike_alert() is False
        assert mail.outbox == []

    @override_settings(
        SNAPADMIN_ERROR_ALERT_THRESHOLD=3,
        SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
        SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES=15,
    )
    def test_old_errors_outside_window_ignored(self):
        _make_events(3, age=timedelta(minutes=30))
        assert maybe_send_spike_alert() is False

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=1)
    def test_no_recipients_no_alert(self):
        _make_events(1)
        assert maybe_send_spike_alert() is False
        assert mail.outbox == []

    @override_settings(
        SNAPADMIN_ERROR_ALERT_ENABLED=False,
        SNAPADMIN_ERROR_ALERT_THRESHOLD=1,
        SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
    )
    def test_alert_disabled(self):
        _make_events(1)
        assert maybe_send_spike_alert() is False

    @override_settings(
        SNAPADMIN_ERROR_ALERT_THRESHOLD=2,
        SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
    )
    def test_alert_fires_through_record_error(self):
        record_error(status_code=500)
        record_error(status_code=500)
        assert len(mail.outbox) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Grouping
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGroupEvents:
    def test_groups_ordered_by_frequency_and_capped(self):
        _make_events(5, exception_class="ValueError", path="/a/")
        _make_events(3, exception_class="TypeError", path="/b/")
        _make_events(1, exception_class="KeyError", path="/c/")

        groups, hidden_groups, hidden_events = group_events(
            ErrorEvent.objects.all(), max_groups=2
        )
        assert [g["exception_class"] for g in groups] == ["ValueError", "TypeError"]
        assert groups[0]["count"] == 5
        assert groups[0]["path"] == "/a/"
        assert groups[0]["first_seen"] <= groups[0]["last_seen"]
        assert hidden_groups == 1
        assert hidden_events == 1

    def test_no_cap_when_groups_fit(self):
        _make_events(2, exception_class="ValueError")
        groups, hidden_groups, hidden_events = group_events(
            ErrorEvent.objects.all(), max_groups=20
        )
        assert len(groups) == 1
        assert (hidden_groups, hidden_events) == (0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Daily digest + retention purge
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestErrorDigest:
    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=RECIPIENTS)
    def test_digest_sent_grouped_and_purges_expired(self):
        _make_events(4, exception_class="ValueError", path="/a/")
        _make_events(2, exception_class="TypeError", path="/b/")
        _make_events(1, age=timedelta(days=40))  # beyond retention

        summary = send_error_digest()
        assert summary == {
            "sent": True, "errors": 6, "groups": 2, "hidden_groups": 0, "purged": 1,
        }
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert "6 errors in 2 groups" in message.subject
        assert "ValueError" in message.body and "TypeError" in message.body
        assert not ErrorEvent.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=30)
        ).exists()

    @override_settings(
        SNAPADMIN_ERROR_DIGEST_EMAILS=RECIPIENTS,
        SNAPADMIN_ERROR_DIGEST_MAX_GROUPS=1,
    )
    def test_digest_group_cap_reported(self):
        _make_events(2, exception_class="ValueError", path="/a/")
        _make_events(1, exception_class="TypeError", path="/b/")
        summary = send_error_digest()
        assert summary["groups"] == 1
        assert summary["hidden_groups"] == 1
        assert "1 more group" in mail.outbox[0].body

    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=RECIPIENTS)
    def test_digest_window_excludes_older_errors(self):
        _make_events(1, age=timedelta(hours=30))
        summary = send_error_digest(hours=24)
        assert summary["sent"] is False
        assert summary["reason"] == "no_errors"

    @override_settings(
        SNAPADMIN_ERROR_DIGEST_ENABLED=False,
        SNAPADMIN_ERROR_DIGEST_EMAILS=RECIPIENTS,
    )
    def test_digest_disabled(self):
        _make_events(1)
        assert send_error_digest()["reason"] == "disabled"
        assert mail.outbox == []

    def test_digest_without_recipients(self):
        _make_events(1)
        assert send_error_digest()["reason"] == "no_recipients"
        assert mail.outbox == []

    @override_settings(SNAPADMIN_ERROR_RETENTION_DAYS=7)
    def test_purge_expired_events(self):
        _make_events(2, age=timedelta(days=8))
        _make_events(1)
        assert purge_expired_events() == 2
        assert ErrorEvent.objects.count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSnapErrorMonitorMiddleware:
    def test_process_exception_records_once(self):
        from django.http import HttpResponseServerError

        middleware = SnapErrorMonitorMiddleware(
            lambda request: HttpResponseServerError()
        )
        request = RequestFactory().get("/boom/")
        try:
            raise ValueError("bad")
        except ValueError as exc:
            middleware.process_exception(request, exc)
        response = middleware(request)  # 500 response after the exception
        assert response.status_code == 500
        assert ErrorEvent.objects.count() == 1  # no double count
        assert ErrorEvent.objects.get().exception_class == "ValueError"

    def test_5xx_response_without_exception_recorded(self):
        from django.http import HttpResponse

        middleware = SnapErrorMonitorMiddleware(
            lambda request: HttpResponse(status=503)
        )
        middleware(RequestFactory().get("/half-broken/"))
        event = ErrorEvent.objects.get()
        assert event.exception_class == "HTTP503"
        assert event.status_code == 503

    def test_success_response_not_recorded(self):
        from django.http import HttpResponse

        middleware = SnapErrorMonitorMiddleware(lambda request: HttpResponse("ok"))
        middleware(RequestFactory().get("/fine/"))
        assert ErrorEvent.objects.count() == 0

    @override_settings(DEBUG=True)  # the demo view is DEBUG-gated
    def test_full_stack_demo_error_view(self):
        client = Client(raise_request_exception=False)
        response = client.get("/demo/error/")
        assert response.status_code == 500
        event = ErrorEvent.objects.get()
        assert event.exception_class == "RuntimeError"
        assert event.path == "/demo/error/"


# ─────────────────────────────────────────────────────────────────────────────
# Management command + Celery task
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDigestEntryPoints:
    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=RECIPIENTS)
    def test_command_sends_digest(self):
        _make_events(2)
        out = StringIO()
        call_command("send_error_digest", stdout=out)
        assert "Digest sent: 2 errors in 1 groups" in out.getvalue()
        assert len(mail.outbox) == 1

    def test_command_reports_not_sent(self):
        out = StringIO()
        call_command("send_error_digest", "--hours", "12", stdout=out)
        assert "Digest not sent (no_errors)" in out.getvalue()
        assert mail.outbox == []

    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=RECIPIENTS)
    def test_celery_task(self):
        from snapadmin.api.tasks import send_error_digest as digest_task

        _make_events(1)
        result = digest_task.apply().result
        assert result["sent"] is True
        assert len(mail.outbox) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Admin
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestErrorEventAdmin:
    def _admin(self):
        from django.contrib import admin as django_admin
        from snapadmin.admin import ErrorEventAdmin
        from snapadmin.models import SnapModel

        SnapModel.register_all_admins()
        SnapModel.register_all_admins()  # second call → AlreadyRegistered branch
        assert ErrorEvent in django_admin.site._registry
        return ErrorEventAdmin(ErrorEvent, django_admin.site)

    def test_read_only(self):
        admin_instance = self._admin()
        request = RequestFactory().get("/admin/")
        assert admin_instance.has_add_permission(request) is False
        assert admin_instance.has_change_permission(request) is False

    def test_status_badge_and_short_message(self):
        admin_instance = self._admin()
        error_5xx = ErrorEvent.record(exception_class="ValueError", message="x" * 200)
        warn_4xx = ErrorEvent(exception_class="Http404", status_code=404, message="short")
        empty = ErrorEvent(exception_class="E", status_code=500, message="")

        assert admin_instance.status_badge(error_5xx) == ("500", "danger")
        assert admin_instance.status_badge(warn_4xx) == ("404", "warning")
        assert admin_instance.short_message(error_5xx).endswith("…")
        assert admin_instance.short_message(warn_4xx) == "short"
        assert admin_instance.short_message(empty) == "—"

    def test_changelist_renders(self, admin_client):
        ErrorEvent.record(exception_class="ValueError", path="/x/", message="boom")
        response = admin_client.get("/admin/snapadmin/errorevent/")
        assert response.status_code == 200
        assert b"ValueError" in response.content
