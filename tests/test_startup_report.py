"""
tests/test_startup_report.py

The startup report (#DX1): what SnapAdmin has switched on, said once when a
development server starts, so a capability nobody switched on is not a
capability nobody knows about.

What is pinned here, because each is a way the report could hurt the project
it is trying to help:

* **When it prints.** ``"auto"`` (the default) prints only for a development
  server under ``DEBUG`` — never on a production worker, never on every
  ``migrate``. ``True`` prints for every process except the commands whose
  output is read by a program. Never under pytest. Once per process, and only
  in the ``runserver`` child that serves requests, not its autoreloader.
* **What it costs.** No query, no connection: it runs inside ``django.setup()``.
  The encryption keyset is never resolved (a key provider may call a KMS).
* **What it says.** Booleans, extra names and package names — never a key, a
  DSN, a fingerprint, a URL or a token.
* **That it can never stop a boot.** A failure is logged and swallowed.
* **One implementation.** ``snapadmin_info --startup`` prints the same block,
  byte for byte.
"""

from __future__ import annotations

import io
import json
import socket
from io import StringIO

import pytest
from django.core.checks import Warning as CheckWarning
from django.core.management import call_command
from django.test import override_settings

from snapadmin.diagnostics import startup

RUNSERVER_CHILD = {"RUN_MAIN": "true"}


def _decide(mode, *, debug=True, argv=("manage.py", "runserver"), environ=None, pytest_loaded=False):
    return startup._should_emit(
        mode,
        debug=debug,
        argv=list(argv),
        environ=RUNSERVER_CHILD if environ is None else environ,
        under_pytest=pytest_loaded,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The setting
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "configured, expected",
    [("auto", "auto"), (True, True), (False, False), ("yes", "auto"), (1, "auto"), (None, "auto")],
)
def test_the_setting_resolves_to_one_of_three_modes(configured, expected, settings):
    settings.SNAPADMIN_STARTUP_REPORT = configured

    assert startup.report_mode() == expected


def test_the_default_is_auto(settings):
    del settings.SNAPADMIN_STARTUP_REPORT

    assert startup.report_mode() == "auto"


@pytest.mark.parametrize("configured", ["yes", 1, "on", ["auto"]])
def test_an_unusable_value_is_reported_by_a_system_check(configured, settings):
    from snapadmin.checks import check_startup_report_setting

    settings.SNAPADMIN_STARTUP_REPORT = configured

    found = check_startup_report_setting(None)

    assert [message.id for message in found] == ["snapadmin.W027"]
    assert isinstance(found[0], CheckWarning)
    assert repr(configured) in found[0].msg


@pytest.mark.parametrize("configured", ["auto", True, False])
def test_a_valid_value_raises_no_check(configured, settings):
    from snapadmin.checks import check_startup_report_setting

    settings.SNAPADMIN_STARTUP_REPORT = configured

    assert check_startup_report_setting(None) == []


# ─────────────────────────────────────────────────────────────────────────────
# When it prints
# ─────────────────────────────────────────────────────────────────────────────

def test_auto_prints_for_the_development_server_under_debug():
    assert _decide("auto") is True


@pytest.mark.parametrize("command", ["runserver_plus", "runsslserver"])
def test_auto_covers_the_other_development_servers(command):
    assert _decide("auto", argv=("manage.py", command)) is True


def test_auto_is_silent_without_debug():
    assert _decide("auto", debug=False) is False


@pytest.mark.parametrize("command", ["migrate", "shell", "makemigrations", "createsuperuser"])
def test_auto_is_silent_for_every_other_command(command):
    assert _decide("auto", argv=("manage.py", command)) is False


def test_auto_is_silent_for_a_wsgi_server():
    assert _decide("auto", argv=("gunicorn", "core.wsgi")) is False


def test_the_autoreloader_parent_stays_silent_and_its_child_prints():
    parent = _decide("auto", environ={})
    child = _decide("auto", environ={"RUN_MAIN": "true"})

    assert (parent, child) == (False, True)


def test_runserver_plus_reports_from_its_werkzeug_child():
    # django-extensions reloads through Werkzeug, which marks its child with
    # WERKZEUG_RUN_MAIN, never Django's RUN_MAIN.
    argv = ("manage.py", "runserver_plus")

    parent = _decide("auto", argv=argv, environ={})
    child = _decide("auto", argv=argv, environ={"WERKZEUG_RUN_MAIN": "true"})

    assert (parent, child) == (False, True)


