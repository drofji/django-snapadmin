"""HTML sanitization for wysiwyg field values rendered in the admin.

Wysiwyg (rich-text) fields store raw HTML and default to ``show_in_list=True``,
so their value is rendered in the admin changelist. Rendering it verbatim would
let anyone able to write the field — a REST API token holder, a low-privileged
staff user, a bulk import — inject markup that executes in an administrator's
browser session (stored XSS). Every wysiwyg value is therefore run through
:func:`sanitize_html` before it is marked safe, unless the field opts out with
``safe_html=True`` for content the developer fully trusts.

The default sanitizer uses :mod:`nh3` (a Rust HTML sanitizer) with its built-in
allowlist: it keeps common rich-text markup while stripping ``<script>``, inline
event handlers (``onerror`` &c.) and unsafe URL schemes (``javascript:``).
Projects that need a different policy can set ``SNAPADMIN_HTML_SANITIZER`` to a
dotted import path pointing at their own ``Callable[[str], str]``.

``nh3`` is imported lazily through :func:`_load_nh3` rather than at module load,
even though it is currently a required core dependency (see ``pyproject.toml``)
and this import cannot fail in a released install today. This is defense in
depth ahead of a planned future release that moves ``nh3`` behind an optional
extra (mirroring ``[wysiwyg]``/``[xlsx]``): once that happens, a missing ``nh3``
must fail *closed* — a pointed :class:`~django.core.exceptions.ImproperlyConfigured`
raised at the moment sanitization is attempted — instead of either crashing the
whole module at Django startup or, worse, silently letting unsanitized HTML
through. The ``SNAPADMIN_HTML_SANITIZER`` escape hatch never touches ``nh3`` at
all, so it keeps working even in that state.
"""
from __future__ import annotations

import functools
from types import ModuleType
from typing import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


@functools.lru_cache(maxsize=1)
def _load_nh3() -> ModuleType:
    """Return the imported ``nh3`` module, imported lazily and cached.

    Mirrors the house lazy-import pattern (see ``exporting._load_openpyxl()``):
    the import is attempted only when sanitization is actually about to run,
    and ``ImportError`` is translated into an actionable ``ImproperlyConfigured``
    naming exactly what is missing and how to fix it, rather than surfacing a
    bare ``ModuleNotFoundError`` from deep inside a save or a changelist render.
    A successful import is cached for the life of the process so the cost is
    paid once, not on every sanitize call.
    """
    try:
        import nh3
    except ImportError as exc:
        raise ImproperlyConfigured(
            "HTML sanitization needs the nh3 library, which could not be "
            "imported. Install it (`pip install nh3`), or avoid the "
            "dependency entirely by pointing SNAPADMIN_HTML_SANITIZER at a "
            "dotted path to your own Callable[[str], str] sanitizer."
        ) from exc
    return nh3


def _default_sanitizer(value: str) -> str:
    """Sanitize *value* with nh3's built-in allowlist."""
    return _load_nh3().clean(value)


def sanitize_html(value: str) -> str:
    """Return *value* with unsafe HTML removed.

    Empty values are returned unchanged. When ``SNAPADMIN_HTML_SANITIZER`` is
    set to a dotted import path, that callable is used instead of the built-in
    nh3 sanitizer -- and ``nh3`` is never imported at all in that case, so the
    setting keeps working even if ``nh3`` is unavailable. Otherwise, if the
    built-in nh3 sanitizer cannot be loaded, this raises
    :class:`~django.core.exceptions.ImproperlyConfigured` rather than returning
    the value unsanitized.
    """
    if not value:
        return value
    dotted = getattr(settings, "SNAPADMIN_HTML_SANITIZER", None)
    sanitizer: Callable[[str], str] = (
        import_string(dotted) if dotted else _default_sanitizer
    )
    return sanitizer(value)
