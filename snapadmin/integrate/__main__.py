"""Entry point for ``python -m snapadmin.integrate`` (mirrors the ``snapadmin-init`` script)."""

from __future__ import annotations

import sys

from snapadmin.integrate.cli import main

if __name__ == "__main__":
    sys.exit(main())
