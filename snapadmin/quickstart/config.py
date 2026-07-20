"""
Save / load a demo configuration as an ``.ini`` file.

Lets one person capture a configuration (``--save-config team.ini``) and share it, so colleagues
reproduce the same environment with ``--load-config team.ini``. Values round-trip as strings; the
``.env`` writer and the wizard interpret booleans leniently (see :func:`snapadmin.quickstart.wizard._truthy`).
"""

from __future__ import annotations

import configparser
from pathlib import Path

from snapadmin.quickstart import QuickstartError

SECTION = "snapadmin-demo"


def save_config(config: dict, path: Path) -> Path:
    """Write ``config`` to ``path`` as an ``.ini`` file under the ``[snapadmin-demo]`` section."""
    parser = configparser.ConfigParser()
    parser[SECTION] = {key: str(value) for key, value in config.items()}
    path = Path(path)
    with open(path, "w") as handle:
        parser.write(handle)
    return path


def load_config(path: Path) -> dict:
    """Load a configuration written by :func:`save_config`."""
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise QuickstartError(f"Config file not found: {path}")
    if not parser.has_section(SECTION):
        raise QuickstartError(f"Config file has no [{SECTION}] section: {path}")
    return dict(parser[SECTION])
