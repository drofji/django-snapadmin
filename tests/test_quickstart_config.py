"""Tests for :mod:`snapadmin.quickstart.config` (#CLI3f)."""

from __future__ import annotations

import pytest

from snapadmin.quickstart import QuickstartError, config


class TestConfigRoundTrip:
    def test_save_and_load(self, tmp_path):
        path = config.save_config({"mode": "docker", "debug": True}, tmp_path / "team.ini")
        loaded = config.load_config(path)
        assert loaded["mode"] == "docker"
        assert loaded["debug"] == "True"  # values round-trip as strings

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(QuickstartError, match="not found"):
            config.load_config(tmp_path / "nope.ini")

    def test_load_wrong_section(self, tmp_path):
        bad = tmp_path / "bad.ini"
        bad.write_text("[other]\nx = 1\n")
        with pytest.raises(QuickstartError, match=r"no \[snapadmin-demo\]"):
            config.load_config(bad)
