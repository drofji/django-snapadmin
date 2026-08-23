"""
Deprecated alias for ``snapadmin_db_backup``.

Kept so an existing crontab, Celery Beat entry or deploy script does not break; it prints a
rename notice on stderr and then runs the real command. See :mod:`snapadmin.management.aliases`
for why the rename happened.

**Removed in 1.0.** Switch to ``snapadmin_db_backup`` before upgrading past the `0.x` series.
"""

from snapadmin.management.aliases import deprecated_alias
from snapadmin.management.commands.snapadmin_db_backup import Command as _Command

Command = deprecated_alias(_Command, old="db_backup", new="snapadmin_db_backup")
