"""
snapadmin/logging_config.py

Structured, colorized logging configuration for SnapAdmin.

Provides:
  - configure_logging()  : Call once in settings.py to activate structured logs
  - get_logger()         : Get a bound structlog logger for any module
  - SnapAdminLogger      : Pre-configured logger for internal SnapAdmin use

Usage:
    # In settings.py:
    from snapadmin.logging_config import configure_logging
    configure_logging(log_level="INFO", json_logs=False)

    # In any module:
    from snapadmin.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("admin_registered", model="Product", fields=5)
"""

import logging
import sys
from typing import Optional

import structlog


# ─────────────────────────────────────────────────────────────────────────────
# ANSI colour helpers
# ─────────────────────────────────────────────────────────────────────────────

_LEVEL_COLOURS = {
    "debug":    "\033[36m",   # cyan
    "info":     "\033[32m",   # green
    "warning":  "\033[33m",   # yellow
    "error":    "\033[31m",   # red
    "critical": "\033[35m",   # magenta
}
_RESET = "\033[0m"
_DIM   = "\033[2m"
_BOLD  = "\033[1m"


def _colourise_level(level: str) -> str:
    """Return ANSI-wrapped level string or plain uppercase fallback."""
    colour = _LEVEL_COLOURS.get(level.lower(), "")
    return f"{colour}{_BOLD}{level.upper():>8}{_RESET}"


class ColourConsoleRenderer:
    """
    structlog renderer that outputs a clean, coloured single-line log entry.

    Format:
        [TIMESTAMP] [LEVEL] event  key=value key=value
    """

    def __call__(self, logger, method: str, event_dict: dict) -> str:  # noqa: ARG002
        ts        = event_dict.pop("timestamp", "")
        level     = event_dict.pop("level", method)
        event     = event_dict.pop("event", "")
        exc_info  = event_dict.pop("exc_info", None)

        ts_str    = f"{_DIM}{ts}{_RESET} " if ts else ""
        level_str = _colourise_level(level)
        event_str = f"{_BOLD}{event}{_RESET}"

        extras = "  ".join(
            f"{_DIM}{k}{_RESET}={v!r}" for k, v in event_dict.items()
        )

        line = f"{ts_str}{level_str}  {event_str}"
        if extras:
            line += f"  {extras}"

        if exc_info:
            import traceback
            tb = traceback.format_exception(*exc_info) if isinstance(exc_info, tuple) else []
            line += "\n" + "".join(tb).rstrip()

        return line


def configure_logging(
    log_level: str = "INFO",
    json_logs: bool = False,
) -> None:
    """
    Configure structlog + stdlib logging for SnapAdmin.

    Args:
        log_level: Minimum log level string (DEBUG / INFO / WARNING / ERROR).
        json_logs: When True (e.g. in production / Docker), emit JSON lines
                   instead of the human-friendly coloured format.
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = ColourConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *shared_processors,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Silence noisy third-party loggers
    for noisy in ("django.db.backends", "elasticsearch", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger bound to the given name.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A structlog BoundLogger instance.
    """
    return structlog.get_logger(name or "snapadmin")


# Pre-configured logger for SnapAdmin internals
SnapAdminLogger = get_logger("snapadmin.core")
