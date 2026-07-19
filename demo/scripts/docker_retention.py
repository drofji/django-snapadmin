"""
demo/scripts/docker_retention.py

Docker image retention for the test/demo image (roadmap task #P).

Policy — "one build per day, keep the last N build-days" (N defaults to 3):

  • Collapse within a day  — images are tagged ``<name>:YYYY-MM-DD`` plus a moving
    ``<name>:latest``. Rebuilding on the same calendar day re-points that day's tag
    at the new image, so only the latest same-day build keeps the tag (the previous
    one becomes a dangling layer, reclaimed by ``docker image prune``).
  • Rolling N-day window  — keep the day-tag of each of the N most-recent build-days;
    once an (N+1)-th distinct build-day appears, the oldest day's tag is removed.
  • History gaps are irrelevant — "N days" means the last N *build-days*, not the last
    N calendar days. Idle days never consume a slot.

The retention math (:func:`select_tags_to_prune`) is a pure function over a list of
tags so it can be unit-tested without a Docker daemon. The daemon-facing helpers at
the bottom are thin wrappers around the ``docker`` CLI.

Usage:
    python -m demo.scripts.docker_retention prune --image snapadmin-test
    python -m demo.scripts.docker_retention prune --image snapadmin-test --keep-days 5
    SNAPADMIN_IMAGE_KEEP_DAYS=5 python -m demo.scripts.docker_retention prune --image snapadmin-test
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

DEFAULT_KEEP_DAYS = 3
KEEP_DAYS_ENV = "SNAPADMIN_IMAGE_KEEP_DAYS"

# Matches "repository:YYYY-MM-DD" (the per-build-day tag). The date is validated
# lexically — ISO dates sort chronologically as plain strings.
_DAY_TAG_RE = re.compile(r"^(?P<repo>.+):(?P<date>\d{4}-\d{2}-\d{2})$")


def resolve_keep_days(explicit: int | None = None) -> int:
    """Resolve the retention window: explicit arg > env var > default (3)."""
    if explicit is not None:
        keep = explicit
    else:
        raw = os.environ.get(KEEP_DAYS_ENV)
        keep = int(raw) if raw not in (None, "") else DEFAULT_KEEP_DAYS
    if keep < 1:
        raise ValueError(f"keep_days must be >= 1, got {keep}")
    return keep


def parse_day_tags(tags: list[str], image: str | None = None) -> list[tuple[str, str]]:
    """
    Filter ``tags`` to valid day-tags and return ``(date, tag)`` pairs.

    Tags that don't match ``<repo>:YYYY-MM-DD`` (e.g. ``:latest``) are ignored. When
    ``image`` is given, only that repository's tags are considered.
    """
    pairs: list[tuple[str, str]] = []
    for tag in tags:
        m = _DAY_TAG_RE.match(tag.strip())
        if not m:
            continue
        if image is not None and m.group("repo") != image:
            continue
        pairs.append((m.group("date"), tag.strip()))
    return pairs


def select_tags_to_prune(
    tags: list[str],
    keep_days: int = DEFAULT_KEEP_DAYS,
    image: str | None = None,
) -> list[str]:
    """
    Return the day-tags that should be removed, keeping the ``keep_days`` most-recent
    distinct build-days. Non-day-tags (``:latest`` etc.) are never returned.

    Deterministic and side-effect-free — this is the unit-tested core of the policy.
    """
    pairs = parse_day_tags(tags, image=image)
    # Distinct build-days, newest first. Dates are ISO so string sort == chrono sort.
    distinct_days = sorted({date for date, _ in pairs}, reverse=True)
    keep_dates = set(distinct_days[:keep_days])
    # Drop every tag whose day is outside the keep window. De-dupe, stable order.
    pruned: list[str] = []
    seen: set[str] = set()
    for date, tag in pairs:
        if date not in keep_dates and tag not in seen:
            pruned.append(tag)
            seen.add(tag)
    return pruned


def select_tags_to_keep(
    tags: list[str],
    keep_days: int = DEFAULT_KEEP_DAYS,
    image: str | None = None,
) -> list[str]:
    """Inverse of :func:`select_tags_to_prune` — the day-tags kept by the policy."""
    pairs = parse_day_tags(tags, image=image)
    distinct_days = sorted({date for date, _ in pairs}, reverse=True)
    keep_dates = set(distinct_days[:keep_days])
    kept: list[str] = []
    seen: set[str] = set()
    for date, tag in pairs:
        if date in keep_dates and tag not in seen:
            kept.append(tag)
            seen.add(tag)
    return kept


# ── Daemon-facing helpers (not unit-tested; thin docker CLI wrappers) ──────────


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def list_image_tags(image: str) -> list[str]:  # pragma: no cover - needs docker
    """Return all ``repo:tag`` strings for ``image`` known to the local daemon."""
    out = _run(["docker", "images", image, "--format", "{{.Repository}}:{{.Tag}}"])
    return [line for line in out.splitlines() if line and "<none>" not in line]


def prune(image: str, keep_days: int, dry_run: bool = False) -> list[str]:  # pragma: no cover - needs docker
    """Remove out-of-window day-tags for ``image`` and reclaim dangling layers."""
    tags = list_image_tags(image)
    to_prune = select_tags_to_prune(tags, keep_days=keep_days, image=image)
    for tag in to_prune:
        if dry_run:
            print(f"[dry-run] would remove {tag}")
        else:
            print(f"Removing {tag}")
            _run(["docker", "image", "rm", tag])
    if not dry_run and to_prune:
        # Reclaim layers left dangling by removed/superseded tags.
        _run(["docker", "image", "prune", "-f"])
    return to_prune


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    parser = argparse.ArgumentParser(description="Docker image retention pruner.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prune", help="Prune out-of-window day-tagged images.")
    p.add_argument("--image", required=True, help="Image repository, e.g. snapadmin-test.")
    p.add_argument("--keep-days", type=int, default=None, help="Build-days to keep (default 3 / env).")
    p.add_argument("--dry-run", action="store_true", help="Print what would be removed.")
    args = parser.parse_args(argv)

    keep_days = resolve_keep_days(args.keep_days)
    removed = prune(args.image, keep_days=keep_days, dry_run=args.dry_run)
    print(f"{'Would remove' if args.dry_run else 'Removed'} {len(removed)} image(s); kept last {keep_days} build-day(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
