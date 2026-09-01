"""
Tests for ``snapadmin.checks.check_fetch_by_max_values`` (#FETCH2a, W013).

Kept in its own file rather than ``tests/test_checks.py`` — this check is
lane 7's alone this round, and the shared checks-suite file has plenty of
other lanes' in-flight additions.
"""

from django.test import override_settings

from snapadmin import checks


class TestFetchByMaxValuesCheck:
    def test_default_is_fine(self):
        assert checks.check_fetch_by_max_values(None) == []

    @override_settings(SNAPADMIN_FETCH_BY_MAX_VALUES=50_000)
    def test_below_the_ceiling_is_fine(self):
        assert checks.check_fetch_by_max_values(None) == []

    @override_settings(SNAPADMIN_FETCH_BY_MAX_VALUES=checks.FETCH_BY_MAX_VALUES_SANE_CEILING)
    def test_exactly_at_the_ceiling_is_fine(self):
        assert checks.check_fetch_by_max_values(None) == []

    @override_settings(SNAPADMIN_FETCH_BY_MAX_VALUES=checks.FETCH_BY_MAX_VALUES_SANE_CEILING + 1)
    def test_past_the_ceiling_warns(self):
        result = checks.check_fetch_by_max_values(None)
        assert len(result) == 1
        assert result[0].id == "snapadmin.W013"
        assert str(checks.FETCH_BY_MAX_VALUES_SANE_CEILING + 1) in result[0].msg

    @override_settings(SNAPADMIN_FETCH_BY_MAX_VALUES="not-a-number")
    def test_unparseable_value_does_not_crash(self):
        assert checks.check_fetch_by_max_values(None) == []
