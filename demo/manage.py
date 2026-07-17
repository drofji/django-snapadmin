#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo.core.settings')
    # This file lives one directory below the repo root (demo/manage.py), but
    # Python only puts the *script's own* directory on sys.path[0] by default —
    # the repo root (where the `demo` and `snapadmin` packages actually live)
    # would never be importable otherwise. Insert it explicitly so `python
    # demo/manage.py <command>` works regardless of the caller's cwd.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
