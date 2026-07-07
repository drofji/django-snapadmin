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
"""
from __future__ import annotations

from typing import Callable

import nh3
from django.conf import settings
from django.utils.module_loading import import_string


def _default_sanitizer(value: str) -> str:
    """Sanitize *value* with nh3's built-in allowlist."""
    return nh3.clean(value)


def sanitize_html(value: str) -> str:
    """Return *value* with unsafe HTML removed.

    Empty values are returned unchanged. When ``SNAPADMIN_HTML_SANITIZER`` is
    set to a dotted import path, that callable is used instead of the built-in
    nh3 sanitizer.
    """
    if not value:
        return value
    dotted = getattr(settings, "SNAPADMIN_HTML_SANITIZER", None)
    sanitizer: Callable[[str], str] = (
        import_string(dotted) if dotted else _default_sanitizer
    )
    return sanitizer(value)
