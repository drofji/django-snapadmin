"""
snapadmin/alerts.py

Alert delivery channels for SnapAdmin — email plus chat webhooks.

``snapadmin.monitoring`` (error spikes, error digests) and ``snapadmin.health``
(subsystem health) decide **when** an alert is due; this module decides **where**
it goes. Both entry points keep their existing thresholds, grouping and
cache-based cooldowns — a channel is a transport, never a second copy of the
anti-spam logic, so adding a webhook cannot make an alert fire more often than
the email always did.

Channels:

  * ``EmailChannel`` — the original transport, unchanged: the same
    ``snapadmin/email/*.txt|.html`` templates through Django's email machinery.
  * ``SlackChannel`` / ``DiscordChannel`` / ``TeamsChannel`` — incoming webhooks.
  * ``TelegramChannel`` — the Bot API ``sendMessage`` endpoint.
  * ``JsonWebhookChannel`` — a plain JSON POST for anything else (PagerDuty
    Events v2 via a relay, an internal endpoint, a log collector).

Webhooks are posted with the standard library (``urllib.request``) — SnapAdmin
adds **no dependency** for alerting.

Two guarantees the callers rely on:

  * **Fail-soft.** ``dispatch()`` never raises: one channel timing out cannot
    break the request that recorded the error, the digest task, or the
    ``snapadmin_health_alert`` command, and it cannot stop the other channels.
  * **A failed delivery does not consume the cooldown.** The caller arms the
    cooldown *before* sending (``arm_cooldown``) so concurrent workers can't
    double-send; if no channel accepted the alert, ``release_cooldown()`` drops
    the key it armed, so the next occurrence alerts instead of being silently
    swallowed for the rest of the window.

**Webhook URLs are secrets** — a Slack URL's path and a Telegram bot token are
the credential. They are never logged, never rendered and never reported by
``snapadmin_info``; anything user-visible goes through ``mask_webhook_url()``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: Alert kinds a channel can subscribe to via a webhook entry's ``events`` list.
ALERT_KIND_ERROR_SPIKE = "error_spike"
ALERT_KIND_ERROR_DIGEST = "error_digest"
ALERT_KIND_HEALTH = "health"
ALERT_KINDS = (ALERT_KIND_ERROR_SPIKE, ALERT_KIND_ERROR_DIGEST, ALERT_KIND_HEALTH)

#: Seconds a webhook POST may take before it is abandoned. Deliberately short:
#: the error-spike alert can run inside a request/response cycle.
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 5.0

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

USER_AGENT = "django-snapadmin"


# ─────────────────────────────────────────────────────────────────────────────
# The alert payload
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Alert:
    """One alert, rendered per channel.

    ``subject``/``summary``/``lines`` are the transport-neutral form every
    channel can render; ``template`` and ``context`` carry the richer email
    body. A channel uses whichever it can.
    """

    kind: str
    subject: str
    summary: str = ""
    lines: tuple[str, ...] = ()
    severity: str = "error"
    template: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)

    def as_text(self, *, bullet: str = "• ") -> str:
        """Plain-text rendering: subject, summary, then one line per detail."""
        parts = [self.subject]
        if self.summary:
            parts.append(self.summary)
        parts.extend(f"{bullet}{line}" for line in self.lines)
        return "\n".join(parts)


@dataclass(frozen=True)
class DeliveryResult:
    """What ``dispatch()`` managed to deliver, per channel name."""

    delivered: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()

    @property
    def any_delivered(self) -> bool:
        """True when at least one channel accepted the alert."""
        return bool(self.delivered)

    @property
    def attempted(self) -> int:
        """How many channels were tried."""
        return len(self.delivered) + len(self.failed)


# ─────────────────────────────────────────────────────────────────────────────
# Secret handling
# ─────────────────────────────────────────────────────────────────────────────

def mask_webhook_url(url: str) -> str:
    """Reduce a webhook URL to ``scheme://host/…`` — the secret is the path.

    ``diagnostics.runtime._mask_url`` redacts only the *password* of a broker
    URL, which is the wrong shape here: for
    ``https://hooks.slack.com/services/T0/B0/XXXX`` and for a Telegram
    ``/bot<token>/sendMessage`` path the credential **is** the path, so the
    whole path is dropped rather than redacted in place.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - urlsplit only raises on IPv6 junk
        return "…"
    if not parts.scheme or not parts.hostname:
        return "…"
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    suffix = "/…" if parts.path.strip("/") or parts.query else ""
    return f"{parts.scheme}://{host}{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# Channels
# ─────────────────────────────────────────────────────────────────────────────

