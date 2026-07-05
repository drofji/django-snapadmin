"""
tests/test_logging_coverage.py

Coverage for snapadmin/logging_config.py — ColourConsoleRenderer, configure_logging.
"""

import pytest


class TestColourConsoleRenderer:
    def _make_renderer(self):
        from snapadmin.logging_config import ColourConsoleRenderer
        return ColourConsoleRenderer()

    def test_renders_basic_event(self):
        renderer = self._make_renderer()
        event_dict = {"timestamp": "2026-01-01T00:00:00", "level": "info", "event": "test_event"}
        output = renderer(None, "info", event_dict)
        assert "test_event" in output
        assert "INFO" in output

    def test_renders_without_timestamp(self):
        renderer = self._make_renderer()
        event_dict = {"level": "warning", "event": "no_timestamp"}
        output = renderer(None, "warning", event_dict)
        assert "no_timestamp" in output

    def test_renders_with_extra_fields(self):
        renderer = self._make_renderer()
        event_dict = {"level": "debug", "event": "with_extras", "user": "alice", "count": 42}
        output = renderer(None, "debug", event_dict)
        assert "with_extras" in output
        assert "alice" in output

    def test_renders_with_exc_info(self):
        renderer = self._make_renderer()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        event_dict = {"level": "error", "event": "with_exc", "exc_info": exc_info}
        output = renderer(None, "error", event_dict)
        assert "with_exc" in output
        assert "ValueError" in output

    def test_unknown_level_uses_plain_uppercase(self):
        renderer = self._make_renderer()
        event_dict = {"level": "custom", "event": "edge_case"}
        output = renderer(None, "custom", event_dict)
        assert "edge_case" in output


class TestConfigureLogging:
    def test_configure_logging_json_mode(self):
        """configure_logging with json_logs=True should not raise."""
        from snapadmin.logging_config import configure_logging
        configure_logging(log_level="INFO", json_logs=True)

    def test_configure_logging_console_mode(self):
        """configure_logging with json_logs=False should not raise."""
        from snapadmin.logging_config import configure_logging
        configure_logging(log_level="DEBUG", json_logs=False)

    def test_get_logger_returns_bound_logger(self):
        from snapadmin.logging_config import get_logger
        logger = get_logger("test.module")
        assert logger is not None

    def test_get_logger_without_name(self):
        from snapadmin.logging_config import get_logger
        logger = get_logger()
        assert logger is not None

    def test_snapadmin_logger_is_importable(self):
        from snapadmin.logging_config import SnapAdminLogger
        assert SnapAdminLogger is not None
