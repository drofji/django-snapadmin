"""
tests/test_properties_backup_retention.py — the laws of backup retention (#EXT3u).

``SNAPADMIN_BACKUP_KEEP`` was the only retention knob — "keep the newest N" — applied
by five copies of the same ``names[:-keep]`` slice (local and network directories,
FTP, SFTP, S3). Daily/weekly/monthly tiers (``SNAPADMIN_BACKUP_KEEP_DAILY`` /
``_WEEKLY`` / ``_MONTHLY``) now sit beside it, all decided by one pure function,
``snapadmin.backup._select_stale(names, policy)``. A tier counts the most recent
days / ISO weeks / months **that have a backup** and keeps the newest backup of
each — no clock involved, so the answer depends only on the names.

These laws are what makes the function safe to point at real backups. The slice
also deleted *every* backup — the one just written included — when ``KEEP`` was
``0`` or negative; the newest run is now always kept.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from snapadmin.backup import BACKUP_PREFIX, _RetentionPolicy, _select_stale

EPOCH = datetime(2025, 1, 1)

stamps = st.integers(min_value=0, max_value=60 * 24 * 400).map(
    lambda minutes: EPOCH + timedelta(minutes=minutes)
)


def _name(stamp: datetime, prefix: str = BACKUP_PREFIX) -> str:
    return f"{prefix}{stamp:%Y%m%d-%H%M%S}.sql.gz"


names = st.sets(stamps, max_size=40).map(lambda chosen: {_name(stamp) for stamp in chosen})
policies = st.builds(
    _RetentionPolicy,
    keep_last=st.integers(min_value=-2, max_value=6),
    keep_daily=st.integers(min_value=0, max_value=6),
    keep_weekly=st.integers(min_value=0, max_value=4),
    keep_monthly=st.integers(min_value=0, max_value=4),
)


class TestTheLaws:
    @given(names, policies)
    def test_the_newest_backup_is_always_kept(self, backup_names, policy):
        stale = _select_stale(backup_names, policy)

        if backup_names:
            assert max(backup_names) not in stale

    @given(names, policies)
    def test_a_second_run_deletes_nothing_more(self, backup_names, policy):
        stale = set(_select_stale(backup_names, policy))

        assert _select_stale(backup_names - stale, policy) == []

    @given(names, policies)
    def test_it_only_ever_selects_names_it_was_given_oldest_first(self, backup_names, policy):
        stale = _select_stale(backup_names, policy)

        assert set(stale) <= backup_names
        assert stale == sorted(stale)

    @given(names, st.integers(min_value=1, max_value=8))
    def test_keep_n_alone_is_exactly_the_old_slice(self, backup_names, keep):
        """No tier set — every existing deployment — behaves exactly as before."""
        legacy = sorted(backup_names)[:-keep]

        assert _select_stale(backup_names, _RetentionPolicy(keep_last=keep)) == legacy

    @given(names, policies)
    def test_adding_a_tier_never_deletes_more(self, backup_names, policy):
        without_daily = _RetentionPolicy(
            keep_last=policy.keep_last, keep_weekly=policy.keep_weekly,
            keep_monthly=policy.keep_monthly,
        )

        assert set(_select_stale(backup_names, policy)) <= set(
            _select_stale(backup_names, without_daily)
        )

    @given(names, st.integers(min_value=1, max_value=6))
    def test_the_daily_tier_keeps_the_newest_of_each_recent_day(self, backup_names, days):
        kept = backup_names - set(_select_stale(backup_names, _RetentionPolicy(1, keep_daily=days)))
        by_day: dict[str, str] = {}
        for name in sorted(backup_names):
            by_day[name[len(BACKUP_PREFIX):][:8]] = name  # the last one wins: the newest

        expected = {by_day[day] for day in sorted(by_day)[-days:]}

        assert expected <= kept


class TestTheEdges:
    def test_keep_zero_no_longer_deletes_the_backup_just_written(self):
        backups = {_name(EPOCH), _name(EPOCH + timedelta(days=1))}

        assert _select_stale(backups, _RetentionPolicy(keep_last=0)) == [_name(EPOCH)]

    def test_a_name_without_a_timestamp_is_never_kept_by_a_tier(self):
        odd = f"{BACKUP_PREFIX}0-manual-copy.sql.gz"  # sorts before every dated name
        older, newer = _name(EPOCH), _name(EPOCH + timedelta(days=1))

        stale = _select_stale({odd, older, newer}, _RetentionPolicy(keep_last=1, keep_daily=5))

        assert stale == [odd]

    def test_weekly_uses_iso_weeks(self):
        sunday = datetime(2026, 1, 4, 12)  # ISO week 1 of 2026
        monday = datetime(2026, 1, 5, 12)  # ISO week 2
        backups = {_name(sunday), _name(monday)}

        assert _select_stale(backups, _RetentionPolicy(keep_last=1, keep_weekly=2)) == []

    def test_the_same_week_number_of_two_years_is_two_weeks(self):
        last_year = datetime(2025, 3, 5)  # ISO week 10 of 2025
        this_year = datetime(2026, 3, 4)  # ISO week 10 of 2026
        backups = {_name(last_year), _name(this_year)}

        assert _select_stale(backups, _RetentionPolicy(keep_last=1, keep_weekly=2)) == []

    def test_monthly_keeps_one_per_month(self):
        backups = {_name(datetime(2026, 1, day)) for day in (3, 17, 30)} | {_name(datetime(2026, 2, 1))}

        assert _select_stale(backups, _RetentionPolicy(keep_last=1, keep_monthly=2)) == [
            _name(datetime(2026, 1, 3)), _name(datetime(2026, 1, 17)),
        ]

    def test_an_s3_key_prefix_does_not_hide_the_timestamp(self):
        older = f"prod/backups/{_name(EPOCH)}"
        newer = f"prod/backups/{_name(EPOCH + timedelta(days=1))}"

        assert _select_stale({older, newer}, _RetentionPolicy(keep_last=1, keep_daily=2)) == []
