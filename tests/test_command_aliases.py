"""
tests/test_command_aliases.py — renamed management commands (#CLI8)

Three commands shipped without the package prefix the others use — ``db_backup``,
``purge_expired_data`` and ``send_error_digest`` next to ``snapadmin_info``, ``snapadmin_reindex``
and friends. Besides being inconsistent, generic names like ``db_backup`` can **collide with a
project's own command**, and Django resolves duplicates silently by ``INSTALLED_APPS`` order.

They are now ``snapadmin_*``. A command name is public API — it lives in crontabs, Dockerfiles and
CI — so the old names keep working, with a rename notice on stderr. These tests pin both halves:
the new names exist and the old ones still do exactly the same thing.
"""

from io import StringIO

import pytest
from django.core.management import call_command, get_commands

RENAMES = [
    ("db_backup", "snapadmin_db_backup"),
    ("purge_expired_data", "snapadmin_purge_expired_data"),
    ("send_error_digest", "snapadmin_send_error_digest"),
]


class TestBothNamesAreRegistered:
    @pytest.mark.parametrize("old,new", RENAMES)
    def test_new_name_exists(self, old, new):
        assert get_commands().get(new) == "snapadmin"

    @pytest.mark.parametrize("old,new", RENAMES)
    def test_old_name_still_exists(self, old, new):
        assert get_commands().get(old) == "snapadmin"

    def test_every_snapadmin_command_is_prefixed(self):
        """Any *new* command must carry the prefix; the three aliases are the exception."""
        legacy = {old for old, _ in RENAMES}
        ours = {
            name for name, app in get_commands().items()
            if app == "snapadmin" and name not in legacy
        }
        assert ours and all(name.startswith("snapadmin_") for name in ours), ours


class TestAliasBehaviour:
    @pytest.mark.parametrize("old,new", RENAMES)
    def test_alias_subclasses_the_real_command(self, old, new):
        from django.core.management import load_command_class

        alias = type(load_command_class("snapadmin", old))
        real = type(load_command_class("snapadmin", new))
        assert issubclass(alias, real)

    @pytest.mark.parametrize("old,new", RENAMES)
    def test_alias_help_points_at_the_new_name(self, old, new):
        from django.core.management import load_command_class

        assert new in load_command_class("snapadmin", old).help

    @pytest.mark.parametrize("old,new", RENAMES)
    def test_alias_help_names_the_removal_window(self, old, new):
        """The 1.0 removal window is decided; the help text must say so, not just "removed"."""
        from django.core.management import load_command_class

        assert "removed in 1.0" in load_command_class("snapadmin", old).help

    @pytest.mark.parametrize("old,new", RENAMES)
    def test_alias_keeps_the_real_arguments(self, old, new):
        from django.core.management import load_command_class

        def options(name):
            parser = load_command_class("snapadmin", name).create_parser("manage.py", name)
            return {action.dest for action in parser._actions}

        assert options(old) == options(new)


@pytest.mark.django_db
class TestAliasStillRuns:
    def test_old_name_runs_and_warns(self):
        out, err = StringIO(), StringIO()
        call_command("purge_expired_data", "--dry-run", stdout=out, stderr=err)

        assert "Dry run complete" in out.getvalue()
        notice = err.getvalue()
        assert "renamed" in notice
        assert "snapadmin_purge_expired_data" in notice
        assert "removed in 1.0" in notice

    def test_new_name_is_quiet(self):
        out, err = StringIO(), StringIO()
        call_command("snapadmin_purge_expired_data", "--dry-run", stdout=out, stderr=err)

        assert "Dry run complete" in out.getvalue()
        assert err.getvalue() == ""

    def test_notice_goes_to_stderr_so_piped_output_is_unaffected(self):
        """A cron job that pipes stdout must still see the warning."""
        out, err = StringIO(), StringIO()
        call_command("purge_expired_data", "--dry-run", stdout=out, stderr=err)

        assert "renamed" not in out.getvalue()
        assert "renamed" in err.getvalue()

    def test_no_color_strips_the_ansi_escapes(self):
        """``--no-color`` reaches the notice too, which a log file depends on.

        The notice is written before ``BaseCommand.execute()`` applies the flag, so
        it has to honour it itself — otherwise ``2>> deploy.log`` collects escape
        sequences. The default stderr wrapper also styles as ERROR, so the plain
        path has to neutralise ``style_func``, not merely skip ``self.style``.
        """
        out, err = StringIO(), StringIO()
        call_command("purge_expired_data", "--dry-run", "--no-color", stdout=out, stderr=err)

        written = err.getvalue()
        assert "renamed" in written
        assert "\033" not in written

    def test_send_error_digest_alias_runs(self):
        out, err = StringIO(), StringIO()
        call_command("send_error_digest", stdout=out, stderr=err)
        assert "snapadmin_send_error_digest" in err.getvalue()

    def test_db_backup_alias_runs(self, settings):
        settings.SNAPADMIN_BACKUP_ENABLED = False
        out, err = StringIO(), StringIO()
        call_command("db_backup", stdout=out, stderr=err)
        assert "snapadmin_db_backup" in err.getvalue()
