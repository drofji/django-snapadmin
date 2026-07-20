"""
Argument parsing and orchestration for ``snapadmin-demo``.

Wave 1 wires the core flow: resolve the version → download + cache → extract ``demo/`` → install /
migrate / seed / serve. The interactive wizard and saved-config flags plug in here later without
changing this contract.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snapadmin.quickstart import QuickstartError, TagNotFoundError, extract, fetch, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapadmin-demo",
        description="Download and run the SnapAdmin demo — no existing project required.",
    )
    parser.add_argument(
        "--version",
        help="Release version to download (default: the installed django-snapadmin version).",
    )
    parser.add_argument(
        "--path",
        default=".",
        help="Directory to extract demo/ into (default: the current directory).",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Do not pip-install the demo requirements.",
    )
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="Prepare everything but do not start the server.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete the cached downloads under ~/.cache/snapadmin-demo/ before running.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Assume yes — replace existing demo files without asking.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        version = fetch.resolve_version(args.version)
        print(f"Downloading SnapAdmin demo v{version} …")
        archive = fetch.download_demo(version, clear_cache=args.clear_cache)
        print("Extracting demo/ …")
        demo_dir = extract.extract_demo(archive, Path(args.path), assume_yes=args.yes)
        run.run_demo(demo_dir, skip_install=args.skip_install, no_serve=args.no_serve)
        return 0
    except TagNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except QuickstartError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