def test_runserver_without_the_reloader_prints_in_its_only_process():
    assert _decide("auto", argv=("manage.py", "runserver", "--noreload"), environ={}) is True


def test_true_prints_for_any_process():
    assert _decide(True, debug=False, argv=("manage.py", "migrate")) is True
    assert _decide(True, debug=False, argv=("gunicorn", "core.wsgi")) is True


@pytest.mark.parametrize(
    "argv",
    [
        ("manage.py", "snapadmin_info"),
        ("manage.py", "dumpdata", "demo"),
        ("manage.py", "shell", "-c", "print(1)"),
        ("manage.py", "dbshell"),
        ("manage.py", "diffsettings"),
        ("manage.py", "sqlmigrate", "demo", "0001"),
        ("manage.py", "inspectdb"),
        ("manage.py", "test"),
        ("manage.py", "help"),
        ("manage.py", "snapadmin_license_check", "--json"),
        ("django-admin", "dumpdata"),
    ],
)
def test_true_stays_silent_for_machine_read_commands(argv):
    assert _decide(True, argv=argv) is False


DJANGO_MAIN = "/venv/lib/python3.12/site-packages/django/__main__.py"


def test_python_dash_m_django_is_recognised_as_a_management_command():
    # `python -m django runserver`: by the time settings load, sys.argv[0] is
    # the path of django/__main__.py — never the literal "-m".
    assert _decide("auto", argv=(DJANGO_MAIN, "runserver")) is True
    assert _decide(True, argv=(DJANGO_MAIN, "dumpdata")) is False


def test_another_package_run_with_dash_m_is_not_a_management_command():
    other_main = "/venv/lib/python3.12/site-packages/celery/__main__.py"

    assert _decide("auto", argv=(other_main, "runserver")) is False


@pytest.mark.parametrize("argv", [("gunicorn",), ()])
def test_a_process_with_no_command_argument_is_no_management_command(argv):
    assert startup._command(list(argv)) is None
    assert _decide("auto", argv=argv) is False
    assert _decide(True, argv=argv, environ={}) is True


def test_shell_completion_is_never_disturbed():
    assert _decide(True, environ={"DJANGO_AUTO_COMPLETE": "1"}) is False


def test_false_never_prints():
    assert _decide(False) is False


@pytest.mark.parametrize("mode", ["auto", True])
def test_nothing_prints_under_pytest(mode):
    assert _decide(mode, pytest_loaded=True) is False
    assert _decide(mode, environ={"RUN_MAIN": "true", "PYTEST_CURRENT_TEST": "x"}) is False


