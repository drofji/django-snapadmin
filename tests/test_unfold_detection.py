"""
tests/test_unfold_detection.py — the stock-admin fallback is never silent (#EXT3j).

Each admin-facing module caught ``ImportError`` around its Unfold imports and fell
back without a word. A custom user model that imported SnapAdmin produced an
import cycle, Unfold half-loaded, and the admin lost its theme with nothing in the
log; two modules could even decide differently. Tests covered "Unfold absent" and
"Unfold present", never "present but the import fails".

The class guard is an ``ast`` scan: every ``except ImportError`` handler in the
package re-raises, logs, or sits in a frozen allowlist with the reason it may be
silent.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from structlog.testing import capture_logs

from snapadmin import unfold_compat
from snapadmin.checks import check_unfold_theme

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "snapadmin"

#: Handlers allowed to stay silent — "<path>:<enclosing function>" -> why.
SILENT_BY_DESIGN = {
    "api/exports.py:create": "answers 503 naming the [celery] extra — the caller is told",
    "api/reindex.py:post": "answers 503 naming the [celery] extra — the caller is told",
    "checks.py:_schedule_period_hours": "celery is the optional [celery] extra; no schedule to judge",
    "checks.py:check_snap_action_read_only_conflict": "urls.py raises the pointed error for missing API deps",
    "checks.py:check_sharding_strategy": "reported as snapadmin.E014 with the import error",
    "checks.py:_extra_settings_in_play": "Django's own app loading fails first for a listed-but-broken app",
    "diagnostics/runtime.py:collect": "the Celery section reports enabled: False — that is the report",
    "extra_settings_admin.py:apply_unfold_styling": "Django's own app loading fails first for a listed-but-broken app",
    "scaffold/validate.py:validate_identifier_name": "find_spec raising is no evidence of a name conflict",
    "tasks.py:<module>": "falls back to celery_compat, which refuses to queue loudly",
}


def _silent_import_error_handlers() -> set[str]:
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ExceptHandler) and node.type is not None):
                continue
            caught = {n.id for n in ast.walk(node.type) if isinstance(n, ast.Name)}
            if not caught & {"ImportError", "ModuleNotFoundError"}:
                continue
            raises = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            logs = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "logger"
                for n in ast.walk(node)
            )
            if raises or logs:
                continue
            scope, parent = "<module>", parents.get(node)
            while parent is not None:
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scope = parent.name
                    break
                parent = parents.get(parent)
            found.add(f"{path.relative_to(PACKAGE).as_posix()}:{scope}")
    return found


class TestNoSilentImportFallback:
    def test_every_silent_import_error_handler_is_allowed_by_name(self):
        silent = _silent_import_error_handlers()

        unexplained = sorted(silent - set(SILENT_BY_DESIGN))
        stale = sorted(set(SILENT_BY_DESIGN) - silent)

        assert len(silent) >= 5, "the scan found too few handlers — it is not looking"
        assert not unexplained, (
            f"review: {unexplained} catch ImportError and carry on without raising or logging — "
            "may this fallback really be silent? Log it, raise, or add it to SILENT_BY_DESIGN "
            "with the reason"
        )
        assert not stale, f"review: SILENT_BY_DESIGN names handlers that are gone: {stale}"


@pytest.fixture
def fresh_decision(monkeypatch):
    monkeypatch.setattr(unfold_compat, "_decision", None)


class TestTheDecision:
    def test_a_listed_unfold_that_fails_to_import_is_logged_and_warned(self, fresh_decision, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def failing(name, *args, **kwargs):
            if name == "unfold.admin":
                raise ImportError("cannot import name 'ModelAdmin' from partially initialized module 'unfold.admin'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failing)
        with capture_logs() as logs:
            active, reason = unfold_compat.detect_unfold()
        monkeypatch.setattr(builtins, "__import__", real_import)

        assert active is False
        assert reason.startswith("import failed: cannot import name 'ModelAdmin'")
        assert [entry["event"] for entry in logs] == ["snapadmin.unfold_import_failed"]
        messages = check_unfold_theme(None)
        assert [m.id for m in messages] == ["snapadmin.W032"]
        assert "partially initialized" in messages[0].msg

    def test_an_unlisted_unfold_is_the_informational_message(self, fresh_decision, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(unfold_compat, "settings", SimpleNamespace(INSTALLED_APPS=["django.contrib.admin"]))

        assert unfold_compat.detect_unfold() == (False, unfold_compat.NOT_INSTALLED)
        assert [m.id for m in check_unfold_theme(None)] == ["snapadmin.I001"]

    def test_the_first_decision_is_kept_so_every_module_agrees(self, fresh_decision, monkeypatch):
        from types import SimpleNamespace

        first = unfold_compat.detect_unfold()
        monkeypatch.setattr(unfold_compat, "settings", SimpleNamespace(INSTALLED_APPS=[]))

        assert unfold_compat.detect_unfold() == first

    def test_the_admin_modules_share_the_process_decision(self):
        import snapadmin.admin as admin_module
        import snapadmin.admin_gen as admin_gen_module

        assert admin_module.UNFOLD_INSTALLED is admin_gen_module.UNFOLD_INSTALLED is unfold_compat.detect_unfold()[0]
