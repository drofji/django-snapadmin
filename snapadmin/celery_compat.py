"""
Keep ``snapadmin.tasks`` importable when Celery is not installed.

Celery is an optional extra (``pip install django-snapadmin[celery]``), yet
``snapadmin/tasks.py`` is a module a project can legitimately import — Celery's own
``autodiscover_tasks()`` finds it, a Beat schedule names its tasks, and application code
sometimes imports one directly. With a bare ``from celery import shared_task`` at the top,
that import blew up on a base install: an optional dependency behaving like a required one,
the same coupling the REST and GraphQL stacks lost in an earlier release.

:func:`shared_task` here is a stand-in used only when the real decorator is unavailable. It
draws a deliberate line:

* **Calling** the task runs it **synchronously** in the current process. Every SnapAdmin task
  body is ordinary Python with no worker-specific API, and running it in-process is exactly
  what ``CELERY_TASK_ALWAYS_EAGER`` does in the test suite.
* **Scheduling** it — ``delay()``, ``apply_async()``, ``retry()`` — raises
  :class:`~django.core.exceptions.ImproperlyConfigured` naming the extra. A no-op would be
  worse than the ``ImportError`` it replaces: the caller would believe work was queued while
  nothing ever ran it, and nothing on the page or in the logs would say otherwise.

Task ``name``s are preserved, so a Beat schedule or documentation written against
``snapadmin.purge_expired_data`` keeps describing the same task once Celery is installed.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from django.core.exceptions import ImproperlyConfigured

#: Shown whenever something tries to reach the broker without Celery installed.
INSTALL_HINT = (
    "Celery is not installed, so this SnapAdmin task cannot be queued. "
    "Install it with `pip install django-snapadmin[celery]` and configure a broker "
    "(CELERY_BROKER_URL), or call the task directly to run it in-process."
)


class UnavailableTask:
    """A callable stand-in for a Celery task, with the broker half disabled."""

    def __init__(self, fn: Callable[..., Any], *, name: str, bind: bool = False) -> None:
        self.run = fn
        self.name = name
        self._bind = bind
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Run the task body here and now, passing ``self`` first for ``bind=True``."""
        if self._bind:
            return self.run(self, *args, **kwargs)
        return self.run(*args, **kwargs)

    def delay(self, *args: Any, **kwargs: Any):
        raise ImproperlyConfigured(INSTALL_HINT)

    def apply_async(self, *args: Any, **kwargs: Any):
        raise ImproperlyConfigured(INSTALL_HINT)

    def retry(self, *args: Any, **kwargs: Any):
        """Retrying is a worker feature — there is nothing here to hand the task back to."""
        raise ImproperlyConfigured(INSTALL_HINT)


def shared_task(*args: Any, **kwargs: Any):
    """Stand in for ``celery.shared_task``, in both the bare and the called form.

    Accepts the same shapes SnapAdmin uses — ``@shared_task`` and
    ``@shared_task(bind=True, name="…")`` — and ignores worker-only options such as
    ``acks_late``, which describe delivery semantics that only exist with a broker.
    """
    if len(args) == 1 and not kwargs and callable(args[0]):
        fn = args[0]
        return UnavailableTask(fn, name=fn.__name__)

    def decorator(fn: Callable[..., Any]) -> UnavailableTask:
        return UnavailableTask(fn, name=kwargs.get("name", fn.__name__), bind=bool(kwargs.get("bind")))

    return decorator