# ─────────────────────────────────────────────────────────────────────────────
# What it costs: nothing but reading configuration
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the startup report opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.mark.django_db
def test_the_report_runs_no_query_and_opens_no_connection(django_assert_num_queries, no_network):
    with django_assert_num_queries(0):
        text = startup.startup_report()

    assert text.startswith("SnapAdmin ")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "configured, environ",
    [
        ({"KEY_PROVIDER": "   "}, {}),                         # the resolver strips it to nothing
        ({}, {"SNAPADMIN_ENCRYPTION_KEYS": '""'}),               # a dotenv loader's empty quotes
        ({}, {"SNAPADMIN_ENCRYPTION_KEYS": "  "}),
        ({"KEYS": []}, {}),
    ],
)
def test_encryption_reads_as_off_wherever_the_resolver_finds_no_key(configured, environ, monkeypatch, settings):
    from snapadmin.diagnostics.features import _encryption_configured

    monkeypatch.delenv("SNAPADMIN_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("SNAPADMIN_ENCRYPTION_KEY_FILE", raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    settings.SNAPADMIN_ENCRYPTION = configured
    monkeypatch.setattr("snapadmin.diagnostics.features._encrypted_field_count", lambda: 0)

    assert _encryption_configured() == (False, "")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "configured, environ",
    [
        ({"KEY_PROVIDER": "myproject.kms.keys"}, {}),
        ({"KEY_FILE": "/run/secrets/keys"}, {}),
        ({}, {"SNAPADMIN_ENCRYPTION_KEY_FILE": "/run/secrets/keys"}),
        ({}, {"SNAPADMIN_ENCRYPTION_KEYS": "k1:" + "a" * 44}),
        ({"KEYS": [{"id": "k1", "key": "a" * 44}]}, {}),
    ],
)
def test_encryption_reads_as_on_for_each_key_source(configured, environ, monkeypatch, settings):
    from snapadmin.diagnostics.features import _encryption_configured

    monkeypatch.delenv("SNAPADMIN_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("SNAPADMIN_ENCRYPTION_KEY_FILE", raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    settings.SNAPADMIN_ENCRYPTION = configured
    monkeypatch.setattr("snapadmin.diagnostics.features._encrypted_field_count", lambda: 0)

    assert _encryption_configured() == (True, "")


@pytest.mark.django_db
def test_an_encryption_setting_of_the_wrong_shape_reads_as_misconfigured(settings):
    settings.SNAPADMIN_ENCRYPTION = "a" * 44  # a key pasted where the dict belongs

    facts = startup.startup_facts()

    assert "field_encryption" in facts["misconfigured"]
    assert "field_encryption" not in facts["off"]
    assert "a" * 44 not in repr(facts)


@pytest.mark.django_db
def test_the_encryption_keyset_is_never_resolved(monkeypatch, settings):
    from snapadmin.encryption import keys

    def resolve(*args, **kwargs):
        raise AssertionError("the startup report resolved the keyset")

    monkeypatch.setattr(keys, "get_keyset", resolve)
    settings.SNAPADMIN_ENCRYPTION = {"KEYS": {"1": "a" * 44}}

    facts = startup.startup_facts()

    assert "field_encryption" in facts["on"]


@pytest.mark.django_db
def test_api_tokens_are_not_checked_because_that_needs_the_database():
    facts = startup.startup_facts()

    assert facts["not_checked"]["api_tokens"] == "needs the database"
    assert "api_tokens" not in facts["on"] and "api_tokens" not in facts["off"]


# ─────────────────────────────────────────────────────────────────────────────
# What it says
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_the_block_groups_every_capability_once(settings):
    settings.SNAPADMIN_BACKUP_ENABLED = False
    settings.SNAPADMIN_AUDIT_LOG_ENABLED = True

    facts = startup.startup_facts()
    grouped = [*facts["on"], *facts["off"], *facts["misconfigured"], *facts["needs_extra"],
               *facts["not_checked"]]

    assert "audit_trail" in facts["on"]
    assert "backups" in facts["off"]
    assert len(grouped) == len(set(grouped))


@pytest.mark.django_db
def test_a_switched_on_capability_without_its_extra_is_flagged_not_listed_as_on(monkeypatch, settings):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "graphene_django" else real(name, *a)
    )
    settings.SNAPADMIN_GRAPHQL_ENABLED = True

    facts = startup.startup_facts()
    text = startup.render_startup(facts)

    assert facts["needs_extra"]["graphql"] == "graphql"
    assert "graphql" not in facts["on"]
    assert "pip install 'django-snapadmin[graphql]'" in text


@pytest.mark.django_db
def test_the_block_reads_as_a_summary(settings):
    settings.DEBUG = True
    text = startup.render_startup(startup.startup_facts())
    lines = text.splitlines()

    assert lines[0].startswith("SnapAdmin ") and "Django " in lines[0] and "Python " in lines[0]
    assert any(line.lstrip().startswith("✓ on") for line in lines)
    assert any(line.lstrip().startswith("✗ off") for line in lines)
    assert "Audit trail" in text
    assert "manage.py snapadmin_info" in text
    assert "SNAPADMIN_STARTUP_REPORT = False" in text
    assert "health probes skipped" in text


def _facts(**overrides):
    facts = {
        "version": "1.2.3", "django": "5.2", "python": "3.12.4",
        "on": [], "off": [], "misconfigured": [], "needs_extra": {}, "not_checked": {},
        "licences": {"concerns": []}, "health": None,
    }
    return {**facts, **overrides}


@pytest.mark.parametrize(
    "facts, absent",
    [
        (_facts(off=["backups"]), "✓ on"),
        (_facts(on=["audit_trail"]), "✗ off"),
    ],
)
def test_an_empty_group_prints_no_line(facts, absent):
    text = startup.render_startup(facts)

    assert absent not in text
    assert text.splitlines()[1].lstrip()[0] in "✓✗"


