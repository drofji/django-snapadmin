"""Argument parsing and orchestration for ``snapadmin-new``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snapadmin.scaffold import ScaffoldError, render, validate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapadmin-new",
        description="Generate a runnable Django + SnapAdmin project: an admin panel, a "
        "REST API and a GraphQL endpoint from one worked model — SQLite, no Docker, no "
        "manual edits.",
        epilog="Examples:\n"
        "  snapadmin-new myshop\n"
        "  snapadmin-new myshop --app-name storefront\n"
        "  snapadmin-new myshop --full   # + Dockerfile, docker-compose.yml, Postgres/Redis/ES\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_name", help="Name of the project (also its settings-package name).")
    parser.add_argument(
        "--path", default=".", help="Directory to create the project in (default: the current directory)."
    )
    parser.add_argument(
        "--app-name",
        dest="app_name",
        default="catalog",
        help="Name of the app carrying the worked SnapModel example (default: catalog).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also write a Dockerfile, docker-compose.yml and the Postgres/Redis/Elasticsearch wiring.",
    )
    return parser


def _print_next_steps(dest: Path, *, full: bool) -> None:
    print()
    print("Next steps:")
    print(f"  cd {dest}")
    print("  python manage.py migrate")
    print("  python manage.py createsuperuser")
    print("  python manage.py runserver")
    print()
    print("Then open http://127.0.0.1:8000/admin/")
    if full:
        print()
        print("Or, with Docker instead: docker compose up --build")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate.validate_identifier_name(args.project_name, kind="project")
        validate.validate_identifier_name(args.app_name, kind="app")
        if args.app_name == args.project_name:
            raise ScaffoldError(
                f"The app name {args.app_name!r} is the same as the project name — pass "
                "--app-name to give the example app a different name."
            )

        dest = (Path(args.path) / args.project_name).resolve()
        validate.validate_target_directory(dest)

        print(f"Creating {args.project_name} in {dest} …")
        written = render.generate_project(
            dest,
            project_name=args.project_name,
            app_name=args.app_name,
            full=args.full,
        )
        print(f"Wrote {len(written)} file(s).")
        _print_next_steps(dest, full=args.full)
        return 0
    except ScaffoldError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
