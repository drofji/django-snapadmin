"""
Argument parsing and orchestration for ``snapadmin-demo``.

Core flow: resolve the version → download + cache → extract ``demo/`` → optionally write a ``.env``
(from the interactive wizard, a saved config, or non-interactive flags) → install / migrate / seed /
serve.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snapadmin.quickstart import (
    QuickstartError,
    TagNotFoundError,
    config as config_mod,
    extract,
    fetch,
    run,
    wizard,
)

_CONFIG_FLAG_KEYS = ("mode", "database", "db_host", "db_port", "db_user", "db_password", "db_name", "admin_password")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapadmin-demo",
        description="Download and run the SnapAdmin demo — no existing project required.",
    )
    parser.add_argument("--version", help="Release version to download (default: the installed django-snapadmin version).")
    parser.add_argument("--path", default=".", help="Directory to extract demo/ into (default: the current directory).")
    parser.add_argument("--skip-install", action="store_true", help="Do not pip-install the demo requirements.")
    parser.add_argument("--no-serve", action="store_true", help="Prepare everything but do not start the server.")
    parser.add_argument("--clear-cache", action="store_true", help="Delete the cached downloads before running.")
    parser.add_argument("-y", "--yes", action="store_true", help="Assume yes — replace existing demo files without asking.")

    config_group = parser.add_argument_group("configuration")
    config_group.add_argument("--interactive", action="store_true", help="Configure the demo through an interactive wizard.")
    config_group.add_argument("--load-config", metavar="FILE", help="Load a saved configuration (.ini).")
    config_group.add_argument("--save-config", metavar="FILE", help="Save the resolved configuration to a file (.ini).")
    config_group.add_argument("--mode", choices=["runserver", "docker"], help="Run mode.")
    config_group.add_argument("--database", choices=["sqlite", "postgresql"], help="Database backend.")
    config_group.add_argument("--db-host", dest="db_host", help="PostgreSQL host.")
    config_group.add_argument("--db-port", dest="db_port", help="PostgreSQL port.")
    config_group.add_argument("--db-user", dest="db_user", help="PostgreSQL user.")
    config_group.add_argument("--db-password", dest="db_password", help="PostgreSQL password.")
    config_group.add_argument("--db-name", dest="db_name", help="PostgreSQL database name.")
    config_group.add_argument("--admin-password", dest="admin_password", help="Demo superuser password.")
    config_group.add_argument("--elasticsearch", dest="elasticsearch", action="store_true", default=None, help="Enable Elasticsearch.")
    config_group.add_argument("--no-elasticsearch", dest="elasticsearch", action="store_false", help="Disable Elasticsearch.")
    config_group.add_argument("--debug", action="store_true", help="Enable Django debug mode in the generated .env.")
    config_group.add_argument("--no-secret-key", dest="no_secret_key", action="store_true", help="Do not auto-generate a SECRET_KEY.")
    return parser


def _config_from_args(args: argparse.Namespace) -> dict | None:
    """Resolve a demo configuration from --load-config / --interactive / flags, or ``None``."""
    if args.load_config:
        return config_mod.load_config(args.load_config)
    if args.interactive:
        return wizard.run_wizard()

    config: dict = {key: getattr(args, key) for key in _CONFIG_FLAG_KEYS if getattr(args, key) is not None}
    if args.elasticsearch is not None:
        config["elasticsearch"] = args.elasticsearch
    if args.debug:
        config["debug"] = True
    if not config:
        return None
    config["generate_secret_key"] = not args.no_secret_key
    return config


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        version = fetch.resolve_version(args.version)
        config = _config_from_args(args)

        print(f"Downloading SnapAdmin demo v{version} …")
        archive = fetch.download_demo(version, clear_cache=args.clear_cache)
        print("Extracting demo/ …")
        demo_dir = extract.extract_demo(archive, Path(args.path), assume_yes=args.yes)

        if config is not None:
            env_path = wizard.write_env(config, demo_dir / ".env")
            print(f"Wrote configuration to {env_path}")
            if args.save_config:
                config_mod.save_config(config, Path(args.save_config))
                print(f"Saved configuration to {args.save_config}")

        run.run_demo(
            demo_dir,
            skip_install=args.skip_install,
            no_serve=args.no_serve,
            mode=(config or {}).get("mode", "runserver"),
        )
        return 0
    except TagNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except QuickstartError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
