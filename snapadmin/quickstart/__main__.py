"""Entry point for ``python -m snapadmin.quickstart`` (mirrors the ``snapadmin-demo`` script)."""

from __future__ import annotations

import sys

from snapadmin.quickstart.cli import main

if __name__ == "__main__":
    sys.exit(main())
