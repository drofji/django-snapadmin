"""
Deprecated alias for ``snapadmin_send_error_digest``.

Kept so an existing crontab, Celery Beat entry or deploy script does not break; it prints a
rename notice on stderr and then runs the real command. See :mod:`snapadmin.management.aliases`
for why the rename happened.

**Removed in 1.0.** Switch to ``snapadmin_send_error_digest`` before upgrading past the `0.x`
series.
"""

from snapadmin.management.aliases import deprecated_alias
from snapadmin.management.commands.snapadmin_send_error_digest import Command as _Command

Command = deprecated_alias(_Command, old="send_error_digest", new="snapadmin_send_error_digest")