def test_a_misconfigured_capability_is_not_reported_as_simply_off():
    text = startup.render_startup(_facts(off=["backups"], misconfigured=["sharding"]))

    assert "  ⚠ misconfigured Sharding — run manage.py check" in text.splitlines()
    assert "Sharding" not in [line for line in text.splitlines() if "✗ off" in line][0]


@pytest.mark.django_db
def test_misconfigured_sharding_is_grouped_as_misconfigured(settings):
    settings.SNAPADMIN_SHARDING = {"ENABLED": True, "STRATEGY": "no-such-strategy",
                                   "DATABASES": ["not a dsn"]}

    facts = startup.startup_facts()

    assert "sharding" in facts["misconfigured"]
    assert "sharding" not in facts["off"]


def test_a_wrapped_group_never_splits_a_capability_name():
    many = ["pii_masking", "gdpr_subject_access", "connectivity_awareness", "show_in_form_default",
            "retention_purge", "read_only_models", "write_allowlist", "decorated_models",
            "field_encryption", "background_tasks", "error_monitoring", "tenant_scoping"]
    expected_labels = [startup._humanise(key) for key in many]

    lines = startup.render_startup(_facts(on=many)).splitlines()[1:-3]
    names_per_line = [line[len("  ✓ on           "):].rstrip(" ·").split(" · ") for line in lines]

    assert len(lines) > 1
    assert [name for names in names_per_line for name in names] == expected_labels
    assert all(len(line) <= startup._WRAP_WIDTH for line in lines)


@pytest.mark.django_db
def test_the_licence_line_names_a_package_to_review(monkeypatch):
    from snapadmin import licensing

    monkeypatch.setattr(
        licensing,
        "commercial_verdict",
        lambda statuses: {"commercial_ok": False, "core_all_permissive": True,
                          "concerns": ["django-ckeditor-5"]},
    )

    text = startup.startup_report()

    assert "Licences: review django-ckeditor-5 (snapadmin_license_check)" in text


@pytest.mark.django_db
def test_the_licence_line_when_every_package_is_fine(monkeypatch):
    from snapadmin import licensing

    monkeypatch.setattr(
        licensing,
        "commercial_verdict",
        lambda statuses: {"commercial_ok": True, "core_all_permissive": True, "concerns": []},
    )

    assert "Licences: every installed dependency allows commercial use" in startup.startup_report()


@pytest.mark.django_db
def test_nothing_secret_reaches_the_block(settings):
    secret_key = "k" * 43 + "="
    settings.SNAPADMIN_ENCRYPTION = {"KEYS": {"prod-2026": secret_key}}
    settings.SNAPADMIN_ALERT_WEBHOOKS = ["https://hooks.example.com/services/T000/SECRET-PATH"]
    settings.SNAPADMIN_HEALTH_ALERT_EMAILS = ["ops@example.com"]
    settings.CELERY_BROKER_URL = "redis://:broker-password@redis.internal:6379/0"
    settings.SNAPADMIN_BACKUP_SFTP_HOST = "backup.internal"

    facts = startup.startup_facts()
    text = startup.render_startup(facts)

    for secret in (secret_key, "prod-2026", "SECRET-PATH", "hooks.example.com", "ops@example.com",
                   "broker-password", "redis.internal", "backup.internal"):
        assert secret not in text
        assert secret not in repr(facts)


# ─────────────────────────────────────────────────────────────────────────────
# Opt-in health probes
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def only_collectors(monkeypatch):
    """Replace the registered collectors with the given ones.

    The registry is swapped, never ``snapadmin.diagnostics.collect`` itself:
    ``snapadmin_info`` binds ``collect`` when its module is first imported, so a
    patched function would stay inside the command for every later test.
    """
    from snapadmin.diagnostics import registry

    registry.load_collectors()

    def install(*collectors):
        monkeypatch.setattr(registry, "_REGISTRY", {collector.name: collector for collector in collectors})

    return install


def _collector(name, *, ok=True, health_probe=True, calls=None):
    from snapadmin.diagnostics.registry import Collector

    def run(**kwargs):
        if calls is not None:
            calls.append((name, kwargs))
        return {"ok": ok}

    return Collector(name=name, title=name, icon="", order=1, health_probe=health_probe, fn=run)


