"""
System-checks collector for ``snapadmin_info``.

Django runs its system checks before every management command and prints each message in full.
For a diagnostics command that is exactly backwards: the report a user asked for scrolls off the
top behind a wall of advisory text — and SnapAdmin's own advisory checks are the bulk of it.

``snapadmin_info`` therefore opts out of the automatic run (``requires_system_checks = []``) and
surfaces the result here instead: a one-line-per-severity **count**, with the messages themselves
shown only under ``--verbose``. Errors are the exception — they block a working deployment, so
their messages are always listed, however the command was invoked.

``ok`` is ``False`` when any check reports ``ERROR`` or worse, so ``--health-check`` treats a
broken configuration as a failed probe.
"""

from __future__ import annotations

from django.core import checks

from snapadmin.diagnostics.registry import register

#: Severity buckets, most severe first. Django's levels: DEBUG 10 … CRITICAL 50.
_LEVELS: tuple[tuple[str, int, int], ...] = (
    ("critical", checks.CRITICAL, 10**9),
    ("errors", checks.ERROR, checks.CRITICAL),
    ("warnings", checks.WARNING, checks.ERROR),
    ("infos", checks.INFO, checks.WARNING),
)

#: At or above this level a message is always printed, not just counted.
_ALWAYS_SHOW = checks.ERROR


def _describe(message: checks.CheckMessage) -> str:
    """One compact line per message: ``(id) text``, hint dropped unless verbose."""
    identifier = f"({message.id}) " if message.id else ""
    return f"{identifier}{message.msg}"


@register("checks", title="System checks", icon="🩺", order=5, health_probe=True)
def collect(*, verbose: bool) -> dict:
    """Collect the Django system-check summary."""
    messages = checks.run_checks(include_deployment_checks=False)

    data: dict = {}
    blocking: list[checks.CheckMessage] = []
    for label, floor, ceiling in _LEVELS:
        bucket = [m for m in messages if floor <= m.level < ceiling]
        if bucket:
            data[label] = len(bucket)
        if floor >= _ALWAYS_SHOW:
            blocking.extend(bucket)

    data["ok"] = not blocking
    if not data.get("ok") or verbose:
        shown = messages if verbose else blocking
        if shown:
            data["messages"] = [_describe(message) for message in shown]
    elif messages:
        data["detail"] = "run `manage.py check` for the full text"
    return data
