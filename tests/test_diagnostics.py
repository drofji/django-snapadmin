"""
Tests for the ``snapadmin_info`` diagnostics framework (#CLI1a spine).

Covers the collector registry, the generic text renderer, the version/feature-flags collector,
and the ``snapadmin_info`` management command (text, ``--json``, ``--section``, ``--brief``,
``--verbose`` and ``--health-check`` paths). Later sections (database, elasticsearch, …) add
their own collector modules and test files.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from snapadmin.diagnostics import (
    collect,
    get_collector,
    get_collectors,
    register,
)
from snapadmin.diagnostics import render
from snapadmin.diagnostics.registry import Collector, load_collectors


def _collector(name="section", title="Section", icon="🔧", *, health_probe=False, ok=True, data=None):
    payload = {"ok": ok} if health_probe else (data or {})
    return Collector(
        name=name,
        title=title,
        icon=icon,
        order=1,
        health_probe=health_probe,
        fn=lambda *, verbose: payload,
    )


@pytest.fixture
def temp_collector():
    """Register throwaway collectors, cleaning them out of the global registry afterwards."""
    from snapadmin.diagnostics import registry

    registry.load_collectors()
    created: list[str] = []

    def make(name, *, health_probe=False, ok=True, data=None):
        registry._REGISTRY[name] = _collector(
            name=name, title=name.title(), health_probe=health_probe, ok=ok, data=data
        )
        created.append(name)
        return registry._REGISTRY[name]

    yield make

    for name in created:
        registry._REGISTRY.pop(name, None)


# ── registry ──────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_load_collectors_is_idempotent(self):
        load_collectors()
        load_collectors()  # second call takes the cached early-return branch
        assert any(c.name == "version" for c in get_collectors())

    def test_get_collectors_sorted_by_order_then_name(self, temp_collector):
        temp_collector("zzz_last")  # order defaults to 1 in the helper
        collectors = get_collectors()
        orders = [(c.order, c.name) for c in collectors]
        assert orders == sorted(orders)

    def test_get_collector_found_and_missing(self):
        assert get_collector("version") is not None
        assert get_collector("does-not-exist") is None

    def test_collector_collect_calls_fn(self):
        collector = _collector(data={"k": "v"})
        assert collector.collect(verbose=False) == {"k": "v"}

    def test_register_decorator_returns_function(self, temp_collector):
        from snapadmin.diagnostics import registry

        @register("temp_reg", title="Temp", order=999)
        def _fn(*, verbose):  # pragma: no cover - body never called here
            return {}

        try:
            assert _fn is not None
            assert registry._REGISTRY["temp_reg"].title == "Temp"
        finally:
            registry._REGISTRY.pop("temp_reg", None)


# ── collect() driver ──────────────────────────────────────────────────────────


class TestCollect:
    def test_collect_all_includes_version(self):
        names = [c.name for c, _ in collect()]
        assert "version" in names

    def test_collect_sections_filters(self):
        results = collect(sections=["version"])
        assert [c.name for c, _ in results] == ["version"]

    def test_collect_unknown_section_yields_nothing(self):
        assert collect(sections=["nope"]) == []

    def test_collect_health_only_returns_only_probes(self):
        # ``version`` is informational; ``database``/``elasticsearch`` are health probes.
        names = [c.name for c, _ in collect(health_only=True)]
        assert "version" not in names
        assert "database" in names

    def test_collect_health_only_keeps_probes(self, temp_collector):
        temp_collector("probe_x", health_probe=True, ok=True)
        names = [c.name for c, _ in collect(health_only=True)]
        assert "probe_x" in names
        assert "version" not in names  # non-probe stays out


# ── renderer ──────────────────────────────────────────────────────────────────


class TestRenderer:
    def test_renders_header_and_scalars(self):
        col = _collector(data={"version": "1.0", "flag": True, "off": False})
        text = render.render_report([(col, {"version": "1.0", "flag": True, "off": False})])
        assert "🔧 Section" in text
        assert "Version: 1.0" in text
        assert "Flag: ✓" in text
        assert "Off: ✗" in text

    def test_disabled_section_collapses(self):
        col = _collector()
        text = render.render_report([(col, {"enabled": False})])
        assert text == "🔧 Section: disabled"

    def test_empty_icon_is_stripped(self):
        col = _collector(icon="")
        text = render.render_report([(col, {"a": 1})])
        assert text.splitlines()[0] == "Section"

    def test_brief_hides_nested_and_metadata(self):
        col = _collector()
        data = {"a": 1, "nested": {"x": 1}, "items": [1, 2], "_meta": 9}
        text = render.render_report([(col, data)], brief=True)
        assert "A: 1" in text
        assert "Nested" not in text
        assert "Items" not in text
        assert "Meta" not in text

    def test_full_render_walks_nested_structures(self):
        col = _collector()
        data = {
            "scalar": None,
            "nested": {"k": "v"},
            "items": [{"id": 1}, "raw"],
            "_hidden": "x",
        }
        lines = render.render_report([(col, data)]).splitlines()
        assert "  Scalar: —" in lines
        assert "  Nested:" in lines
        assert "    K: v" in lines
        assert "  Items:" in lines
        assert "    Id: 1" in lines
        assert "    - raw" in lines
        assert all("Hidden" not in line for line in lines)

    def test_render_value_scalar_leaf(self):
        assert render._render_value("x", depth=1) == ["  x"]

    def test_format_scalar_variants(self):
        assert render._format_scalar(True) == "✓"
        assert render._format_scalar(False) == "✗"
        assert render._format_scalar(None) == "—"
        assert render._format_scalar("txt") == "txt"

    def test_humanise(self):
        assert render._humanise("rest_api") == "Rest api"


# ── version collector ─────────────────────────────────────────────────────────


class TestVersionCollector:
    def test_reports_versions_and_features(self):
        data = get_collector("version").collect(verbose=False)
        assert set(data) == {"version", "status", "django", "python", "features"}
        assert "rest_api" in data["features"]
        assert isinstance(data["features"]["rest_api"], bool)

    def test_prerelease_status(self, monkeypatch):
        monkeypatch.setattr("snapadmin.diagnostics.version.__version__", "0.1.0b4")
        data = get_collector("version").collect(verbose=False)
        assert data["status"] == "pre-release"

    def test_stable_status(self, monkeypatch):
        monkeypatch.setattr("snapadmin.diagnostics.version.__version__", "1.0.0")
        data = get_collector("version").collect(verbose=False)
        assert data["status"] == "stable"

    @override_settings(DEBUG=True)
    def test_flag_none_default_follows_debug_true(self):
        # An unset flag whose default is None ("follow DEBUG") resolves to DEBUG.
        from snapadmin.diagnostics.version import _flag

        assert _flag("SNAPADMIN_UNSET_FOR_TEST", None) is True

    @override_settings(DEBUG=False)
    def test_flag_none_default_follows_debug_false(self):
        from snapadmin.diagnostics.version import _flag

        assert _flag("SNAPADMIN_UNSET_FOR_TEST", None) is False

    def test_flag_uses_bool_default_when_unset(self):
        from snapadmin.diagnostics.version import _flag

        assert _flag("SNAPADMIN_UNSET_FOR_TEST", True) is True
        assert _flag("SNAPADMIN_UNSET_FOR_TEST", False) is False

    @override_settings(SNAPADMIN_REST_API_ENABLED=False)
    def test_flag_reads_configured_value(self):
        from snapadmin.diagnostics.version import _flag

        assert _flag("SNAPADMIN_REST_API_ENABLED", True) is False


# ── management command ────────────────────────────────────────────────────────


def _run(**kwargs):
    out = StringIO()
    call_command("snapadmin_info", stdout=out, **kwargs)
    return out.getvalue()


@pytest.mark.django_db
class TestSnapadminInfoCommand:
    def test_default_text_report(self):
        text = _run()
        assert "Version & Status" in text

    def test_json_output(self):
        payload = json.loads(_run(as_json=True))
        assert "version" in payload
        assert payload["version"]["features"]

    def test_section_filter(self):
        text = _run(sections=["version"])
        assert "Version & Status" in text

    def test_unknown_section_errors(self):
        with pytest.raises(CommandError) as exc:
            _run(sections=["bogus"])
        assert "Unknown section" in str(exc.value)

    def test_brief(self):
        text = _run(brief=True)
        assert "Version & Status" in text
        # Feature flags live under a nested dict, hidden in brief mode.
        assert "Rest api" not in text

    def test_verbose(self):
        text = _run(verbose=True)
        assert "Version & Status" in text

    def test_health_check_passes_when_db_reachable(self):
        text = _run(health_check=True)
        assert "Health check passed" in text

    def test_health_check_fails(self, temp_collector):
        temp_collector("db_probe", health_probe=True, ok=False)
        with pytest.raises(CommandError) as exc:
            _run(health_check=True)
        assert "db_probe" in str(exc.value)

    def test_health_check_json_omits_success_line(self, temp_collector):
        temp_collector("ok_probe", health_probe=True, ok=True)
        text = _run(health_check=True, as_json=True)
        assert "Health check passed" not in text
        assert "ok_probe" in json.loads(text)
