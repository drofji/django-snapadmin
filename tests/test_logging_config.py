"""
tests/test_logging_config.py

``snapadmin.logging_config`` — the structured-logging entry points a project
calls from ``settings.py``.

``ColourConsoleRenderer`` is the one place SnapAdmin decides what a log line
*looks like*, so the tests pin the rendered string in full rather than checking
that the event name appears somewhere in it: the format is the contract a
terminal, a log shipper and a human reader all consume. ``configure_logging``
reconfigures the root logger and structlog process-wide, so the tests here
assert what it actually installed — and put the previous configuration back,
since leaving a test's logging setup in place would follow the whole session
(#QA1b; the file was previously named for the coverage metric rather than for
this behaviour).
"""

import logging

import pytest
import structlog

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


@pytest.fixture
def renderer():
    from snapadmin.logging_config import ColourConsoleRenderer

    return ColourConsoleRenderer()


class TestColourConsoleRenderer:
    def test_renders_timestamp_level_and_event_in_that_order(self, renderer):
        line = renderer(
            None,
            "info",
            {"timestamp": "2026-01-01T00:00:00", "level": "info", "event": "test_event"},
        )
        assert line == (
            f"{DIM}2026-01-01T00:00:00{RESET} "
            f"{GREEN}{BOLD}    INFO{RESET}"
            f"  {BOLD}test_event{RESET}"
        )

    def test_omits_the_timestamp_block_entirely_when_there_is_none(self, renderer):
        line = renderer(None, "warning", {"level": "warning", "event": "no_timestamp"})
        # No leading space either — the whole timestamp segment is dropped.
        assert line == f"{YELLOW}{BOLD} WARNING{RESET}  {BOLD}no_timestamp{RESET}"

    def test_extra_fields_are_appended_as_dimmed_key_repr_pairs(self, renderer):
        line = renderer(
            None,
            "debug",
            {"level": "debug", "event": "with_extras", "user": "alice", "count": 42},
        )
        assert line == (
            f"{CYAN}{BOLD}   DEBUG{RESET}  {BOLD}with_extras{RESET}"
            f"  {DIM}user{RESET}='alice'  {DIM}count{RESET}=42"
        )

    def test_the_level_column_is_right_aligned_to_eight_characters(self, renderer):
        line = renderer(None, "error", {"level": "error", "event": "e"})
        assert line == f"{RED}{BOLD}   ERROR{RESET}  {BOLD}e{RESET}"

    def test_an_unknown_level_loses_the_colour_but_keeps_the_column(self, renderer):
        line = renderer(None, "custom", {"level": "custom", "event": "edge_case"})
        # No colour prefix for a level the map does not know, still bold, still
        # padded to the same width so the column does not jump.
        assert line == f"{BOLD}  CUSTOM{RESET}  {BOLD}edge_case{RESET}"

    def test_the_level_falls_back_to_the_method_name_when_absent(self, renderer):
        line = renderer(None, "warning", {"event": "no_level_key"})
        assert line == f"{YELLOW}{BOLD} WARNING{RESET}  {BOLD}no_level_key{RESET}"

    def test_an_exception_is_appended_as_a_traceback_on_its_own_lines(self, renderer):
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        line = renderer(None, "error", {"level": "error", "event": "with_exc", "exc_info": exc_info})
        head, _, traceback_text = line.partition("\n")
        assert head == f"{RED}{BOLD}   ERROR{RESET}  {BOLD}with_exc{RESET}"
        assert traceback_text.startswith("Traceback (most recent call last):")
        assert traceback_text.endswith("ValueError: test error")

    def test_a_non_tuple_exc_info_renders_a_trailing_blank_line_not_a_crash(self, renderer):
        # structlog can hand the renderer a bare True; formatting it as a
        # traceback is impossible, and the renderer must not raise.
        line = renderer(None, "error", {"level": "error", "event": "boom", "exc_info": True})
        assert line == f"{RED}{BOLD}   ERROR{RESET}  {BOLD}boom{RESET}\n"


