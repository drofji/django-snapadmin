"""
snapadmin/management/aliases.py

Backward-compatible aliases for renamed management commands.

Three of SnapAdmin's commands shipped without the package prefix the others use — ``db_backup``,
``purge_expired_data`` and ``send_error_digest`` next to ``snapadmin_info``, ``snapadmin_reindex``
and friends. Besides being inconsistent, those names are generic enough to **collide with a
project's own command of the same name**: Django resolves duplicates silently by ``INSTALLED_APPS``
order, so whichever app wins, wins quietly.

The commands are now ``snapadmin_db_backup``, ``snapadmin_purge_expired_data`` and
``snapadmin_send_error_digest``. A management-command name is public API — it lives in people's
crontabs, Dockerfiles and CI — so the old names keep working through the aliases built here: same
arguments, same behaviour, plus one line on stderr pointing at the new name. Removing them is a
future breaking change with its own release note.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, OutputWrapper


def deprecated_alias(base: type[BaseCommand], *, old: str, new: str) -> type[BaseCommand]:
    """Build a ``Command`` that behaves like ``base`` but warns it has been renamed.

    The notice goes to **stderr**, so a cron job that pipes stdout somewhere still surfaces it,
    and piping the command's real output stays unaffected.
    """

    class Alias(base):  # type: ignore[valid-type, misc]
        help = f"Deprecated alias for `{new}`. Use `{new}` instead; this name will be removed."

        def execute(self, *args, **options):
            # BaseCommand.execute() installs the caller's stderr, but only after it
            # starts — and the notice belongs *before* the command's own output. Apply
            # the same redirect first so call_command(stderr=…) captures it too.
            if options.get("stderr"):
                self.stderr = OutputWrapper(options["stderr"])
            notice = (
                f"`manage.py {old}` has been renamed to `manage.py {new}`. "
                f"The old name still works but will be removed in a future release — "
                f"update your crontab, Celery Beat entries and deploy scripts."
            )
            # --no-color is applied by BaseCommand.execute(), which has not run yet,
            # so honour it here: a notice appended to a log file must not carry ANSI
            # escapes. The stderr wrapper styles as ERROR by default, so plain output
            # needs its style_func neutralised too, not just an unstyled string.
            if options.get("no_color"):
                self.stderr.write(notice, style_func=lambda message: message)
            else:
                self.stderr.write(self.style.WARNING(notice))
            return super().execute(*args, **options)

    Alias.__name__ = f"Deprecated{base.__name__}"
    Alias.__qualname__ = Alias.__name__
    Alias.__doc__ = f"Deprecated alias for ``{new}``."
    return Alias