@pytest.mark.django_db
def test_probes_run_only_when_asked(only_collectors, settings):
    calls = []
    only_collectors(_collector("database", calls=calls),
                    _collector("inventory", health_probe=False, calls=calls))

    settings.SNAPADMIN_STARTUP_REPORT_PROBES = False
    without = startup.startup_facts(probes=startup.probes_requested())
    settings.SNAPADMIN_STARTUP_REPORT_PROBES = True
    with_probes = startup.startup_facts(probes=startup.probes_requested())

    assert without["health"] is None
    assert with_probes["health"] == {"database": True}
    assert calls == [("database", {"verbose": False})]


@pytest.mark.django_db
def test_probe_results_are_one_line(only_collectors):
    only_collectors(_collector("database"), _collector("elasticsearch", ok=False))

    text = startup.render_startup(startup.startup_facts(probes=True))

    assert "Health: database ✓ · elasticsearch ✗" in text
    assert "health probes skipped" not in text


# ─────────────────────────────────────────────────────────────────────────────
# Emitting: where, how often, and never fatally
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_process(monkeypatch):
    """Each test is a new process as far as the once-per-process guard goes, and
    the decision is forced on, since this suite itself runs under pytest."""
    monkeypatch.setattr(startup, "_emitted", False)
    monkeypatch.setattr(startup, "_should_emit", lambda *args, **kwargs: True)


class _Terminal(StringIO):
    def isatty(self):
        return True


@pytest.mark.django_db
def test_the_block_goes_to_stderr_once_per_process(fresh_process, settings):
    settings.DEBUG = True
    stream = _Terminal()

    startup.emit_startup_report(stream=stream)
    startup.emit_startup_report(stream=stream)

    assert stream.getvalue().count("SnapAdmin ") == 1
    assert stream.getvalue() == startup.startup_report() + "\n"


@pytest.mark.django_db
def test_without_a_terminal_and_without_debug_it_is_one_json_line_on_stderr(
    fresh_process, settings, capsys
):
    settings.DEBUG = False
    stream = io.StringIO()  # not a terminal: a log file, a container's captured stderr

    startup.emit_startup_report(stream=stream)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert (record["event"], record["level"]) == ("snapadmin.startup", "info")
    assert "audit_trail" in record["on"]
    assert record["version"] == startup.startup_facts()["version"]
    # Regression: this went through structlog, which SnapAdmin's logging sends
    # to stdout — into the output of whatever command was being piped.
    assert capsys.readouterr().out == ""


@pytest.mark.django_db
def test_a_failing_report_never_stops_the_boot(fresh_process, monkeypatch):
    warnings = []
    monkeypatch.setattr(startup, "startup_facts", lambda **kwargs: 1 / 0)
    monkeypatch.setattr(startup.logger, "warning", lambda event, **fields: warnings.append(event))

    startup.emit_startup_report(stream=_Terminal())

    assert warnings == ["snapadmin.startup.report_failed"]


def test_nothing_is_emitted_when_the_decision_says_no(monkeypatch, settings):
    monkeypatch.setattr(startup, "_emitted", False)
    settings.SNAPADMIN_STARTUP_REPORT = "auto"
    stream = _Terminal()

    startup.emit_startup_report(stream=stream)  # this is pytest: never

    assert stream.getvalue() == ""


def test_ready_emits_the_report(monkeypatch):
    from django.apps import apps

    calls = []
    monkeypatch.setattr(startup, "emit_startup_report", lambda: calls.append(True))

    apps.get_app_config("snapadmin").ready()

    assert calls == [True]


# ─────────────────────────────────────────────────────────────────────────────
# One implementation, two entry points
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_snapadmin_info_startup_prints_the_same_block():
    output = StringIO()

    call_command("snapadmin_info", "--startup", stdout=output)

    assert output.getvalue() == startup.startup_report() + "\n"


@pytest.mark.parametrize(
    "extra",
    [("--json",), ("--brief",), ("--health-check",), ("--section", "features"), ("--verbose",)],
)
def test_snapadmin_info_startup_refuses_flags_it_would_ignore(extra):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="--startup cannot be combined with " + extra[0]):
        call_command("snapadmin_info", "--startup", *extra, stdout=StringIO())


@pytest.mark.django_db
@override_settings(SNAPADMIN_STARTUP_REPORT_PROBES=True)
def test_snapadmin_info_startup_honours_the_probe_setting(only_collectors):
    only_collectors(_collector("database"))
    output = StringIO()

    call_command("snapadmin_info", "--startup", stdout=output)

    assert "  Health: database ✓\n" in output.getvalue()
    assert "health probes skipped" not in output.getvalue()

