"""
Tests for the alert delivery channels (``snapadmin.alerts``) and the two entry
points that use them:

  * ``snapadmin.monitoring`` — error spike alert and error digest,
  * ``snapadmin.health`` — subsystem health alert.

Three properties are pinned here beyond "the payload looks right":

  * **email stays one channel among several** — the existing thresholds,
    grouping and cooldowns are shared, never reimplemented per channel;
  * **fail-soft** — an unreachable webhook cannot break the caller, and never
    stops the other channels;
  * **webhook URLs are secrets** — they never reach a log line, a payload, an
    email body or ``snapadmin_info``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from io import StringIO
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import override_settings

from snapadmin import alerts, health, monitoring
from snapadmin.alerts import (
    ALERT_KIND_ERROR_DIGEST,
    ALERT_KIND_ERROR_SPIKE,
    ALERT_KIND_HEALTH,
    Alert,
    AlertDeliveryError,
    DiscordChannel,
    EmailChannel,
    JsonWebhookChannel,
    SlackChannel,
    TeamsChannel,
    TelegramChannel,
    arm_cooldown,
    build_channels,
    build_webhook_channel,
    dispatch,
    get_webhook_channels,
    mask_webhook_url,
    post_json,
    release_cooldown,
)
from snapadmin.health import HEALTH_ALERT_COOLDOWN_CACHE_KEY, send_health_alert
from snapadmin.models import ErrorEvent
from snapadmin.monitoring import (
    ALERT_COOLDOWN_CACHE_KEY,
    group_lines,
    maybe_send_spike_alert,
    send_error_digest,
)

SLACK_URL = "https://hooks.slack.com/services/T00000/B00000/SuperSecretToken"
DISCORD_URL = "https://discord.com/api/webhooks/123456/AnotherSecretToken"
TEAMS_URL = "https://acme.webhook.office.com/webhookb2/abc/IncomingWebhook/def/ghi"
TELEGRAM_TOKEN = "123456:AAF-SecretBotToken"
RECIPIENTS = ["ops@example.com"]

SLACK = {"type": "slack", "url": SLACK_URL}
TELEGRAM = {"type": "telegram", "token": TELEGRAM_TOKEN, "chat_id": "-100999"}

_DOWN = [
    {"name": "database", "title": "Database", "ok": False, "data": {"error": "connection refused"}}
]


@pytest.fixture(autouse=True)
def _clean_state():
    cache.delete(ALERT_COOLDOWN_CACHE_KEY)
    cache.delete(HEALTH_ALERT_COOLDOWN_CACHE_KEY)
    mail.outbox = []
    yield
    cache.delete(ALERT_COOLDOWN_CACHE_KEY)
    cache.delete(HEALTH_ALERT_COOLDOWN_CACHE_KEY)


def _alert(**kwargs) -> Alert:
    defaults = {
        "kind": ALERT_KIND_ERROR_SPIKE,
        "subject": "[SnapAdmin] 3 server errors",
        "summary": "3 errors in 15 min (threshold 3).",
        "lines": ("3× ValueError — GET /api/x/",),
    }
    return Alert(**{**defaults, **kwargs})


@contextmanager
def _captured_posts(*, status: int = 200, error: Exception | None = None):
    """Patch ``urlopen`` and collect every (url, payload, timeout) posted."""
    posted: list[dict] = []

    def _urlopen(request, timeout=None):
        posted.append(
            {
                "url": request.full_url,
                "payload": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
                "headers": dict(request.header_items()),
                "method": request.get_method(),
            }
        )
        if error is not None:
            raise error
        response = MagicMock()
        response.__enter__.return_value.status = status
        return response

    with patch.object(alerts.urllib_request, "urlopen", side_effect=_urlopen):
        yield posted


def _make_events(n: int, *, exception_class: str = "ValueError", path: str = "/api/x/"):
    return [
        ErrorEvent.record(exception_class=exception_class, path=path, message="boom")
        for _ in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# The alert payload
# ─────────────────────────────────────────────────────────────────────────────

class TestAlert:
    def test_as_text_renders_subject_summary_and_bullets(self):
        text = _alert().as_text()
        assert text.splitlines() == [
            "[SnapAdmin] 3 server errors",
            "3 errors in 15 min (threshold 3).",
            "• 3× ValueError — GET /api/x/",
        ]

    def test_as_text_without_summary_or_lines(self):
        assert _alert(summary="", lines=()).as_text() == "[SnapAdmin] 3 server errors"

    def test_delivery_result_reports_what_happened(self):
        result = alerts.DeliveryResult(delivered=("email",), failed=("slack",))
        assert result.any_delivered is True
        assert result.attempted == 2
        assert alerts.DeliveryResult().any_delivered is False


# ─────────────────────────────────────────────────────────────────────────────
# Secret handling — a webhook URL is a credential
# ─────────────────────────────────────────────────────────────────────────────

class TestMaskWebhookUrl:
    def test_drops_the_path_because_the_path_is_the_secret(self):
        assert mask_webhook_url(SLACK_URL) == "https://hooks.slack.com/…"

    def test_keeps_a_non_default_port(self):
        masked = mask_webhook_url("https://hooks.internal:8443/x/y")
        assert masked == "https://hooks.internal:8443/…"

    def test_masks_a_query_string_only_url(self):
        assert mask_webhook_url("https://example.com/?token=abc") == "https://example.com/…"

    def test_bare_host_has_nothing_to_hide(self):
        assert mask_webhook_url("https://example.com") == "https://example.com"

    @pytest.mark.parametrize("url", ["", "not-a-url", "://nohost"])
    def test_unparseable_input_reveals_nothing(self, url):
        assert mask_webhook_url(url) in ("", "…")

    def test_telegram_channel_target_hides_the_bot_token(self):
        channel = TelegramChannel(token=TELEGRAM_TOKEN, chat_id="-100999")
        assert TELEGRAM_TOKEN not in channel.target
        assert channel.target == "https://api.telegram.org/…"

    def test_email_channel_target_is_a_count_not_an_address(self):
        assert EmailChannel(recipients=RECIPIENTS).target == "1 recipient(s)"

    def test_base_channel_target_falls_back_to_the_name(self):
        assert alerts.AlertChannel().target == "channel"


# ─────────────────────────────────────────────────────────────────────────────
# Per-provider payloads
# ─────────────────────────────────────────────────────────────────────────────

class TestPayloads:
    def test_slack_uses_mrkdwn_bold(self):
        payload = SlackChannel(url=SLACK_URL).payload(_alert())
        assert payload == {
            "text": "*[SnapAdmin] 3 server errors*\n"
                    "3 errors in 15 min (threshold 3).\n"
                    "• 3× ValueError — GET /api/x/"
        }

    def test_discord_uses_markdown_bold_in_content(self):
        payload = DiscordChannel(url=DISCORD_URL).payload(_alert())
        assert payload["content"].startswith("**[SnapAdmin] 3 server errors**")

    def test_teams_posts_a_message_card_coloured_by_severity(self):
        payload = TeamsChannel(url=TEAMS_URL).payload(_alert(severity="warning"))
        assert payload["@type"] == "MessageCard"
        assert payload["title"] == "[SnapAdmin] 3 server errors"
        assert payload["themeColor"] == TeamsChannel.THEME_COLORS["warning"]
        assert "- 3× ValueError — GET /api/x/" in payload["text"]

    def test_teams_falls_back_to_the_error_colour(self):
        payload = TeamsChannel(url=TEAMS_URL).payload(_alert(severity="nonsense"))
        assert payload["themeColor"] == TeamsChannel.THEME_COLORS["error"]

    def test_telegram_sends_plain_text_to_the_chat(self):
        payload = TelegramChannel(token=TELEGRAM_TOKEN, chat_id="-100999").payload(_alert())
        assert payload["chat_id"] == "-100999"
        assert payload["disable_web_page_preview"] is True
        # No parse_mode: markdown punctuation in an exception message can't
        # make Telegram reject the post.
        assert "parse_mode" not in payload

    def test_json_channel_posts_the_structured_alert(self):
        payload = JsonWebhookChannel(url="https://example.com/hook").payload(
            _alert(kind=ALERT_KIND_HEALTH, severity="error")
        )
        assert payload["source"] == "django-snapadmin"
        assert payload["kind"] == ALERT_KIND_HEALTH
        assert payload["lines"] == ["3× ValueError — GET /api/x/"]
        assert payload["sent_at"]

    def test_body_is_truncated_to_the_provider_limit(self):
        long_alert = _alert(lines=tuple(f"line {i} " + "x" * 100 for i in range(100)))
        content = DiscordChannel(url=DISCORD_URL).payload(long_alert)["content"]
        assert len(content) <= DiscordChannel.max_body_length
        assert content.endswith("… (truncated)")

    def test_an_uncapped_channel_keeps_the_whole_body(self):
        long_alert = _alert(lines=tuple(f"line {i}" for i in range(500)))
        payload = JsonWebhookChannel(url="https://example.com/hook").body(long_alert)
        assert "line 499" in payload


# ─────────────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────────────

class TestPostJson:
    def test_posts_json_with_the_configured_timeout(self):
        with _captured_posts() as posted:
            status = post_json("https://example.com/hook", {"a": 1}, timeout=2.5)
        assert status == 200
        assert posted == [
            {
                "url": "https://example.com/hook",
                "payload": {"a": 1},
                "timeout": 2.5,
                "headers": {"Content-type": "application/json",
                            "User-agent": "django-snapadmin"},
                "method": "POST",
            }
        ]

    def test_a_non_2xx_response_is_a_delivery_error(self):
        with _captured_posts(status=302):
            with pytest.raises(AlertDeliveryError, match="HTTP 302"):
                post_json("https://example.com/hook", {}, timeout=1)

    def test_a_transport_failure_propagates_to_the_dispatcher(self):
        with _captured_posts(error=urllib_error.URLError("no route to host")):
            with pytest.raises(urllib_error.URLError):
                post_json("https://example.com/hook", {}, timeout=1)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

class TestChannelConfiguration:
    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK, TELEGRAM])
    def test_builds_every_configured_channel(self):
        channels = get_webhook_channels(kind=ALERT_KIND_ERROR_SPIKE)
        assert [channel.name for channel in channels] == ["slack", "telegram"]

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[
        {"type": "slack", "url": SLACK_URL, "events": ["health"]},
        {"type": "discord", "url": DISCORD_URL},
    ])
    def test_events_filter_subscribes_a_channel_to_one_kind(self):
        assert [c.name for c in get_webhook_channels(kind=ALERT_KIND_HEALTH)] == [
            "slack", "discord",
        ]
        assert [c.name for c in get_webhook_channels(kind=ALERT_KIND_ERROR_SPIKE)] == ["discord"]

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[
        {"type": "slack", "url": SLACK_URL, "events": ["helth"]},
    ])
    def test_a_misspelled_event_is_logged_not_silently_swallowed(self):
        with patch.object(alerts, "logger") as log:
            assert get_webhook_channels(kind=ALERT_KIND_HEALTH) == []
        assert log.warning.call_args[0][0] == "alert_webhook_unknown_events"

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[{"type": "slack", "url": SLACK_URL,
                                                  "timeout": 12}])
    def test_per_entry_timeout_wins(self):
        assert get_webhook_channels(kind=ALERT_KIND_HEALTH)[0].timeout == 12.0

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK], SNAPADMIN_ALERT_WEBHOOK_TIMEOUT=9)
    def test_global_timeout_setting_applies(self):
        assert get_webhook_channels(kind=ALERT_KIND_HEALTH)[0].timeout == 9.0

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK], SNAPADMIN_ALERT_WEBHOOK_TIMEOUT="soon")
    def test_an_unparseable_timeout_falls_back_to_the_default(self):
        with patch.object(alerts, "logger") as log:
            channel = get_webhook_channels(kind=ALERT_KIND_HEALTH)[0]
        assert channel.timeout == alerts.DEFAULT_WEBHOOK_TIMEOUT_SECONDS
        assert log.warning.call_args[0][0] == "alert_webhook_timeout_invalid"

    @pytest.mark.parametrize("entry,expected_log", [
        ({"type": "carrier-pigeon", "url": SLACK_URL}, "alert_webhook_unknown_type"),
        ({"url": SLACK_URL}, "alert_webhook_unknown_type"),
        ({"type": "slack"}, "alert_webhook_url_invalid"),
        ({"type": "slack", "url": "ftp://example.com/x"}, "alert_webhook_url_invalid"),
        ({"type": "telegram", "chat_id": "1"}, "alert_webhook_telegram_incomplete"),
        ({"type": "telegram", "token": TELEGRAM_TOKEN}, "alert_webhook_telegram_incomplete"),
        ("https://hooks.slack.com/services/x", "alert_webhook_entry_invalid"),
    ])
    def test_a_broken_entry_is_skipped_and_logged(self, entry, expected_log):
        with patch.object(alerts, "logger") as log:
            assert build_webhook_channel(entry, index=0) is None
        assert log.warning.call_args[0][0] == expected_log

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS={"type": "slack", "url": SLACK_URL})
    def test_a_setting_that_is_not_a_list_is_ignored(self):
        # A single dict instead of a list of dicts is the obvious typo; it must
        # not iterate into nonsense or raise inside a request.
        with patch.object(alerts, "logger") as log:
            assert get_webhook_channels(kind=ALERT_KIND_HEALTH) == []
        assert log.warning.call_args[0][0] == "alert_webhooks_setting_invalid"

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=SLACK_URL)
    def test_a_bare_url_string_is_ignored(self):
        assert get_webhook_channels(kind=ALERT_KIND_HEALTH) == []

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_email_and_webhooks_are_built_together_email_first(self):
        channels = build_channels(kind=ALERT_KIND_ERROR_SPIKE, recipients=RECIPIENTS)
        assert [channel.name for channel in channels] == ["email", "slack"]

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK], SNAPADMIN_ALERT_EMAIL_ENABLED=False)
    def test_email_can_be_switched_off_leaving_chat_only(self):
        channels = build_channels(kind=ALERT_KIND_ERROR_SPIKE, recipients=RECIPIENTS)
        assert [channel.name for channel in channels] == ["slack"]

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_webhooks_work_without_any_email_recipient(self):
        channels = build_channels(kind=ALERT_KIND_ERROR_SPIKE, recipients=[])
        assert [channel.name for channel in channels] == ["slack"]

    def test_nothing_configured_means_no_channels(self):
        assert build_channels(kind=ALERT_KIND_ERROR_SPIKE, recipients=[]) == []


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch — fail-soft, and never leaks the URL
# ─────────────────────────────────────────────────────────────────────────────

class TestDispatch:
    def test_delivers_to_every_channel(self):
        with _captured_posts() as posted:
            result = dispatch(_alert(), [SlackChannel(url=SLACK_URL),
                                         JsonWebhookChannel(url="https://example.com/hook")])
        assert result.delivered == ("slack", "json")
        assert result.failed == ()
        assert [post["url"] for post in posted] == [SLACK_URL, "https://example.com/hook"]

    def test_one_dead_channel_does_not_stop_the_others(self):
        class Exploding(alerts.AlertChannel):
            name = "exploding"

            def deliver(self, alert):
                raise RuntimeError("boom")

        with _captured_posts() as posted:
            result = dispatch(_alert(), [Exploding(), SlackChannel(url=SLACK_URL)])
        assert result.failed == ("exploding",)
        assert result.delivered == ("slack",)
        assert len(posted) == 1

    def test_dispatch_never_raises_when_everything_fails(self):
        with _captured_posts(error=urllib_error.URLError("timed out")):
            result = dispatch(_alert(), [SlackChannel(url=SLACK_URL)])
        assert result.any_delivered is False
        assert result.failed == ("slack",)

    def test_a_failure_log_never_contains_the_webhook_url(self):
        with patch.object(alerts, "logger") as log:
            failure = urllib_error.HTTPError(SLACK_URL, 404, "Not Found", {}, None)
            with _captured_posts(error=failure):
                dispatch(_alert(), [SlackChannel(url=SLACK_URL)])
        logged = json.dumps(log.warning.call_args.kwargs, ensure_ascii=False)
        assert "SuperSecretToken" not in logged
        assert logged.count("https://hooks.slack.com/…") == 1
        assert log.warning.call_args.kwargs["error"] == "HTTP 404"

    def test_a_url_error_is_logged_with_its_reason(self):
        with patch.object(alerts, "logger") as log:
            with _captured_posts(error=urllib_error.URLError("no route to host")):
                dispatch(_alert(), [SlackChannel(url=SLACK_URL)])
        assert "no route to host" in log.warning.call_args.kwargs["error"]

    def test_a_plain_exception_is_logged_by_type(self):
        class Exploding(alerts.AlertChannel):
            name = "exploding"

            def deliver(self, alert):
                raise ValueError("bad payload")

        with patch.object(alerts, "logger") as log:
            dispatch(_alert(), [Exploding()])
        assert log.warning.call_args.kwargs["error"] == "ValueError: bad payload"


# ─────────────────────────────────────────────────────────────────────────────
# The shared cooldown
# ─────────────────────────────────────────────────────────────────────────────

class TestCooldown:
    def test_arming_twice_only_succeeds_once(self):
        first = arm_cooldown("snapadmin:test-cooldown", minutes=5)
        second = arm_cooldown("snapadmin:test-cooldown", minutes=5)
        assert first is not None
        assert second is None
        cache.delete("snapadmin:test-cooldown")

    def test_release_frees_the_window_for_the_next_attempt(self):
        token = arm_cooldown("snapadmin:test-cooldown", minutes=5)
        release_cooldown("snapadmin:test-cooldown", token)
        assert cache.get("snapadmin:test-cooldown") is None
        assert arm_cooldown("snapadmin:test-cooldown", minutes=5) is not None
        cache.delete("snapadmin:test-cooldown")

    def test_release_only_drops_our_own_claim(self):
        # A concurrent worker that armed the window *and* delivered must keep it.
        our_token = arm_cooldown("snapadmin:test-cooldown", minutes=5)
        cache.set("snapadmin:test-cooldown", "someone-else", timeout=300)
        release_cooldown("snapadmin:test-cooldown", our_token)
        assert cache.get("snapadmin:test-cooldown") == "someone-else"
        cache.delete("snapadmin:test-cooldown")

    def test_releasing_a_window_we_never_armed_is_a_no_op(self):
        cache.set("snapadmin:test-cooldown", "held", timeout=300)
        release_cooldown("snapadmin:test-cooldown", None)
        assert cache.get("snapadmin:test-cooldown") == "held"
        cache.delete("snapadmin:test-cooldown")


# ─────────────────────────────────────────────────────────────────────────────
# Error spike alert through the channels
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSpikeAlertChannels:
    """The spike alert with `SNAPADMIN_ERROR_ALERT_THRESHOLD = 3` throughout."""

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=3, SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK, TELEGRAM])
    def test_email_and_webhooks_all_receive_the_same_spike(self):
        _make_events(3)
        with _captured_posts() as posted:
            assert maybe_send_spike_alert() is True
        assert len(mail.outbox) == 1
        assert [post["url"] for post in posted] == [
            SLACK_URL, f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        ]
        assert "3 server errors" in posted[0]["payload"]["text"]
        assert "3× ValueError" in posted[0]["payload"]["text"]

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=3, SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_the_cooldown_is_shared_by_the_channels_not_per_channel(self):
        # The point of #FUT2a: adding a webhook must not make alerting noisier.
        _make_events(3)
        with _captured_posts() as posted:
            assert maybe_send_spike_alert() is True
            assert maybe_send_spike_alert() is False
        assert len(posted) == 1
        assert len(mail.outbox) == 1

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=3, SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_a_webhook_alone_is_enough_to_alert(self):
        # Before channels existed this returned False: no email meant no alert.
        _make_events(3)
        with _captured_posts() as posted:
            assert maybe_send_spike_alert() is True
        assert len(posted) == 1
        assert mail.outbox == []

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=3, SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_a_dead_webhook_still_lets_the_email_through(self):
        _make_events(3)
        with _captured_posts(error=urllib_error.URLError("timed out")):
            assert maybe_send_spike_alert() is True
        assert len(mail.outbox) == 1

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=3, SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_a_failed_delivery_does_not_consume_the_cooldown(self):
        # #FUT2c: silence is the one thing a monitoring system must not produce.
        _make_events(3)
        with _captured_posts(error=urllib_error.URLError("timed out")):
            assert maybe_send_spike_alert() is False
        assert cache.get(ALERT_COOLDOWN_CACHE_KEY) is None
        with _captured_posts() as posted:
            assert maybe_send_spike_alert() is True
        assert len(posted) == 1

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=3, SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_recording_an_error_never_raises_because_of_a_webhook(self):
        _make_events(2)
        with _captured_posts(error=urllib_error.URLError("timed out")):
            event = monitoring.record_error(status_code=500)
        assert event is not None

    def test_group_lines_caps_the_chat_list_and_says_what_it_hid(self):
        groups = [
            {"count": 12 - i, "exception_class": f"Error{i}", "method": "GET", "path": f"/{i}/"}
            for i in range(12)
        ]
        lines = group_lines(groups, hidden_groups=3, hidden_events=30, max_lines=10)
        assert lines[0] == "12× Error0 — GET /0/"
        assert len(lines) == 11
        # 2 groups trimmed here (2 + 1 errors) + 3 groups the digest cap already
        # hid (30 errors) — the reader is told the list is not exhaustive.
        assert lines[-1] == "…and 5 more group(s) covering 33 error(s)"

    def test_group_lines_without_a_cap_adds_no_footer(self):
        groups = [{"count": 2, "exception_class": "ValueError", "method": "", "path": ""}]
        assert group_lines(groups) == ("2× ValueError — GET —",)


# ─────────────────────────────────────────────────────────────────────────────
# Error digest through the channels
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDigestChannels:
    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_digest_reaches_email_and_chat(self):
        _make_events(2)
        with _captured_posts() as posted:
            summary = send_error_digest()
        assert summary["sent"] is True
        assert summary["channels"] == "email,slack"
        assert len(mail.outbox) == 1
        assert "Error digest" in posted[0]["payload"]["text"]

    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[{"type": "discord", "url": DISCORD_URL,
                                                  "events": [ALERT_KIND_ERROR_DIGEST]}])
    def test_a_digest_only_webhook_receives_the_digest_but_not_a_spike(self):
        _make_events(2)
        with _captured_posts() as posted:
            summary = send_error_digest()
        assert summary["sent"] is True
        assert len(posted) == 1
        with _captured_posts() as spike_posts:
            with override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=1):
                assert maybe_send_spike_alert() is False
        assert spike_posts == []

    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_a_digest_nobody_accepted_reports_delivery_failed(self):
        _make_events(2)
        with _captured_posts(error=urllib_error.URLError("timed out")):
            summary = send_error_digest()
        assert summary["sent"] is False
        assert summary["reason"] == "delivery_failed"
        # The purge still happened — the digest run is not lost work.
        assert summary["purged"] == 0

    @override_settings(SNAPADMIN_ERROR_DIGEST_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[])
    def test_no_channel_at_all_is_still_reported_as_no_recipients(self):
        _make_events(2)
        summary = send_error_digest()
        assert summary["reason"] == "no_recipients"


# ─────────────────────────────────────────────────────────────────────────────
# Health alert through the channels
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthAlertChannels:
    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_a_failing_probe_reaches_email_and_chat(self):
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            with _captured_posts() as posted:
                summary = send_health_alert()
        assert summary["sent"] is True
        assert summary["channels"] == "email,slack"
        text = posted[0]["payload"]["text"]
        assert "Health alert" in text
        assert "Database: connection refused" in text

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[TELEGRAM])
    def test_chat_only_health_alerting_needs_no_mail_server(self):
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            with _captured_posts() as posted:
                summary = send_health_alert()
        assert summary["sent"] is True
        assert mail.outbox == []
        assert posted[0]["payload"]["chat_id"] == "-100999"

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_a_failed_delivery_releases_the_cooldown(self):
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            with _captured_posts(error=urllib_error.URLError("timed out")):
                summary = send_health_alert()
            assert summary["sent"] is False
            assert summary["reason"] == "delivery_failed"
            assert cache.get(HEALTH_ALERT_COOLDOWN_CACHE_KEY) is None
            # The next scheduled run must try again rather than assume it told someone.
            with _captured_posts() as posted:
                assert send_health_alert()["sent"] is True
        assert len(posted) == 1

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_the_health_command_survives_an_unreachable_webhook(self):
        out = StringIO()
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            with _captured_posts(error=urllib_error.URLError("timed out")):
                # The command still exits non-zero — but because a probe is down,
                # which is its job, not because the webhook timed out.
                with pytest.raises(CommandError, match="1 health probe"):
                    call_command("snapadmin_health_alert", stdout=out)
        assert len(mail.outbox) == 1
        assert "Health alert sent" in out.getvalue()

    @override_settings(SNAPADMIN_HEALTH_ALERT_EMAILS=[], SNAPADMIN_ERROR_ALERT_EMAILS=[],
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_the_health_command_names_the_channels_not_a_recipient_count(self):
        """A chat-only deployment has zero recipients — the line must not read as a failure."""
        out = StringIO()
        with patch.object(health, "run_health_probes", return_value=_DOWN):
            with _captured_posts():
                with pytest.raises(CommandError, match="1 health probe"):
                    call_command("snapadmin_health_alert", stdout=out)
        printed = out.getvalue()
        assert "via slack" in printed
        assert "recipient(s)" not in printed

    def test_probe_lines_explain_why_each_subsystem_is_down(self):
        probes = [
            {"name": "database", "title": "Database", "ok": False, "data": {"error": "refused"}},
            {"name": "api", "title": "REST API", "ok": False, "data": {"detail": "500"}},
            {"name": "graphql", "title": "GraphQL", "ok": False, "data": {}},
        ]
        assert health.probe_lines(probes) == (
            "Database: refused",
            "REST API: 500",
            "GraphQL: reported unhealthy",
        )


# ─────────────────────────────────────────────────────────────────────────────
# The secret must not surface anywhere else
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestWebhookUrlsStaySecret:
    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK, TELEGRAM])
    def test_snapadmin_info_never_reports_a_webhook_url(self):
        out = StringIO()
        call_command("snapadmin_info", "--json", stdout=out)
        report = out.getvalue()
        assert "SuperSecretToken" not in report
        assert TELEGRAM_TOKEN not in report

    @override_settings(SNAPADMIN_ERROR_ALERT_THRESHOLD=1, SNAPADMIN_ERROR_ALERT_EMAILS=RECIPIENTS,
                       SNAPADMIN_ALERT_WEBHOOKS=[SLACK])
    def test_the_alert_email_never_carries_the_webhook_url(self):
        _make_events(1)
        with _captured_posts():
            maybe_send_spike_alert()
        body = mail.outbox[0].body + str(mail.outbox[0].alternatives)
        assert "hooks.slack.com" not in body

    @override_settings(SNAPADMIN_ALERT_WEBHOOKS=[SLACK, TELEGRAM])
    def test_the_posted_payload_never_echoes_the_target_url(self):
        with _captured_posts() as posted:
            dispatch(_alert(), get_webhook_channels(kind=ALERT_KIND_ERROR_SPIKE))
        serialised = json.dumps([post["payload"] for post in posted])
        assert "SuperSecretToken" not in serialised
        assert TELEGRAM_TOKEN not in serialised


# ─────────────────────────────────────────────────────────────────────────────
# Backward compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_monitoring_send_email_still_renders_and_sends(self):
        # Kept as a delegation to alerts.send_email_alert: the rendering moved,
        # the import path other code already used did not.
        monitoring._send_email(
            subject="[SnapAdmin] Health alert — 1 subsystem down: database",
            template="health_alert",
            context={"failing": _DOWN, "probes": _DOWN, "checked": 1,
                     "generated_at": None},
            recipients=RECIPIENTS,
            from_email="snapadmin@example.com",
        )
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == RECIPIENTS
        assert "connection refused" in mail.outbox[0].body
