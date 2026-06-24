"""
tests/test_docker_retention.py

Tests for the Docker image retention pruner (roadmap task #P). These cover the
pure policy logic over a list of tags — no Docker daemon required.
"""

import pytest

from scripts.docker_retention import (
    DEFAULT_KEEP_DAYS,
    parse_day_tags,
    resolve_keep_days,
    select_tags_to_keep,
    select_tags_to_prune,
)

IMG = "snapadmin-test"


def _day(date: str) -> str:
    return f"{IMG}:{date}"


class TestParseDayTags:
    def test_ignores_latest_and_non_date_tags(self):
        tags = [_day("2026-06-24"), f"{IMG}:latest", f"{IMG}:dev", "<none>:<none>"]
        pairs = parse_day_tags(tags)
        assert pairs == [("2026-06-24", _day("2026-06-24"))]

    def test_filters_by_image_repository(self):
        tags = [_day("2026-06-24"), "other-image:2026-06-24"]
        pairs = parse_day_tags(tags, image=IMG)
        assert pairs == [("2026-06-24", _day("2026-06-24"))]

    def test_strips_whitespace(self):
        pairs = parse_day_tags([f"  {_day('2026-06-24')}  "])
        assert pairs == [("2026-06-24", _day("2026-06-24"))]


class TestSelectTagsToPrune:
    def test_under_window_prunes_nothing(self):
        tags = [_day("2026-06-24"), _day("2026-06-23"), _day("2026-06-22")]
        assert select_tags_to_prune(tags, keep_days=3) == []

    def test_drops_oldest_when_fourth_day_appears(self):
        tags = [
            _day("2026-06-24"),
            _day("2026-06-23"),
            _day("2026-06-22"),
            _day("2026-06-21"),
        ]
        assert select_tags_to_prune(tags, keep_days=3) == [_day("2026-06-21")]

    def test_keeps_three_most_recent_build_days_not_calendar_days(self):
        # Gaps in history don't consume slots — last 3 *build-days* are kept.
        tags = [
            _day("2026-06-24"),
            _day("2026-06-10"),
            _day("2026-05-01"),
            _day("2026-01-15"),
        ]
        pruned = select_tags_to_prune(tags, keep_days=3)
        assert pruned == [_day("2026-01-15")]

    def test_never_prunes_latest_or_non_date_tags(self):
        tags = [
            _day("2026-06-24"),
            _day("2026-06-23"),
            _day("2026-06-22"),
            _day("2026-06-21"),
            f"{IMG}:latest",
        ]
        pruned = select_tags_to_prune(tags, keep_days=3)
        assert f"{IMG}:latest" not in pruned
        assert pruned == [_day("2026-06-21")]

    def test_unordered_input_still_prunes_oldest(self):
        tags = [
            _day("2026-06-22"),
            _day("2026-06-24"),
            _day("2026-06-21"),
            _day("2026-06-23"),
        ]
        assert select_tags_to_prune(tags, keep_days=3) == [_day("2026-06-21")]

    def test_configurable_window(self):
        tags = [_day(f"2026-06-{d:02d}") for d in (24, 23, 22, 21, 20)]
        # keep 2 → drop the 3 oldest
        pruned = select_tags_to_prune(tags, keep_days=2)
        assert set(pruned) == {_day("2026-06-22"), _day("2026-06-21"), _day("2026-06-20")}

    def test_worked_example_from_roadmap(self):
        # Builds: a month ago, a week ago, yesterday, today (same-day rebuilds
        # already collapsed to one tag per day by the build script). After today's
        # build only THREE images remain: a-week-ago, yesterday, today.
        tags = [
            _day("2026-06-24"),  # today
            _day("2026-06-23"),  # yesterday
            _day("2026-06-17"),  # a week ago
            _day("2026-05-24"),  # a month ago
        ]
        kept = select_tags_to_keep(tags, keep_days=3)
        pruned = select_tags_to_prune(tags, keep_days=3)
        assert set(kept) == {_day("2026-06-24"), _day("2026-06-23"), _day("2026-06-17")}
        assert pruned == [_day("2026-05-24")]


class TestSelectTagsToKeep:
    def test_keep_is_complement_of_prune(self):
        tags = [_day(f"2026-06-{d:02d}") for d in (24, 23, 22, 21)]
        kept = set(select_tags_to_keep(tags, keep_days=3))
        pruned = set(select_tags_to_prune(tags, keep_days=3))
        assert kept | pruned == set(tags)
        assert kept & pruned == set()


class TestResolveKeepDays:
    def test_explicit_wins(self):
        assert resolve_keep_days(5) == 5

    def test_env_var_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("SNAPADMIN_IMAGE_KEEP_DAYS", "7")
        assert resolve_keep_days() == 7

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("SNAPADMIN_IMAGE_KEEP_DAYS", raising=False)
        assert resolve_keep_days() == DEFAULT_KEEP_DAYS

    def test_empty_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SNAPADMIN_IMAGE_KEEP_DAYS", "")
        assert resolve_keep_days() == DEFAULT_KEEP_DAYS

    def test_zero_or_negative_rejected(self):
        with pytest.raises(ValueError):
            resolve_keep_days(0)
        with pytest.raises(ValueError):
            resolve_keep_days(-2)