class AlertChannel:
    """Base class for a delivery transport.

    Subclasses implement :meth:`deliver` and may raise — ``dispatch()`` is what
    turns a failure into a logged, swallowed non-event.
    """

    #: Stable channel name used in logs and in the callers' result dicts.
    name: ClassVar[str] = "channel"

    @property
    def target(self) -> str:
        """Human-readable destination, safe to log (never a secret)."""
        return self.name

    def deliver(self, alert: Alert) -> None:
        """Send ``alert``. Raises on failure."""
        raise NotImplementedError  # pragma: no cover - abstract


class EmailChannel(AlertChannel):
    """Django email delivery — the original SnapAdmin alert transport."""

    name: ClassVar[str] = "email"

    def __init__(self, *, recipients: Sequence[str], from_email: str | None = None) -> None:
        self.recipients = list(recipients)
        self.from_email = from_email

    @property
    def target(self) -> str:
        return f"{len(self.recipients)} recipient(s)"

    def deliver(self, alert: Alert) -> None:
        send_email_alert(
            subject=alert.subject,
            template=alert.template,
            context=dict(alert.context),
            recipients=self.recipients,
            from_email=self.from_email,
        )


class WebhookChannel(AlertChannel):
    """Base class for the JSON-over-HTTPS channels."""

    name: ClassVar[str] = "webhook"

    #: Provider limit for the message body; ``None`` means "no practical cap".
    max_body_length: ClassVar[int | None] = None

    def __init__(self, *, url: str, timeout: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS) -> None:
        self.url = url
        self.timeout = timeout

    @property
    def target(self) -> str:
        return mask_webhook_url(self.url)

    def body(self, alert: Alert) -> str:
        """The message text for this provider, already truncated to its limit."""
        return _truncate(alert.as_text(), self.max_body_length)

    def payload(self, alert: Alert) -> dict[str, Any]:
        """The JSON body posted to the webhook."""
        raise NotImplementedError  # pragma: no cover - abstract

    def deliver(self, alert: Alert) -> None:
        post_json(self.url, self.payload(alert), timeout=self.timeout)


class SlackChannel(WebhookChannel):
    """Slack incoming webhook (``https://hooks.slack.com/services/…``)."""

    name: ClassVar[str] = "slack"
    max_body_length: ClassVar[int | None] = 3000

    def body(self, alert: Alert) -> str:
        # Slack renders "mrkdwn": single asterisks are bold.
        text = "\n".join(
            [f"*{alert.subject}*", *([alert.summary] if alert.summary else []),
             *(f"• {line}" for line in alert.lines)]
        )
        return _truncate(text, self.max_body_length)

    def payload(self, alert: Alert) -> dict[str, Any]:
        return {"text": self.body(alert)}


class DiscordChannel(WebhookChannel):
    """Discord incoming webhook (``https://discord.com/api/webhooks/…``)."""

    name: ClassVar[str] = "discord"
    #: Discord rejects a message whose ``content`` exceeds 2000 characters.
    max_body_length: ClassVar[int | None] = 2000

    def body(self, alert: Alert) -> str:
        text = "\n".join(
            [f"**{alert.subject}**", *([alert.summary] if alert.summary else []),
             *(f"• {line}" for line in alert.lines)]
        )
        return _truncate(text, self.max_body_length)

    def payload(self, alert: Alert) -> dict[str, Any]:
        return {"content": self.body(alert)}


class TeamsChannel(WebhookChannel):
    """Microsoft Teams incoming webhook, posted as a legacy ``MessageCard``.

    MessageCard is what both the classic Office 365 connector and the newer
    Workflows endpoint accept, which is why it is used in preference to an
    Adaptive Card wrapper that only one of the two understands.
    """

    name: ClassVar[str] = "teams"
    max_body_length: ClassVar[int | None] = 8000

    #: Card accent per severity — Teams wants a hex colour without the "#".
    THEME_COLORS: ClassVar[dict[str, str]] = {
        "error": "d93025",
        "warning": "f9ab00",
        "info": "1a73e8",
    }

    def body(self, alert: Alert) -> str:
        # MessageCard renders markdown and collapses single newlines, so the
        # blocks are separated by blank lines to survive the round trip.
        blocks = [alert.summary] if alert.summary else []
        blocks.extend(f"- {line}" for line in alert.lines)
        return _truncate("\n\n".join(blocks), self.max_body_length)

    def payload(self, alert: Alert) -> dict[str, Any]:
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": alert.subject,
            "themeColor": self.THEME_COLORS.get(alert.severity, self.THEME_COLORS["error"]),
            "title": alert.subject,
            "text": self.body(alert),
        }


