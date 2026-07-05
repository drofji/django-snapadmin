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

from django.http import HttpRequest, HttpResponse

from snapadmin.monitoring import record_error


class SnapErrorMonitorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        # 5xx responses produced without an exception (e.g. a view returning
        # HttpResponseServerError directly). Exceptions were already recorded
        # in process_exception — the flag prevents double counting.
        if response.status_code >= 500 and not getattr(
            request, "_snapadmin_error_recorded", False
        ):
            record_error(request=request, status_code=response.status_code)
        return response

    def process_exception(self, request: HttpRequest, exception: Exception) -> None:
        request._snapadmin_error_recorded = True
        record_error(request=request, exception=exception)
        return None
