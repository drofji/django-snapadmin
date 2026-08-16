"""
tests/test_celery_optional.py — ``snapadmin.tasks`` must import without Celery (#FIX1).

Celery is an optional extra (``pip install django-snapadmin[celery]``), but
``snapadmin/tasks.py`` used to open with a bare ``from celery import shared_task``, so
importing it on a base install raised ``ModuleNotFoundError`` — the same coupling the
REST/GraphQL stacks lost in #DEP1. The module now falls back to a stand-in decorator.

The stand-in deliberately splits two behaviours:

* **calling** a task runs it synchronously — the body is ordinary Python, and that is what
  ``CELERY_TASK_ALWAYS_EAGER`` does anyway;
* **scheduling** it (``delay`` / ``apply_async``) raises ``ImproperlyConfigured``. A silent
  no-op here would turn a missing dependency into background work that never happens, which
  is a worse failure than the import error it replaces.

Import-time behaviour is exercised by executing the module fresh with ``celery`` hidden, the
technique ``test_api_optional.py`` uses for DRF — the branch is chosen while the module runs,
so a monkeypatched flag could not reach it.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured


class _HideModules:
    """Make ``celery`` unimportable and evict the modules that cached it."""

    def __init__(self, *names: str) -> None:
        self.names = names

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.names:
            raise ImportError(f"hidden by test: {name}")
        return None

    def _is_evicted(self, module_name: str) -> bool:
        return module_name.split(".")[0] in self.names or module_name == "snapadmin.tasks"

    def __enter__(self):
        sys.meta_path.insert(0, self)
        self._saved = {k: v for k, v in sys.modules.items() if self._is_evicted(k)}
        for key in self._saved:
            del sys.modules[key]
        return self

    def __exit__(self, *exc):
        sys.meta_path.remove(self)
        sys.modules.update(self._saved)
        return False


def _tasks_without_celery():
    """Execute ``snapadmin/tasks.py`` fresh with Celery hidden."""
    source = pathlib.Path(importlib.import_module("snapadmin.tasks").__file__)
    with _HideModules("celery"):
        spec = importlib.util.spec_from_file_location("snapadmin_tasks_no_celery", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


TASK_NAMES = {
    "purge_expired_tokens": "snapadmin.purge_expired_tokens",
    "purge_expired_data": "snapadmin.purge_expired_data",
    "send_error_digest": "snapadmin.send_error_digest",
    "run_export": "snapadmin.run_export",
    "run_es_reindex": "snapadmin.run_es_reindex",
    "send_health_alert": "snapadmin.send_health_alert",
    "run_db_backups": "snapadmin.run_db_backups",
}


class TestTasksImportWithoutCelery:
    def test_module_executes_with_celery_hidden(self):
        module = _tasks_without_celery()
        assert module is not None

    @pytest.mark.parametrize("attr", sorted(TASK_NAMES))
    def test_every_task_still_exists(self, attr):
        module = _tasks_without_celery()
        assert callable(getattr(module, attr))

    @pytest.mark.parametrize("attr,name", sorted(TASK_NAMES.items()))
    def test_task_names_are_preserved(self, attr, name):
        """A Beat schedule refers to tasks by name — the fallback keeps the same ones."""
        module = _tasks_without_celery()
        assert getattr(module, attr).name == name

    @pytest.mark.parametrize("attr", sorted(TASK_NAMES))
    def test_scheduling_raises_an_actionable_error(self, attr):
        module = _tasks_without_celery()
        task = getattr(module, attr)
        for call in (task.delay, task.apply_async):
            with pytest.raises(ImproperlyConfigured) as exc:
                call()
            assert "django-snapadmin[celery]" in str(exc.value)

    def test_calling_a_task_runs_it_synchronously(self, db):
        """The body is ordinary Python; without a broker, running it in-process is the honest fallback."""
        module = _tasks_without_celery()
        result = module.purge_expired_tokens()
        assert result["deleted"] == 0


class TestCompatShim:
    def test_bare_decorator_form(self):
        from snapadmin.celery_compat import shared_task

        @shared_task
        def work():
            return "done"

        assert work() == "done"
        assert work.name == "work"

    def test_bind_passes_the_task_as_self(self):
        from snapadmin.celery_compat import shared_task

        @shared_task(bind=True, name="snapadmin.test_bound")
        def work(self):
            return self.name

        assert work() == "snapadmin.test_bound"

    def test_arguments_are_forwarded(self):
        from snapadmin.celery_compat import shared_task

        @shared_task(name="snapadmin.test_args")
        def work(a, b=1):
            return a + b

        assert work(2, b=3) == 5

    def test_retry_is_not_silently_swallowed(self):
        """``self.retry()`` has no meaning without a worker — say so rather than pretend."""
        from snapadmin.celery_compat import shared_task

        @shared_task(bind=True, name="snapadmin.test_retry")
        def work(self):
            self.retry()

        with pytest.raises(ImproperlyConfigured):
            work()

    def test_docstring_and_name_survive_the_decorator(self):
        from snapadmin.celery_compat import shared_task

        @shared_task(name="snapadmin.test_meta")
        def work():
            """Well documented."""

        assert work.__doc__ == "Well documented."
        assert work.__name__ == "work"


class TestRealCeleryIsStillUsedWhenInstalled:
    def test_tasks_are_celery_tasks_in_this_environment(self):
        """Celery is installed here, so the shim must stay out of the way."""
        from celery.app.task import Task

        import snapadmin.tasks as tasks

        assert isinstance(tasks.purge_expired_tokens, Task)
