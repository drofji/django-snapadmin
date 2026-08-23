"""Entry point for ``python -m snapadmin.scaffold`` (mirrors the ``snapadmin-new`` script)."""

from __future__ import annotations

import sys

from snapadmin.scaffold.cli import main

if __name__ == "__main__":
    sys.exit(main())
