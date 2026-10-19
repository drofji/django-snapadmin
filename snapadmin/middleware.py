"""
snapadmin/middleware.py

Opt-in error monitoring middleware.

Add to MIDDLEWARE to activate (any position after CommonMiddleware works):

    MIDDLEWARE = [
        ...
        "snapadmin.middleware.SnapErrorMonitorMiddleware",
    ]

Every unhandled exception and every 5xx response is recorded as an
``ErrorEvent``; spike alerts and the daily digest are documented in
:mod:`snapadmin.monitoring`. ``SNAPADMIN_ERROR_MONITOR_ENABLED = False``
turns recording off without editing MIDDLEWARE.
"""

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.http import HttpRequest, HttpResponse

from snapadmin.monitoring import record_error


class SnapErrorMonitorMiddleware:
    """Records unhandled exceptions and 5xx responses into the error monitor.

    Add it to ``MIDDLEWARE`` to feed the spike alerts, the daily digest and the
    admin's error log::

        MIDDLEWARE = [
            ...,
            "snapadmin.middleware.SnapErrorMonitorMiddleware",
        ]

    It only observes: ``process_exception`` records the exception and returns
    ``None``, leaving Django's own error handling untouched. A 5xx returned
    without raising is recorded too, and a flag keeps the two paths from counting
    the same failure twice.

    Sync and async capable (#EXT3m): around an async view chain it is a
    coroutine function and records through ``sync_to_async``, so ASGI keeps the
    chain async instead of switching it to sync mode for this one middleware.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest):
        if iscoroutinefunction(self):
            return self.__acall__(request)
        response = self.get_response(request)
        if self._unrecorded_5xx(request, response):
            record_error(request=request, status_code=response.status_code)
        return response

    async def __acall__(self, request: HttpRequest) -> HttpResponse:
        response = await self.get_response(request)
        if self._unrecorded_5xx(request, response):
            await sync_to_async(record_error, thread_sensitive=True)(
                request=request, status_code=response.status_code
            )
        return response

    @staticmethod
    def _unrecorded_5xx(request: HttpRequest, response: HttpResponse) -> bool:
        # 5xx responses produced without an exception (e.g. a view returning
        # HttpResponseServerError directly). Exceptions were already recorded
        # in process_exception — the flag prevents double counting.
        return response.status_code >= 500 and not getattr(
            request, "_snapadmin_error_recorded", False
        )

    def process_exception(self, request: HttpRequest, exception: Exception) -> None:
        # A marker read back in process_response, set on the request the way
        # Django's own middleware set their own attributes.
        request._snapadmin_error_recorded = True  # type: ignore[attr-defined]
        record_error(request=request, exception=exception)
        return None