class TelegramChannel(WebhookChannel):
    """Telegram Bot API ``sendMessage``.

    Configured with ``token`` + ``chat_id`` rather than a URL — the bot token
    is a credential, so it is only ever assembled into the API URL here and is
    masked out of ``target``.
    """

    name: ClassVar[str] = "telegram"
    #: Telegram rejects a message longer than 4096 characters.
    max_body_length: ClassVar[int | None] = 4096

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        timeout: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
        url: str | None = None,
    ) -> None:
        super().__init__(url=url or TELEGRAM_API_URL.format(token=token), timeout=timeout)
        self.chat_id = chat_id

    def payload(self, alert: Alert) -> dict[str, Any]:
        # No parse_mode: the text is sent verbatim, so an exception message
        # containing Markdown punctuation can't make Telegram reject the post.
        return {
            "chat_id": self.chat_id,
            "text": self.body(alert),
            "disable_web_page_preview": True,
        }


class JsonWebhookChannel(WebhookChannel):
    """Generic JSON POST — the escape hatch for any other endpoint."""

    name: ClassVar[str] = "json"

    def payload(self, alert: Alert) -> dict[str, Any]:
        return {
            "source": "django-snapadmin",
            "kind": alert.kind,
            "severity": alert.severity,
            "subject": alert.subject,
            "summary": alert.summary,
            "lines": list(alert.lines),
            "sent_at": timezone.now().isoformat(),
        }


#: ``type`` value → channel class, for ``SNAPADMIN_ALERT_WEBHOOKS`` entries.
WEBHOOK_CHANNEL_TYPES: dict[str, type[WebhookChannel]] = {
    "slack": SlackChannel,
    "discord": DiscordChannel,
    "teams": TeamsChannel,
    "telegram": TelegramChannel,
    "json": JsonWebhookChannel,
    "webhook": JsonWebhookChannel,
}


def _truncate(text: str, limit: int | None) -> str:
    """Cut ``text`` to ``limit`` characters, marking that it was cut."""
    if limit is None or len(text) <= limit:
        return text
    marker = "\n… (truncated)"
    return text[: max(limit - len(marker), 0)] + marker


# ─────────────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────────────

