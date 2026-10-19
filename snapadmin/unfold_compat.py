"""
Decide once whether the optional Unfold theme is active — and say why when it is not.

``django-unfold`` is the ``[theme]`` extra. SnapAdmin uses its admin classes when the
package imports *and* ``'unfold'`` is in ``INSTALLED_APPS``, Django's stock admin
otherwise. ``snapadmin.admin``, ``snapadmin.admin_gen`` and the auth/extra-settings
styling each used to decide that on their own, catching ``ImportError`` without a
word — so a custom user model that imported SnapAdmin (an import cycle: Unfold half
loaded) produced an unthemed admin with nothing in the log, and two modules could
even disagree, rendering half a theme (#EXT3j).

Now every one of them asks :func:`detect_unfold`. The **first** decision is kept for
the process, so they always agree; an Unfold that is listed in ``INSTALLED_APPS`` but
fails to import is logged (``snapadmin.unfold_import_failed``) and reported by
``snapadmin.W032`` with the error, while a theme that is simply not installed stays
the informational ``snapadmin.I001``.
"""

from __future__ import annotations

from django.conf import settings

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: Why the stock admin is in use: Unfold is not in ``INSTALLED_APPS``.
NOT_INSTALLED = "not in INSTALLED_APPS"

#: ``(active, reason)`` — the process-wide decision, made on first use.
_decision: tuple[bool, str | None] | None = None


def detect_unfold() -> tuple[bool, str | None]:
    """``(True, None)`` when the Unfold admin is in use, else ``(False, reason)``.

    ``reason`` is :data:`NOT_INSTALLED`, or ``"import failed: <error>"`` for an
    Unfold that ``INSTALLED_APPS`` lists but that could not be imported — the case
    worth a warning, because the operator asked for the theme.
    """
    global _decision
    if _decision is None:
        _decision = _decide()
    return _decision


def _decide() -> tuple[bool, str | None]:
    if "unfold" not in settings.INSTALLED_APPS:
        return False, NOT_INSTALLED
    try:
        import unfold.admin  # noqa: F401
        import unfold.contrib.filters.admin  # noqa: F401
        import unfold.decorators  # noqa: F401
    except (ImportError, RuntimeError) as exc:
        logger.warning("snapadmin.unfold_import_failed", error=str(exc))
        return False, f"import failed: {exc}"
    return True, None
