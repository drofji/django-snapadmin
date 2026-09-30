"""
The startup report: what SnapAdmin has switched on, said once when a server starts (#DX1).

A capability that is installed but switched off is invisible — nobody runs a
diagnostics command for a feature they have forgotten exists. So when a
development server starts, SnapAdmin prints one short block: what is on, what
is off, what is switched on but missing its optional package, whether the
installed dependencies allow commercial use, and how to see more or silence it.

``SNAPADMIN_STARTUP_REPORT`` decides when:

* ``"auto"`` (default) — under ``DEBUG``, for a development server
  (``runserver`` and its common variants) only: where a developer is watching
  the console, and nowhere else — not a production worker, not every
  ``migrate`` of a dev box.
* ``True`` — every process (a staging box whose operator wants the facts in the
  logs), except commands whose output a program reads.
* ``False`` — never.

What it is built on is the ``features`` collector that ``snapadmin_info``
renders, run in its configuration-only mode: no query, no connection, no key
resolution, because this runs inside ``django.setup()``. Health probes are
opt-in (``SNAPADMIN_STARTUP_REPORT_PROBES = True``), and the block says when
they were skipped, so it is never mistaken for a health check.
``manage.py snapadmin_info --startup`` prints the identical block.

It goes to **stderr** — never stdout, which a command's caller may be parsing —
as text when ``DEBUG`` is on or stderr is a terminal, and otherwise as one JSON
line, ``{"event": "snapadmin.startup", …}``, carrying the same facts. It holds
booleans, extra names and package names only: never a key, a DSN, a URL, a
fingerprint or a token.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import warnings
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence
from typing import IO, Literal

from snapadmin.diagnostics.render import _humanise, _wrap_names
from snapadmin.logging_config import get_logger

logger = get_logger("snapadmin.startup")

Mode = Literal["auto"] | bool

#: The development servers ``"auto"`` prints for.
_DEVELOPMENT_SERVERS = frozenset({"runserver", "runserver_plus", "runsslserver"})

#: Commands whose output a program reads, or that already report the same facts:
#: silent even under ``True``.
_QUIET_COMMANDS = frozenset(
    {
        "snapadmin_info",
        "dumpdata",
        "shell",
        "shell_plus",
        "dbshell",
        "diffsettings",
        "sqlmigrate",
        "sqlflush",
        "sqlsequencereset",
        "inspectdb",
        "test",
        "help",
        "version",
    }
)

_WRAP_WIDTH = 96

#: Set once this process has reported (or decided not to); ``ready()`` can run
#: more than once in a process, the report must not.
_emitted = False


# ─────────────────────────────────────────────────────────────────────────────
# Deciding
# ─────────────────────────────────────────────────────────────────────────────


def report_mode() -> Mode:
    """``SNAPADMIN_STARTUP_REPORT``, resolved: ``"auto"``, ``True`` or ``False``.

    Any other value is treated as ``"auto"``; ``snapadmin.W027`` names it.
    """
    from snapadmin.conf import get_setting

    value = get_setting("SNAPADMIN_STARTUP_REPORT", "auto")
    if value is True or value is False or value == "auto":
        return value
    return "auto"


def probes_requested() -> bool:
    """``SNAPADMIN_STARTUP_REPORT_PROBES`` — run the health probes at startup too."""
    from snapadmin.conf import get_setting

    return get_setting("SNAPADMIN_STARTUP_REPORT_PROBES", False) is True


def _command(argv: Sequence[str]) -> str | None:
    """The management command in ``argv``, or ``None`` for any other entry point."""
    if len(argv) < 2:
        return None
    program = os.path.basename(argv[0])
    # ``python -m django …`` leaves the path of django/__main__.py in argv[0].
    run_as_module = (
        program == "__main__.py" and os.path.basename(os.path.dirname(argv[0])) == "django"
    )
    if program.endswith("manage.py") or program.startswith("django-admin") or run_as_module:
        return argv[1]
    return None


def _should_emit(
    mode: Mode,
    *,
    debug: bool,
    argv: Sequence[str],
    environ: Mapping[str, str],
    under_pytest: bool,
) -> bool:
    """Whether this process prints the report. Pure, so every rule is testable."""
    if mode is False or under_pytest or "PYTEST_CURRENT_TEST" in environ:
        return False
    if "DJANGO_AUTO_COMPLETE" in environ or "--json" in argv:
        return False
    command = _command(argv)
    if command in _QUIET_COMMANDS:
        return False
    if command in _DEVELOPMENT_SERVERS and "--noreload" not in argv:
        # The autoreloader's parent only watches files; the child it starts is
        # the process that serves requests, and reports once. Django marks it
        # RUN_MAIN; runserver_plus reloads through Werkzeug, WERKZEUG_RUN_MAIN.
        if "true" not in (environ.get("RUN_MAIN"), environ.get("WERKZEUG_RUN_MAIN")):
            return False
    if mode == "auto":
        return debug and command in _DEVELOPMENT_SERVERS
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Collecting and rendering
# ─────────────────────────────────────────────────────────────────────────────


def startup_facts(*, probes: bool = False) -> dict:
    """The facts the block states, as JSON-clean data (the JSON-line form).

    Built on the ``features`` collector's configuration-only mode. ``probes``
    additionally runs the health-probe collectors — a connection each.
    """
    import django

    from snapadmin import __version__, licensing
    from snapadmin.diagnostics.features import MISCONFIGURED, _capabilities, needs_extra

    caps = _capabilities(live=False)
    missing = needs_extra(caps)
    facts: dict = {
        "version": __version__,
        "django": django.get_version(),
        "python": platform.python_version(),
        "on": [key for key, enabled, _ in caps if enabled and key not in missing],
        "off": [
            key for key, enabled, detail in caps if enabled is False and detail != MISCONFIGURED
        ],
        "misconfigured": [
            key for key, enabled, detail in caps if enabled is False and detail == MISCONFIGURED
        ],
        "needs_extra": missing,
        "not_checked": {key: detail for key, enabled, detail in caps if enabled is None},
        "licences": licensing.commercial_verdict(licensing.scan_curated()),
        "health": None,
    }
    if probes:
        import snapadmin.diagnostics as diagnostics

        with warnings.catch_warnings():
            # Asked for explicitly: the probes query the database inside
            # django.setup(), which Django rightly warns about by default.
            warnings.filterwarnings(
                "ignore", message="Accessing the database during app initialization"
            )
            results = diagnostics.collect(health_only=True, verbose=False)
        facts["health"] = {collector.name: data.get("ok") is True for collector, data in results}
    return facts


def _wrapped(prefix: str, names: list[str]) -> list[str]:
    return _wrap_names(prefix, names, width=_WRAP_WIDTH)


def render_startup(facts: Mapping) -> str:
    """The block, as text. The single renderer for both entry points."""
    lines = [f"SnapAdmin {facts['version']} · Django {facts['django']} · Python {facts['python']}"]
    if facts["on"]:
        lines += _wrapped("  ✓ on           ", [_humanise(key) for key in facts["on"]])
    if facts["off"]:
        lines += _wrapped("  ✗ off          ", [_humanise(key) for key in facts["off"]])
    for key in facts["misconfigured"]:
        lines.append(f"  ⚠ misconfigured {_humanise(key)} — run manage.py check")
    for key, extra in facts["needs_extra"].items():
        lines.append(
            f"  ⚠ needs extra  {_humanise(key)} is switched on but not installed — "
            f"pip install 'django-snapadmin[{extra}]'"
        )
    for key, reason in facts["not_checked"].items():
        lines.append(f"  ? not checked  {_humanise(key)} ({reason})")
    concerns = facts["licences"]["concerns"]
    if concerns:
        lines.append(f"  Licences: review {', '.join(concerns)} (snapadmin_license_check)")
    else:
        lines.append("  Licences: every installed dependency allows commercial use")
    health = facts["health"]
    if health is None:
        lines.append(
            "  Configuration only — health probes skipped "
            "(SNAPADMIN_STARTUP_REPORT_PROBES = True runs them)."
        )
    else:
        lines.append(
            "  Health: " + " · ".join(f"{name} {'✓' if ok else '✗'}" for name, ok in health.items())
        )
    lines.append("  More: manage.py snapadmin_info · silence: SNAPADMIN_STARTUP_REPORT = False")
    return "\n".join(lines)


def startup_report(*, probes: bool = False) -> str:
    """The startup block as text — what ``runserver`` prints and
    ``snapadmin_info --startup`` shows."""
    return render_startup(startup_facts(probes=probes))


# ─────────────────────────────────────────────────────────────────────────────
# Emitting
# ─────────────────────────────────────────────────────────────────────────────


def emit_startup_report(*, stream: IO[str] | None = None) -> None:
    """Report once for this process if the setting and the process call for it.

    Called from ``SnapAdminConfig.ready()``. Never raises: a report that cannot
    be built is logged and the boot carries on.
    """
    global _emitted
    if _emitted:
        return
    _emitted = True
    try:
        from django.conf import settings

        debug = bool(getattr(settings, "DEBUG", False))
        if not _should_emit(
            report_mode(),
            debug=debug,
            argv=sys.argv,
            environ=os.environ,
            under_pytest="_pytest" in sys.modules,
        ):
            return
        facts = startup_facts(probes=probes_requested())
        target = stream if stream is not None else sys.stderr
        if debug or target.isatty():
            target.write(render_startup(facts) + "\n")
        else:
            # One JSON line on the same stream, not a structlog event: SnapAdmin's
            # logging writes to stdout, and stdout may be a piped command's output.
            record = {
                "event": "snapadmin.startup",
                "level": "info",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **facts,
            }
            target.write(json.dumps(record) + "\n")
        target.flush()
    except Exception as exc:  # the report is advice; the boot is not optional
        from snapadmin.diagnostics.registry import _describe

        # _describe strips credentials: a settings parser's error may quote a DSN.
        logger.warning("snapadmin.startup.report_failed", error=_describe(exc))
