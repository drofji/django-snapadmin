"""
``snapadmin-init`` — a read-only integration doctor.

It inspects an existing Django project and reports what SnapAdmin wiring is already present and what
is missing, printing the exact block to paste for each missing piece (INSTALLED_APPS with the right
ordering, the URL include, a settings block, optional REST/GraphQL config, the install line, and
advisory model-conversion hints). **It never edits the project** — the worst it does is print a
snippet you review before pasting, which is safer and more trustworthy than an automatic edit.

Like the demo bootstrapper it is a stdlib-only console script (``snapadmin-init`` /
``python -m snapadmin.integrate``) and must not import Django at module-import time — it runs against
a project before SnapAdmin is installed into it.
"""

from __future__ import annotations

__all__ = ["IntegrateError"]


class IntegrateError(Exception):
    """A user-facing failure of the integration doctor (printed without a traceback)."""
