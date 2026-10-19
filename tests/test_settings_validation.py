"""
tests/test_settings_validation.py — a setting value SnapAdmin cannot use fails at
``manage.py check``, never at the first request (#EXT3f).

``SNAPADMIN_THROTTLE_USER = ""`` — what an unset environment variable becomes — did
not switch throttling off, as its docstring implied: DRF's ``parse_rate("")``
raised while the throttle was built, so **every** throttled endpoint answered
``500``. A project that set its rate the DRF way, in
``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``, silently kept SnapAdmin's 600/min. And
the feature audit reported rate limiting **off** for a project that never set
either setting, while both default throttles were on.

The same class of input crashes every numeric setting coerced with ``int()`` at
use time. The class guard is an ``ast`` scan: a ``SNAPADMIN_*`` setting the package
coerces to a number must have an entry in the validated schema.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from django.test import override_settings

from snapadmin.checks import (
    _NUMERIC_SETTINGS,
    check_removed_storage_settings,
    check_setting_values,
    check_throttle_rates,
)
from snapadmin.conf import _resolve_throttle_rate

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "snapadmin"
DRF_DEFAULTS = {"anon": None, "user": None}


def _ids(messages) -> list[str]:
    return sorted(message.id for message in messages)


class TestThrottleRateResolution:
    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("5/min", "5/min"),
            ("  7/hour  ", "7/hour"),
            ("", "600/min"),
            ("   ", "600/min"),
            (None, None),
        ],
    )
    def test_the_setting_decides_and_a_blank_value_falls_back(self, configured, expected):
        with override_settings(SNAPADMIN_THROTTLE_USER=configured):
            assert _resolve_throttle_rate("SNAPADMIN_THROTTLE_USER", {}) == expected

    def test_an_unset_setting_takes_the_drf_scope_rate_then_the_default(self, settings):
        del settings.SNAPADMIN_THROTTLE_USER
        del settings.SNAPADMIN_THROTTLE_ANON

        assert _resolve_throttle_rate("SNAPADMIN_THROTTLE_USER", {"snapadmin_user": "42/min"}) == "42/min"
        assert _resolve_throttle_rate("SNAPADMIN_THROTTLE_USER", {}) == "600/min"
        assert _resolve_throttle_rate("SNAPADMIN_THROTTLE_ANON", {}) == "60/min"

    def test_a_value_that_is_not_text_is_handed_on_as_text(self):
        with override_settings(SNAPADMIN_THROTTLE_USER=600):
            assert _resolve_throttle_rate("SNAPADMIN_THROTTLE_USER", {}) == "600"

    def test_the_snapadmin_setting_wins_over_the_drf_scope_rate(self):
        with override_settings(SNAPADMIN_THROTTLE_USER="9/min"):
            assert _resolve_throttle_rate("SNAPADMIN_THROTTLE_USER", {"snapadmin_user": "42/min"}) == "9/min"


@pytest.mark.django_db
class TestABlankRateNoLongerBreaksTheApi:
    @override_settings(SNAPADMIN_THROTTLE_USER="", SNAPADMIN_THROTTLE_ANON="")
    def test_the_reported_case_an_empty_rate_answers_200_not_500(self, auth_client):
        response = auth_client.get("/api/models/demo/Product/")

        assert response.status_code == 200

    @override_settings(SNAPADMIN_THROTTLE_USER="")
    def test_the_throttle_built_from_a_blank_rate_uses_the_default(self):
        from snapadmin.api.views import SnapUserRateThrottle

        throttle = SnapUserRateThrottle()

        assert (throttle.rate, throttle.num_requests, throttle.duration) == ("600/min", 600, 60)


class TestThrottleChecks:
    @pytest.mark.parametrize("value", ["abc", "10/fortnight", "ten/min", "10", "/min", 600])
    def test_a_malformed_rate_is_an_error(self, value):
        with override_settings(SNAPADMIN_THROTTLE_USER=value):
            messages = check_setting_values(None)

        assert _ids(messages) == ["snapadmin.E029"]
        assert "SNAPADMIN_THROTTLE_USER" in messages[0].msg

    @pytest.mark.parametrize("value", ["10/s", "600/min", "5/hour", "100/day", None])
    def test_a_valid_or_disabled_rate_passes(self, value):
        with override_settings(SNAPADMIN_THROTTLE_USER=value, SNAPADMIN_THROTTLE_ANON=None):
            assert check_setting_values(None) == []

    def test_a_blank_rate_warns_that_it_falls_back(self):
        with override_settings(SNAPADMIN_THROTTLE_USER="  ", SNAPADMIN_THROTTLE_ANON=None):
            messages = check_throttle_rates(None)

        assert _ids(messages) == ["snapadmin.W029"]
        assert "600/min" in messages[0].msg

    def test_a_drf_user_rate_that_does_not_apply_warns(self, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"user": "1000/min"}}
        settings.SNAPADMIN_THROTTLE_USER = "600/min"

        messages = check_throttle_rates(None)

        assert _ids(messages) == ["snapadmin.W028"]
        assert "1000/min" in messages[0].msg and "600/min" in messages[0].msg

    def test_a_drf_rate_equal_to_the_effective_one_is_silent(self, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"user": "600/min"}}
        settings.SNAPADMIN_THROTTLE_USER = "600/min"

        assert check_throttle_rates(None) == []


class TestNumericSettings:
    @pytest.mark.parametrize("value", ["", "  ", "abc", "1.5x"])
    def test_a_value_int_cannot_read_is_an_error(self, value):
        with override_settings(SNAPADMIN_API_PAGE_SIZE=value):
            messages = check_setting_values(None)

        assert _ids(messages) == ["snapadmin.E029"]
        assert "SNAPADMIN_API_PAGE_SIZE" in messages[0].msg and repr(value) in messages[0].msg

    @pytest.mark.parametrize("value", [25, "25", " 25 "])
    def test_a_number_or_a_numeric_string_passes(self, value):
        with override_settings(SNAPADMIN_API_PAGE_SIZE=value):
            assert check_setting_values(None) == []

    def test_every_setting_the_package_coerces_to_a_number_is_in_the_schema(self):
        coerced: dict[str, str] = {}
        for path in PACKAGE.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"int", "float"}
                    and node.args
                ):
                    continue
                inner = node.args[0]
                while isinstance(inner, ast.BoolOp):  # int(get_setting(...) or 0)
                    inner = inner.values[0]
                if (
                    isinstance(inner, ast.Call)
                    and getattr(inner.func, "id", getattr(inner.func, "attr", None)) == "get_setting"
                    and inner.args
                    and isinstance(inner.args[0], ast.Constant)
                ):
                    coerced[inner.args[0].value] = str(path.relative_to(PACKAGE.parent))

        assert len(coerced) >= 20, "the scan found too few call sites — it is not looking"
        missing = {name: where for name, where in coerced.items() if name not in _NUMERIC_SETTINGS}
        assert not missing, (
            f"review: {missing} coerce a setting with int()/float() but have no entry in "
            "snapadmin.checks._NUMERIC_SETTINGS — a blank environment value would crash at use "
            "time instead of failing manage.py check"
        )


class TestRemovedStorageSettings:
    @pytest.mark.parametrize("name", ["DEFAULT_FILE_STORAGE", "STATICFILES_STORAGE"])
    def test_a_storage_setting_django_ignores_warns(self, settings, name):
        setattr(settings, name, "storages.backends.s3.S3Storage")

        messages = check_removed_storage_settings(None)

        assert _ids(messages) == ["snapadmin.W030"]
        assert name in messages[0].msg

    def test_no_warning_without_them(self):
        assert check_removed_storage_settings(None) == []


class TestTheFeatureAuditReportsTheEffectiveRate:
    @staticmethod
    def _features() -> dict:
        from snapadmin.diagnostics import get_collector

        return get_collector("features").collect(verbose=False)

    def test_unset_settings_mean_the_default_throttles_are_on(self, settings):
        del settings.SNAPADMIN_THROTTLE_USER
        del settings.SNAPADMIN_THROTTLE_ANON

        assert self._features()["rate_limiting"] is True

    @override_settings(SNAPADMIN_THROTTLE_ANON=None, SNAPADMIN_THROTTLE_USER=None)
    def test_both_disabled_explicitly_means_off(self):
        assert self._features()["rate_limiting"] is False
