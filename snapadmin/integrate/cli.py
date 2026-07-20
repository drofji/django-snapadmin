"""Argument parsing and orchestration for the read-only ``snapadmin-init`` doctor."""

from __future__ import annotations

import argparse
import sys

from snapadmin.integrate import IntegrateError, detect, report
from snapadmin.integrate import steps as steps_mod


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapadmin-init",
        description="Report how to integrate SnapAdmin into an existing Django project. "
        "Read-only — it prints snippets to paste and never edits your files.",
    )
    parser.add_argument("--path", default=".", help="Project root (default: current directory).")
    parser.add_argument("--settings", help="Path to settings.py (auto-detected if omitted).")
    parser.add_argument("--urls", help="Path to the root urls.py (auto-detected if omitted).")
    parser.add_argument("--url-prefix", dest="url_prefix", default="", help="URL prefix for the SnapAdmin routes, e.g. api/.")
    parser.add_argument("--extras", help="Comma-separated extras for the install line, e.g. elasticsearch,celery.")
    parser.add_argument("--api", action="store_true", help="Also check the REST framework configuration.")
    parser.add_argument("--graphql", action="store_true", help="Also check the GraphQL configuration.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the report as JSON.")
    return parser


def _split_extras(value: str | None) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = detect.build_context(
            project_dir=args.path,
            settings=args.settings,
            urls=args.urls,
            url_prefix=args.url_prefix,
            extras=_split_extras(args.extras),
            include_api=args.api,
            include_graphql=args.graphql,
        )
    except IntegrateError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    steps = steps_mod.check_project(ctx)
    if args.as_json:
        print(report.render_json(steps, ctx))
    else:
        print(report.render_text(steps, ctx))
    return 0