@pytest.fixture
def restore_logging():
    """Put the root logger and structlog's global configuration back."""
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    noisy_before = {
        name: logging.getLogger(name).level
        for name in ("django.db.backends", "elasticsearch", "urllib3")
    }
    structlog_before = structlog.get_config()
    try:
        yield
    finally:
        root.handlers = handlers_before
        root.setLevel(level_before)
        for name, level in noisy_before.items():
            logging.getLogger(name).setLevel(level)
        structlog.configure(**structlog_before)


class TestConfigureLogging:
    def test_console_mode_installs_the_colour_renderer_on_one_stdout_handler(
        self, restore_logging
    ):
        import sys

        from snapadmin.logging_config import ColourConsoleRenderer, configure_logging

        configure_logging(log_level="DEBUG", json_logs=False)

        root = logging.getLogger()
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stdout
        assert isinstance(handler.formatter.processors[-1], ColourConsoleRenderer)
        assert root.level == logging.DEBUG

    def test_json_mode_swaps_the_renderer_and_nothing_else(self, restore_logging):
        from snapadmin.logging_config import configure_logging

        configure_logging(log_level="INFO", json_logs=True)

        formatter = logging.getLogger().handlers[0].formatter
        assert isinstance(formatter.processors[-1], structlog.processors.JSONRenderer)
        assert logging.getLogger().level == logging.INFO

    def test_an_unknown_level_name_falls_back_to_info(self, restore_logging):
        from snapadmin.logging_config import configure_logging

        configure_logging(log_level="NOT_A_LEVEL")

        assert logging.getLogger().level == logging.INFO

    def test_the_noisy_third_party_loggers_are_pinned_to_warning(self, restore_logging):
        from snapadmin.logging_config import configure_logging

        for name in ("django.db.backends", "elasticsearch", "urllib3"):
            logging.getLogger(name).setLevel(logging.DEBUG)

        configure_logging(log_level="DEBUG")

        for name in ("django.db.backends", "elasticsearch", "urllib3"):
            assert logging.getLogger(name).level == logging.WARNING

    def test_structlog_is_configured_to_cache_the_bound_logger(self, restore_logging):
        from snapadmin.logging_config import configure_logging

        configure_logging()

        config = structlog.get_config()
        assert config["wrapper_class"] is structlog.stdlib.BoundLogger
        assert config["cache_logger_on_first_use"] is True


class TestGetLogger:
    """What ``get_logger`` hands back, stated instead of "it is not None".

    structlog returns a *lazy proxy*: nothing is bound until the logger is first
    used, which is why the name only becomes observable after ``.bind()``. That
    is the contract callers get, so the tests say it.
    """

    def test_returns_a_lazy_proxy_that_binds_to_the_given_name(self):
        from snapadmin.logging_config import get_logger

        proxy = get_logger("test.module")
        assert isinstance(proxy, structlog._config.BoundLoggerLazyProxy)

        bound = proxy.bind()
        assert isinstance(bound, structlog.stdlib.BoundLogger)
        assert bound._logger.name == "test.module"

    def test_the_returned_logger_emits_the_event_and_its_keywords(self):
        from structlog.testing import capture_logs

        from snapadmin.logging_config import get_logger

        with capture_logs() as emitted:
            get_logger("test.module").info("admin_registered", model="Product", fields=5)

        assert emitted == [
            {
                "event": "admin_registered",
                "model": "Product",
                "fields": 5,
                "log_level": "info",
            }
        ]

    def test_defaults_to_the_package_name(self):
        from snapadmin.logging_config import get_logger

        assert get_logger().bind()._logger.name == "snapadmin"

    def test_the_prebound_internal_logger_names_the_core(self):
        from snapadmin.logging_config import SnapAdminLogger

        assert SnapAdminLogger.bind()._logger.name == "snapadmin.core"