def post_json(url: str, payload: Mapping[str, Any], *, timeout: float) -> int:
    """POST ``payload`` as JSON and return the HTTP status code.

    Raises ``urllib.error.URLError``/``HTTPError`` on a transport failure and
    ``AlertDeliveryError`` on a non-2xx response — ``dispatch()`` catches both.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib_request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        status = int(getattr(response, "status", 0) or 0)
    if not 200 <= status < 300:
        raise AlertDeliveryError(f"HTTP {status}")
    return status


class AlertDeliveryError(Exception):
    """A channel refused the alert (non-2xx response, malformed config …)."""


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

def _webhook_entries() -> list[Mapping[str, Any]]:
    """The raw ``SNAPADMIN_ALERT_WEBHOOKS`` entries, tolerating a bad setting."""
    configured = getattr(settings, "SNAPADMIN_ALERT_WEBHOOKS", None) or []
    if isinstance(configured, (str, bytes, Mapping)) or not isinstance(configured, Iterable):
        logger.warning("alert_webhooks_setting_invalid", type=type(configured).__name__)
        return []
    return list(configured)


def _default_timeout() -> float:
    """``SNAPADMIN_ALERT_WEBHOOK_TIMEOUT``, falling back to the 5s default."""
    try:
        return float(getattr(settings, "SNAPADMIN_ALERT_WEBHOOK_TIMEOUT",
                             DEFAULT_WEBHOOK_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        logger.warning("alert_webhook_timeout_invalid")
        return DEFAULT_WEBHOOK_TIMEOUT_SECONDS


def _wants_kind(entry: Mapping[str, Any], kind: str, *, index: int) -> bool:
    """Whether a webhook entry subscribes to this alert kind.

    No ``events`` key means "every kind". An ``events`` list naming something
    that is not an alert kind is a typo that would silently mute the channel,
    so it is logged.
    """
    events = entry.get("events")
    if events is None:
        return True
    wanted = {str(event) for event in events}
    unknown = sorted(wanted - set(ALERT_KINDS))
    if unknown:
        logger.warning("alert_webhook_unknown_events", index=index, events=",".join(unknown))
    return kind in wanted


def build_webhook_channel(entry: Mapping[str, Any], *, index: int) -> WebhookChannel | None:
    """Build one channel from a settings entry, or ``None`` if it is unusable.

    A malformed entry is logged (without its URL) and skipped — a typo in the
    settings must not stop the other channels, and must never crash the request
    that triggered the alert.
    """
    if not isinstance(entry, Mapping):
        logger.warning("alert_webhook_entry_invalid", index=index, type=type(entry).__name__)
        return None
    channel_type = str(entry.get("type", "")).strip().lower()
    channel_class = WEBHOOK_CHANNEL_TYPES.get(channel_type)
    if channel_class is None:
        logger.warning("alert_webhook_unknown_type", index=index, type=channel_type or "missing")
        return None

    timeout = entry.get("timeout")
    timeout = float(timeout) if isinstance(timeout, (int, float)) else _default_timeout()

    if channel_class is TelegramChannel:
        token = str(entry.get("token", "") or "")
        chat_id = str(entry.get("chat_id", "") or "")
        if not token or not chat_id:
            logger.warning("alert_webhook_telegram_incomplete", index=index)
            return None
        return TelegramChannel(token=token, chat_id=chat_id, timeout=timeout)

    url = str(entry.get("url", "") or "")
    if not url.lower().startswith(("http://", "https://")):
        logger.warning("alert_webhook_url_invalid", index=index, type=channel_type)
        return None
    return channel_class(url=url, timeout=timeout)


def get_webhook_channels(*, kind: str) -> list[WebhookChannel]:
    """Every configured webhook channel subscribed to ``kind``."""
    channels: list[WebhookChannel] = []
    for index, entry in enumerate(_webhook_entries()):
        if isinstance(entry, Mapping) and not _wants_kind(entry, kind, index=index):
            continue
        channel = build_webhook_channel(entry, index=index)
        if channel is not None:
            channels.append(channel)
    return channels


def build_channels(
    *,
    kind: str,
    recipients: Sequence[str],
    from_email: str | None = None,
) -> list[AlertChannel]:
    """Every channel this alert kind should go to, email first.

    Email stays exactly as configured — its recipients come from the caller's
    own ``SNAPADMIN_*_EMAILS`` setting — and is simply one channel among
    several. ``SNAPADMIN_ALERT_EMAIL_ENABLED = False`` turns it off for
    installs that want chat delivery only.
    """
    channels: list[AlertChannel] = []
    email_enabled = bool(getattr(settings, "SNAPADMIN_ALERT_EMAIL_ENABLED", True))
    if email_enabled and recipients:
        channels.append(EmailChannel(recipients=recipients, from_email=from_email))
    channels.extend(get_webhook_channels(kind=kind))
    return channels


# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────

def send_email_alert(
    *,
    subject: str,
    template: str,
    context: Mapping[str, Any],
    recipients: Sequence[str],
    from_email: str | None,
) -> None:
    """Render the ``snapadmin/email/<template>.txt|.html`` pair and send it."""
    context = dict(context)
    text_body = render_to_string(f"snapadmin/email/{template}.txt", context)
    html_body = render_to_string(f"snapadmin/email/{template}.html", context)
    email = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=from_email, to=list(recipients)
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)


def dispatch(alert: Alert, channels: Sequence[AlertChannel]) -> DeliveryResult:
    """Send ``alert`` to every channel; never raise.

    A channel that fails is logged (with a masked target) and the next one is
    still tried, so a dead webhook cannot cost the operator the email that
    would have told them the site is down.
    """
    delivered: list[str] = []
    failed: list[str] = []
    for channel in channels:
        try:
            channel.deliver(alert)
        except Exception as exc:
            failed.append(channel.name)
            logger.warning(
                "alert_channel_failed",
                channel=channel.name,
                kind=alert.kind,
                target=channel.target,
                error=_error_detail(exc),
            )
        else:
            delivered.append(channel.name)
    if delivered:
        logger.info(
            "alert_dispatched",
            kind=alert.kind,
            delivered=",".join(delivered),
            failed=",".join(failed),
        )
    return DeliveryResult(delivered=tuple(delivered), failed=tuple(failed))


def _error_detail(exc: Exception) -> str:
    """A log-safe description of a delivery failure.

    ``HTTPError.__str__`` includes the request URL, which for a webhook is the
    credential — so HTTP failures are reduced to their status code.
    """
    if isinstance(exc, urllib_error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib_error.URLError):
        return f"{type(exc).__name__}: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Cooldown (shared by every alert entry point, never per channel)
# ─────────────────────────────────────────────────────────────────────────────

def arm_cooldown(cache_key: str, *, minutes: int) -> str | None:
    """Claim the cooldown window, returning a token, or ``None`` if it is held.

    ``cache.add`` is atomic, so exactly one worker wins the window even when
    several cross the threshold at the same moment. The token identifies *our*
    claim so :func:`release_cooldown` can only drop the key it armed.
    """
    token = f"{timezone.now().isoformat()}#{uuid4().hex[:8]}"
    if cache.add(cache_key, token, timeout=max(int(minutes), 0) * 60):
        return token
    return None


def release_cooldown(cache_key: str, token: str | None) -> None:
    """Drop the cooldown we armed when nothing was actually delivered.

    Without this, one failed POST would mute the alert for the whole cooldown
    window — the silence a monitoring system must never produce. The token
    check means a window armed by a *successful* concurrent send survives.
    """
    if token is None:
        return
    if cache.get(cache_key) == token:
        cache.delete(cache_key)
